"""Load once; reuse the benchmark's frozen chemistry and scoring functions."""
import json
from pathlib import Path
from threading import Lock

import joblib
import numpy as np
from rdkit import Chem, DataStructs
from threadpoolctl import threadpool_limits
import benchmark as benchmark


class UnsupportedQuery(ValueError):
    pass


class Predictor:
    def __init__(self, run_dir):
        run_dir = Path(run_dir)
        self.manifest = json.loads((run_dir/'run_manifest.json').read_text())
        model_path = run_dir/'model_bundle.joblib'
        self.model_sha256 = benchmark.v.digest(model_path)
        if self.model_sha256 != self.manifest['output_sha256']['model_bundle.joblib']:
            raise ValueError('Model artifact hash mismatch.')
        versions = benchmark.dependency_versions()
        if versions != self.manifest['dependencies']:
            raise ValueError('Prediction dependencies differ from the recorded model environment.')
        if benchmark.v.digest(Path(benchmark.__file__)) != self.manifest['code_sha256']:
            raise ValueError('Benchmark implementation differs from the model run.')
        # Only operator-controlled release artifacts are loaded, never request uploads.
        self.bundle = joblib.load(model_path)
        if (self.bundle['dependencies'] != versions or
                self.bundle['dataset_contract_sha256'] != self.manifest['dataset_contract_sha256']):
            raise ValueError('Model bundle disagrees with the run manifest.')
        self.references = benchmark.bit_vectors(self.bundle['reference_X'])
        self.positive_indices = [np.flatnonzero(self.bundle['reference_annotations'][:, j])
                                 for j in range(len(self.bundle['labels']))]
        for model in self.bundle['model'].get('models', []):
            if hasattr(model, 'n_jobs'):
                model.n_jobs = 1
        self.lock = Lock()

    def info(self):
        return {'selected_model': self.bundle['selected_candidate']['id'],
                'model_sha256': self.model_sha256, 'labels': self.bundle['labels'],
                'dataset_contract_sha256': self.bundle['dataset_contract_sha256'],
                'reference_molecules': len(self.references), 'dependencies': self.bundle['dependencies'],
                'score_meaning': 'ranking of recorded annotations; not a calibrated probability of industrial usefulness'}

    def predict(self, smiles, top_k):
        if not 1 <= top_k <= len(self.bundle['labels']):
            raise UnsupportedQuery('top_k exceeds the supported function count.')
        with self.lock, threadpool_limits(limits=1):
            try:
                canonical, X, fp = benchmark.query_features(smiles, self.bundle)
            except ValueError as exc:
                raise UnsupportedQuery(str(exc)) from exc
            if Chem.MolFromSmiles(canonical).GetNumHeavyAtoms() > 256:
                raise UnsupportedQuery('This interactive endpoint supports at most 256 heavy atoms.')
            scores = benchmark.score_candidate(self.bundle['model'], X)[0]
            similarities = np.asarray(DataStructs.BulkTanimotoSimilarity(fp, self.references))
            ids, catalog = self.bundle['reference_ids'], self.bundle['catalog']
            exact = [identity for identity in ids if catalog[identity]['canonical_smiles'] == canonical]
            ranking = []
            for j in np.argsort(-scores, kind='stable')[:top_k]:
                role = self.bundle['labels'][j]
                neighbors = sorted(self.positive_indices[j], key=lambda i: (-similarities[i], str(ids[i])))[:3]
                ranking.append({'role': role, 'annotation_rank_score': float(scores[j]),
                    'reference_chemicals': [dict(molecule_id=str(ids[i]), tanimoto=float(similarities[i]),
                        canonical_smiles=catalog[ids[i]]['canonical_smiles'], dtxsids=catalog[ids[i]]['dtxsids'],
                        evidence=catalog[ids[i]]['recorded_roles'][role]) for i in neighbors]})
            return {'canonical_smiles': canonical, 'source_identity_cross_check': 'not_performed_for_user_query',
                'exact_reference_match': bool(exact),
                'recorded_selected_roles_for_exact_match': sorted({role for identity in exact for role in catalog[identity]['recorded_roles']}),
                'nearest_reference_tanimoto': float(similarities.max()),
                'selected_model': self.bundle['selected_candidate']['id'],
                'score_meaning': 'ranking of recorded annotations; not a calibrated probability of industrial usefulness',
                'evidence_scope': 'train_and_validation_reference_chemicals_only; max_three_references_per_neighbor_role',
                'model_sha256': self.model_sha256, 'dataset_contract_sha256': self.bundle['dataset_contract_sha256'],
                'ranked_functions': ranking}
