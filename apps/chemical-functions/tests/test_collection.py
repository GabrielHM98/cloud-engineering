from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import collect_data as c

POLICY = c.policy_load()
KEY = 'unit-test-credential'


def response(sid, smiles='CCO', key='LFQSCWFLJHTTHZ-UHFFFAOYSA-N'):
    return {'dtxsid': sid, 'smiles': smiles, 'inchikey': key, 'preferredName': 'Ethanol',
            'dtxcid': 'DTXCID1', 'casrn': '64-17-5', 'qcLevel': 1, 'qcNotes': None}


def bytes_json(value):
    return json.dumps(value).encode()


def csv_bytes(rows):
    stream = io.StringIO(newline='')
    writer = c.csv.DictWriter(stream, fieldnames=c.CSV_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def export_row(sid, smiles='CCO'):
    item = dict.fromkeys(c.CSV_FIELDS, '')
    item.update(INPUT=sid, DTXSID=sid, SMILES=smiles, INCHIKEY='LFQSCWFLJHTTHZ-UHFFFAOYSA-N', QC_LEVEL='1')
    return item


class CollectionTests(unittest.TestCase):
    def test_adapter_keeps_original_smiles_nulls_and_duplicates_without_using_ready_variants(self):
        raw = [dict(response('DTXSID1'), qsarReadySmiles='wrong', msReadySmiles='wrong'), response('DTXSID1'), response('DTXSID2', None, None)]
        rows, missing = c.adapt_ctx(bytes_json(raw), ['DTXSID1', 'DTXSID2'], 'raw-sha')
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['SMILES'], 'CCO')
        self.assertEqual(rows[-1]['SMILES'], '')
        self.assertEqual(rows[0]['QC_LEVEL'], '1')
        self.assertFalse(missing)

    def test_schema_drift_unrequested_ids_and_nested_fields_fail(self):
        for payload in [{'content': [response('DTXSID1')]}, [{'dtxsid': 'DTXSID1', 'msReadySmiles': 'CCO', 'inchikey': 'key'}],
                        [response('DTXSID999')], [dict(response('DTXSID1'), smiles=['CCO'])]]:
            with self.assertRaises(ValueError):
                c.adapt_ctx(bytes_json(payload), ['DTXSID1'], 'sha')
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON'):
            c.adapt_ctx(b'[{"dtxsid":"DTXSID1","dtxsid":"DTXSID2","smiles":null,"inchikey":null}]', ['DTXSID1'], 'sha')

    def test_missing_ids_are_reported_without_fabricated_source_rows(self):
        rows, _ = c.adapt_ctx(bytes_json([response('DTXSID1')]), ['DTXSID1', 'DTXSID2'], 'sha')
        result = c.coverage(rows, ['DTXSID1', 'DTXSID2'])
        self.assertEqual(result['export_rows'], 1)
        self.assertFalse(result['mapping_coverage_complete'])
        self.assertEqual(result['missing_requested_input_ids'], ['DTXSID2'])

    def test_overlapping_csv_exports_keep_source_rows_and_replay_identically(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            root = Path(folder)
            a, b = root/'first.csv', root/'second.csv'
            a.write_bytes(csv_bytes([export_row('DTXSID1')]))
            b.write_bytes(csv_bytes([export_row('DTXSID1'), export_row('DTXSID2', 'CCC')]))
            store = root/'store'
            first = c.archive_csvs(store, 'csv-v1', [a, b], ['DTXSID1', 'DTXSID2'], 'https://comptox.epa.gov/dashboard/batch-search')
            generated = (store/'runs/csv-v1/structures.csv').read_bytes()
            second = c.archive_csvs(store, 'csv-v1', [b, a], ['DTXSID1', 'DTXSID2'], 'https://comptox.epa.gov/dashboard/batch-search')
            self.assertEqual(first, second)
            self.assertEqual(generated, (store/'runs/csv-v1/structures.csv').read_bytes())
            self.assertEqual(first['export_rows'], 3)
            self.assertEqual((store/'runs/csv-v1/requested_ids.txt').read_text(), 'DTXSID1\nDTXSID2\n')
            self.assertEqual(first['repeated_input_records'], 1)
            self.assertTrue(first['mapping_coverage_complete'])
            self.assertIsNone(first['raw_objects'][0]['source_retrieved_at'])

    def test_api_batches_resume_without_refetching_completed_requests_or_logging_key(self):
        policy = dict(POLICY, batch_size=1, minimum_request_interval_seconds=0, maximum_attempts_per_batch=1)
        calls = []
        def first_sender(request, timeout, max_bytes):
            ids = json.loads(request.data)
            calls.append(ids)
            self.assertEqual(request.get_method(), 'POST')
            self.assertEqual(request.get_header('X-api-key'), KEY)
            self.assertIn('projection=chemicaldetailall', request.full_url)
            if ids == ['DTXSID2']:
                raise urllib.error.URLError('network down')
            return bytes_json([response(ids[0])])
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            store = Path(folder)
            with self.assertRaisesRegex(ValueError, 'retries exhausted'):
                c.collect_ctx(store, 'api-v1', ['DTXSID1', 'DTXSID2'], policy, KEY, first_sender, lambda _: None)
            resumed_calls = []
            def resumed_sender(request, *args):
                ids = json.loads(request.data)
                resumed_calls.append(ids)
                return bytes_json([response(ids[0])])
            result = c.collect_ctx(store, 'api-v1', ['DTXSID1', 'DTXSID2'], policy, KEY, resumed_sender, lambda _: None)
            self.assertEqual(resumed_calls, [['DTXSID2']])
            self.assertEqual(result['captured_batches'], 2)
            def never(*args):
                raise AssertionError('completed run must replay offline')
            self.assertEqual(result, c.collect_ctx(store, 'api-v1', ['DTXSID1', 'DTXSID2'], policy, None, never, lambda _: None))
            for path in store.rglob('*'):
                if path.is_file():
                    self.assertNotIn(KEY.encode(), path.read_bytes())

    def test_changed_expected_ids_or_policy_cannot_reuse_a_run_id(self):
        policy = dict(POLICY, minimum_request_interval_seconds=0)
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            store = Path(folder)
            sender = lambda *args: bytes_json([response('DTXSID1')])
            c.collect_ctx(store, 'fixed', ['DTXSID1'], policy, KEY, sender, lambda _: None)
            with self.assertRaisesRegex(ValueError, 'Immutable'):
                c.collect_ctx(store, 'fixed', ['DTXSID2'], policy, KEY, sender, lambda _: None)
            with self.assertRaisesRegex(ValueError, 'Immutable'):
                c.collect_ctx(store, 'fixed', ['DTXSID1'], dict(policy, batch_size=1), KEY, sender, lambda _: None)

    def test_success_body_is_captured_before_schema_validation(self):
        body = b'{"unexpected":"shape"}'
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            store = Path(folder)
            with self.assertRaisesRegex(ValueError, 'JSON array'):
                c.collect_ctx(store, 'schema-change', ['DTXSID1'], POLICY, KEY, lambda *args: body, lambda _: None)
            self.assertEqual((store/'objects'/c.sha_bytes(body)).read_bytes(), body)
            self.assertTrue((store/'runs/schema-change/receipts/batch-00001.json').exists())
            self.assertFalse((store/'runs/schema-change/manifest.json').exists())

    def test_transient_http_failure_retries_and_respects_retry_after(self):
        sleeps, attempts = [], []
        policy = dict(POLICY, minimum_request_interval_seconds=0)
        def sender(*args):
            attempts.append(1)
            if len(attempts) == 1:
                raise urllib.error.HTTPError(c.ENDPOINT, 429, 'rate limited', {'Retry-After': '4'}, None)
            return bytes_json([response('DTXSID1')])
        with tempfile.TemporaryDirectory() as folder:
            body = c.request_batch(['DTXSID1'], KEY, policy, Path(folder), 1, [float('-inf')], sender, sleeps.append)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(sleeps, [4])
        self.assertIn(b'DTXSID1', body)

    def test_authentication_errors_do_not_retry_or_echo_provider_error_text(self):
        attempts = []
        def sender(*args):
            attempts.append(1)
            raise urllib.error.HTTPError(c.ENDPOINT, 401, KEY, {}, io.BytesIO(KEY.encode()))
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError) as caught:
                c.request_batch(['DTXSID1'], KEY, POLICY, Path(folder), 1, [float('-inf')], sender, lambda _: None)
            self.assertNotIn(KEY, str(caught.exception))
            self.assertNotIn(KEY, (Path(folder)/'events.jsonl').read_text())
        self.assertEqual(len(attempts), 1)

    def test_retry_after_http_date_and_wait_budget_are_respected(self):
        error = urllib.error.HTTPError(c.ENDPOINT, 503, '', {'Retry-After': 'Thu, 01 Jan 1970 00:01:45 GMT'}, None)
        self.assertEqual(c.retry_delay(error, 1, POLICY, now=100), 5)
        error.headers['Retry-After'] = '120'
        with self.assertRaisesRegex(ValueError, 'wait budget'):
            c.retry_delay(error, 1, POLICY)

    def test_redirects_are_not_followed_with_api_credentials(self):
        self.assertIsNone(c.NoRedirect().redirect_request(None, None, 302, 'redirect', {}, 'https://other.example/'))

    def test_bad_credentials_and_echoed_credentials_are_rejected_before_archival(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            with self.assertRaisesRegex(ValueError, 'header format'):
                c.request_batch(['DTXSID1'], KEY+'\n', POLICY, run, 1, [float('-inf')], lambda *args: b'[]', lambda _: None)
            with self.assertRaisesRegex(ValueError, 'refusing to archive'):
                c.request_batch(['DTXSID1'], KEY, POLICY, run, 1, [float('-inf')], lambda *args: KEY.encode(), lambda _: None)

    def test_raw_object_tampering_is_detected_on_replay(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            store = Path(folder)
            result = c.collect_ctx(store, 'tamper', ['DTXSID1'], POLICY, KEY, lambda *args: bytes_json([response('DTXSID1')]), lambda _: None)
            (store/'objects'/result['raw_objects'][0]['raw_sha256']).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                c.collect_ctx(store, 'tamper', ['DTXSID1'], POLICY, None, lambda *args: b'[]', lambda _: None)

    def test_pinned_archive_uses_verified_cache_and_has_stable_capture_receipt(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            root = Path(folder)
            cache = root/'cached.zip'
            cache.write_bytes(b'pinned fixture bytes')
            lock = {'dataset': 'test-fixture-only', 'size_bytes': cache.stat().st_size,
                    'md5': hashlib.md5(cache.read_bytes()).hexdigest(), 'sha256': c.sha_file(cache),
                    'download_url': 'https://example.org/fixture.zip'}
            source_lock = root/'lock.json'
            source_lock.write_text(json.dumps(lock))
            first = c.capture_cpdat(root/'store', 'cpdat-test', cache, source_lock)
            second = c.capture_cpdat(root/'store', 'cpdat-test', cache, source_lock)
            self.assertEqual(first, second)
            self.assertEqual((root/'store/objects'/first['archive_sha256']).read_bytes(), cache.read_bytes())

    def test_run_ids_and_expected_id_lists_cannot_be_ambiguous(self):
        for name in ['../outside', '.', '..', 'a/b', '']:
            with self.assertRaises(ValueError):
                c.safe_run_id(name)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'ids.txt'
            path.write_text('DTXSID1\nDTXSID1\n')
            with self.assertRaisesRegex(ValueError, 'unique'):
                c.load_ids(path)

    def test_partial_mapping_exit_code_is_explicit(self):
        with patch.object(c.sys, 'argv', ['collect_data.py', 'ctx', '--run-id', 'test']), patch.object(c, 'load_ids', return_value=['DTXSID1']), patch.object(c, 'collect_ctx', return_value={'mapping_coverage_complete': False}):
            self.assertEqual(c.main(), 2)
        with patch.object(c.sys, 'argv', ['collect_data.py', 'ctx', '--run-id', 'test', '--allow-incomplete']), patch.object(c, 'load_ids', return_value=['DTXSID1']), patch.object(c, 'collect_ctx', return_value={'mapping_coverage_complete': False}):
            self.assertEqual(c.main(), 0)

    def test_concurrent_writer_is_rejected_and_lock_is_released(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'run.lock'
            with c.run_lock(path):
                with self.assertRaisesRegex(ValueError, 'Another collector'):
                    with c.run_lock(path):
                        self.fail('second writer entered')
            with c.run_lock(path):
                pass

    def test_csv_source_change_after_contract_is_not_silently_published(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            root = Path(folder)
            path = root/'source.csv'
            path.write_bytes(csv_bytes([export_row('DTXSID1')]))
            with patch.object(c, 'store_file', return_value='0'*64):
                with self.assertRaisesRegex(ValueError, 'changed after the collection contract'):
                    c.archive_csvs(root/'store', 'race', [path], ['DTXSID1'], 'https://comptox.epa.gov/dashboard/batch-search')
            self.assertFalse((root/'store/runs/race/manifest.json').exists())

    def test_archive_change_after_validation_is_not_silently_published(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            root = Path(folder)
            source = root/'source.zip'
            source.write_bytes(b'pinned bytes')
            lock = {'dataset': 'fixture', 'size_bytes': source.stat().st_size,
                    'md5': hashlib.md5(source.read_bytes()).hexdigest(), 'sha256': c.sha_file(source),
                    'download_url': 'https://example.org/fixture.zip'}
            lock_path = root/'lock.json'
            lock_path.write_text(json.dumps(lock))
            with patch.object(c, 'store_file', return_value='0'*64):
                with self.assertRaisesRegex(ValueError, 'changed after checksum validation'):
                    c.capture_cpdat(root/'store', 'race', source, lock_path)

    def test_transport_enforces_response_size_limit(self):
        class Response:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, limit):
                return b'x'*limit
        opener = unittest.mock.Mock()
        opener.open.return_value = Response()
        with patch.object(c.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(ValueError, 'maximum size'):
                c.transport(c.urllib.request.Request(c.ENDPOINT), 1, 5)

    def test_missing_key_fails_without_making_network_requests(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            sender = unittest.mock.Mock(side_effect=AssertionError('must not call API'))
            with self.assertRaisesRegex(ValueError, 'needs CTX_API_KEY'):
                c.collect_ctx(Path(folder), 'no-key', ['DTXSID1'], POLICY, None, sender, lambda _: None)
            sender.assert_not_called()

    def test_cpdat_cli_stdout_is_parseable_json_and_progress_is_on_stderr(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache = root/'cache.zip'
            cache.write_bytes(b'fixture')
            lock = {'dataset': 'fixture', 'size_bytes': cache.stat().st_size,
                    'md5': hashlib.md5(cache.read_bytes()).hexdigest(), 'sha256': c.sha_file(cache),
                    'download_url': 'https://example.org/fixture.zip'}
            path = root/'lock.json'
            path.write_text(json.dumps(lock))
            stdout, stderr = io.StringIO(), io.StringIO()
            argv = ['collect_data.py', 'cpdat', '--run-id', 'json-output', '--store', str(root/'store'), '--cache', str(cache), '--source-lock', str(path)]
            with patch.object(c.sys, 'argv', argv), redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(c.main(), 0)
            parsed = json.loads(stdout.getvalue())
            self.assertEqual(parsed['collection_status'], 'complete')
            self.assertEqual(parsed['raw_objects_count'], 1)
            self.assertNotIn('raw_objects', parsed)
            self.assertIn('Using cached archive', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
