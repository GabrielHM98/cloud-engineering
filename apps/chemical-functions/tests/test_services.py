"""HTTP contracts and parity with the frozen benchmark; no new training."""
import asyncio
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx

import benchmark
from services.catalog_api import create_app as catalog_app
from services.predictor_api import create_app as predictor_app
from services.web_api import create_app as web_app
from services.model_runtime import Predictor

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT/'data/catalog/chemicals.sqlite3'
RUN = ROOT/'runs/benchmark-v1'
HAVE_ARTIFACTS = DB.exists() and (RUN/'model_bundle.joblib').exists()


@unittest.skipUnless(HAVE_ARTIFACTS, 'Requires the existing catalog and trained model artifacts.')
class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = TestClient(catalog_app(DB))
        cls.predictor = TestClient(predictor_app(RUN))
        cls.catalog.__enter__()
        cls.predictor.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.predictor.__exit__(None,None,None)
        cls.catalog.__exit__(None,None,None)

    def test_readiness_and_catalog_counts(self):
        self.assertEqual(self.catalog.get('/readyz').status_code,200)
        self.assertEqual(self.predictor.get('/readyz').status_code,200)
        data = self.catalog.get('/v1/catalog/summary').json()
        self.assertEqual(data['counts']['molecule'],8335)
        self.assertFalse(data['model_predictions_stored'])
        self.assertEqual(len(self.predictor.get('/v1/model').json()['labels']),9)

    def test_exact_catalog_structure_has_sources_and_unknown_stays_unknown(self):
        data = self.catalog.get('/v1/catalog/structure',params={'smiles':'CCO'}).json()
        self.assertTrue(data['found'])
        self.assertEqual(data['substances'][0]['dtxsid'],'DTXSID9020584')
        solvent = next(x for x in data['recorded_uses'] if x['name']=='Solvent')
        self.assertGreater(solvent['evidence_records'],0)
        self.assertIn('document_id',solvent['evidence_examples'][0])
        unknown = self.catalog.get('/v1/catalog/structure',params={'smiles':'absent-structure'}).json()
        self.assertFalse(unknown['found'])
        self.assertIn('unknown',unknown['meaning'])

    def test_catalog_search_and_excluded_record(self):
        rows = self.catalog.get('/v1/catalog/search',params={'q':'ethanol','limit':1}).json()['results']
        self.assertEqual(rows[0]['dtxsid'],'DTXSID9020584')
        self.assertEqual(self.catalog.get('/v1/catalog/substances/DTXSID000000000').status_code,404)
        import catalog_db
        with catalog_db.readonly(DB) as conn:
            excluded = conn.execute("SELECT dtxsid FROM substance WHERE validation_status='missing_smiles' LIMIT 1").fetchone()[0]
        result = self.catalog.get('/v1/catalog/substances/'+excluded).json()
        self.assertIsNone(result['canonical_smiles'])
        self.assertEqual(result['recorded_uses'],[])

    def test_http_prediction_matches_existing_cli_numerically_and_preserves_references(self):
        with redirect_stdout(StringIO()):
            expected = benchmark.predict(RUN,'CCO',9)
        actual = self.predictor.post('/v1/predict',json={'smiles':'OCC','top_k':9}).json()
        self.assertEqual(actual['canonical_smiles'],'CCO')
        for got, want in zip(actual['ranked_functions'],expected['ranked_functions'],strict=True):
            self.assertEqual(got['role'],want['role'])
            # Single-thread serving can change the final accumulation bits of
            # tree probabilities relative to the parallel benchmark CLI.
            self.assertAlmostEqual(got['annotation_rank_score'],want['annotation_rank_score'],places=14)
            self.assertEqual(got['reference_chemicals'],want['reference_chemicals'])
        self.assertEqual(actual['nearest_reference_tanimoto'],expected['nearest_reference_tanimoto'])
        self.assertEqual(actual['recorded_selected_roles_for_exact_match'],expected['recorded_selected_roles_for_exact_match'])
        self.assertIn('not a calibrated probability',actual['score_meaning'])

    def test_input_scope_and_resource_bounds(self):
        for payload in [{'smiles':''},{'smiles':'   '},{'smiles':'not-smiles'},
                        {'smiles':'CCO.[Na+]'}, {'smiles':'CCO','top_k':10},
                        {'smiles':'CCO','top_k':True},{'smiles':'C'*257},
                        {'smiles':'CCO','unexpected':True}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.predictor.post('/v1/predict',json=payload).status_code,422)
        response = self.predictor.post('/v1/predict',content=b'x'*9000,headers={'content-type':'application/json'})
        self.assertEqual(response.status_code,413)
        self.assertEqual(self.catalog.get('/v1/catalog/search',params={'q':'x','limit':500}).status_code,422)

    def test_model_is_loaded_once_for_repeated_requests(self):
        with patch('services.model_runtime.joblib.load', side_effect=AssertionError('Must not reload')):
            for _ in range(2):
                self.assertEqual(self.predictor.post('/v1/predict',json={'smiles':'CCO','top_k':1}).status_code,200)

    def test_corrupt_model_is_rejected_before_deserialization(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            shutil.copy(RUN/'run_manifest.json',path/'run_manifest.json')
            (path/'model_bundle.joblib').write_bytes(b'corrupt')
            with patch('services.model_runtime.joblib.load',side_effect=AssertionError('Do not deserialize')):
                with self.assertRaisesRegex(ValueError,'hash mismatch'):
                    Predictor(path)

    def test_wrong_dependency_environment_fails_before_load(self):
        with patch('services.model_runtime.benchmark.dependency_versions',return_value={'wrong':'1'}):
            with self.assertRaisesRegex(ValueError,'dependencies differ'):
                Predictor(RUN)

    def test_missing_database_stops_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(Exception):
                with TestClient(catalog_app(Path(directory)/'missing.sqlite3')):
                    self.fail('Missing database must not become ready.')


class GatewayTests(unittest.TestCase):
    def client(self,handler):
        transport = httpx.MockTransport(handler)
        return TestClient(web_app(httpx.AsyncClient(transport=transport)))

    def test_gateway_coordinates_predictor_then_exact_catalog_lookup(self):
        paths=[]
        def handler(request):
            paths.append(request.url.path)
            if request.url.path=='/v1/predict':
                self.assertEqual(json.loads(request.content)['smiles'],'OCC')
                return httpx.Response(200,json={'canonical_smiles':'CCO','ranked_functions':[]})
            self.assertEqual(request.url.params['smiles'],'CCO')
            return httpx.Response(200,json={'found':True,'recorded_uses':[]})
        with self.client(handler) as client:
            result=client.post('/api/predict',json={'smiles':'OCC'}).json()
        self.assertEqual(paths,['/v1/predict','/v1/catalog/structure'])
        self.assertEqual(result['catalog_lookup']['status'],'available')
        self.assertIn('prediction',result)

    def test_catalog_outage_keeps_prediction_and_explicit_unknown_lookup_status(self):
        def handler(request):
            if request.url.path=='/v1/predict':
                return httpx.Response(200,json={'canonical_smiles':'CCO'})
            raise httpx.ConnectError('offline',request=request)
        with self.client(handler) as client:
            response=client.post('/api/predict',json={'smiles':'CCO'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['catalog_lookup']['status'],'unavailable')
        self.assertNotIn('found',response.json()['catalog_lookup'])

    def test_predictor_failure_does_not_fabricate_scores(self):
        for kind in ['timeout','offline','invalid-json']:
            def handler(request):
                if kind=='timeout':
                    raise httpx.ReadTimeout('slow',request=request)
                if kind=='offline':
                    raise httpx.ConnectError('offline',request=request)
                return httpx.Response(200,text='not-json')
            with self.subTest(kind=kind), self.client(handler) as client:
                response=client.post('/api/predict',json={'smiles':'CCO'})
                self.assertEqual(response.status_code,{'timeout':504,'offline':503,'invalid-json':502}[kind])
                self.assertNotIn('prediction',response.json())

    def test_readiness_requires_both_backends_but_liveness_is_independent(self):
        def handler(request):
            return httpx.Response(503 if request.url.host=='predictor' else 200,json={'status':'ready'})
        with self.client(handler) as client:
            self.assertEqual(client.get('/healthz').status_code,200)
            self.assertEqual(client.get('/readyz').status_code,503)
            self.assertEqual(client.get('/').status_code,200)

    def test_unsupported_input_status_is_preserved(self):
        def handler(request):
            return httpx.Response(422,json={'detail':'Unsupported chemistry.'})
        with self.client(handler) as client:
            response=client.post('/api/predict',json={'smiles':'CCO.[Na+]'})
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()['detail'],'Unsupported chemistry.')


if __name__=='__main__':
    unittest.main()
