import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import joblib
import numpy as np

import benchmark as b
import validate_structures as v

POLICY = json.loads((v.ROOT / 'benchmark_policy.json').read_text())
STRUCTURE = json.loads((v.ROOT / 'structure_policy.json').read_text())
FEATURES = json.loads((v.ROOT / 'diversity_policy.json').read_text())['morgan']


class BenchmarkTests(unittest.TestCase):
    def test_group_partition_is_stable_and_independent_of_iteration_order(self):
        groups = [f'group{i}' for i in range(100)]
        first = {g: b.partition_for(g, POLICY) for g in groups}
        self.assertEqual(first, {g: b.partition_for(g, POLICY) for g in reversed(groups)})
        self.assertEqual(set(first.values()), set(b.PARTITIONS))
        self.assertEqual(b.partition_for('same-group', POLICY), b.partition_for('same-group', POLICY))

    def test_binary_fingerprint_conversion_preserves_bit_positions(self):
        X = np.zeros((2, 2048), dtype=np.uint8)
        X[0, [0, 7, 8, 177, 2047]] = 1
        X[1, [2, 17]] = 1
        fps = b.bit_vectors(X)
        self.assertEqual(set(fps[0].GetOnBits()), {0, 7, 8, 177, 2047})
        self.assertEqual(set(fps[1].GetOnBits()), {2, 17})

    def test_cross_split_duplicate_fingerprint_is_rejected(self):
        X = np.eye(8, dtype=np.uint8)[:3]
        split = np.asarray(['train', 'validation', 'test'])
        checks = b.cross_split_similarity(X, split, 0.7)
        self.assertTrue(all(r['pairs_at_or_above_threshold'] == 0 for r in checks))
        X[2] = X[0]
        with self.assertRaisesRegex(ValueError, 'Cross-partition similarity'):
            b.cross_split_similarity(X, split, 0.7)

    def test_annotation_metric_constant_reference_and_unannotated_query_denominator(self):
        A = np.asarray([[1, 0], [0, 1], [0, 0], [1, 0]], dtype=np.uint8)
        constant = b.annotation_metrics(A, np.ones_like(A, dtype=float), ['a', 'b'])
        self.assertEqual(constant['macro_observed_annotation_ap'], 0.375)
        self.assertEqual(constant['annotated_queries_for_recall_at_3'], 3)
        self.assertEqual(constant['queries_with_no_selected_annotation'], 1)
        self.assertEqual(constant['mean_observed_recall_at_3_on_annotated_queries'], 1)
        perfect = b.annotation_metrics(A, A.astype(float), ['a', 'b'])
        self.assertEqual(perfect['macro_observed_annotation_ap'], 1)

    def test_model_selection_uses_validation_only(self):
        results = [{'candidate': {'id': 'a'}, 'metrics': {'macro_observed_annotation_ap': 0.3}, 'test_ap': 1.0},
                   {'candidate': {'id': 'b'}, 'metrics': {'macro_observed_annotation_ap': 0.6}, 'test_ap': 0.0}]
        self.assertEqual(b.select_winner(results)['candidate']['id'], 'b')

    def test_nearest_positive_baseline_uses_only_role_annotated_reference_molecules(self):
        X = np.asarray([[1, 0, 0, 0, 0, 0, 0, 0], [0, 1, 0, 0, 0, 0, 0, 0]], dtype=np.uint8)
        A = np.eye(2, dtype=np.uint8)
        model = b.fit_candidate({'kind': 'nearest_positive'}, X, A, POLICY)
        np.testing.assert_array_equal(b.score_candidate(model, X), np.eye(2))

    def test_fitted_binary_models_survive_serialization_with_label_columns_intact(self):
        X = np.asarray([[1, 0, 1, 0, 0, 0, 0, 0], [0, 1, 0, 1, 0, 0, 0, 0]]*8, dtype=np.uint8)
        A = np.asarray([[1, 0], [0, 1]]*8, dtype=np.uint8)
        policy = dict(POLICY, forest_estimators=4, cpu_threads=1)
        for spec in [{'kind': 'logistic', 'C': 1.0}, {'kind': 'forest', 'min_samples_leaf': 1}]:
            model = b.fit_candidate(spec, X, A, policy)
            scores = b.score_candidate(model, X[:2])
            self.assertGreater(scores[0, 0], scores[0, 1])
            self.assertGreater(scores[1, 1], scores[1, 0])
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder)/'model.joblib'
                joblib.dump(model, path)
                np.testing.assert_allclose(b.score_candidate(joblib.load(path), X[:2]), scores)

    def test_query_checks_graph_scope_without_claiming_source_identity_verification(self):
        bundle = {'structure_policy': STRUCTURE, 'feature_policy': FEATURES}
        canonical, X, _ = b.query_features('OCC', bundle)
        self.assertEqual(canonical, 'CCO')
        self.assertEqual(X.shape, (1, 2048))
        for bad in ['[Na+].CC(=O)[O-]', 'CCO |Sg:n:0,1::ht|', '[Cl-]', 'C1CC']:
            with self.assertRaisesRegex(ValueError, 'outside the supported graph policy'):
                b.query_features(bad, bundle)

    def test_insufficient_label_support_fails_before_training(self):
        A = np.asarray([[1], [0], [1]], dtype=np.uint8)
        with self.assertRaisesRegex(ValueError, 'Insufficient'):
            b.label_support(A, np.asarray(['a', 'b', 'c']), np.asarray(['g1', 'g2', 'g3']),
                            np.asarray(['train', 'validation', 'test']), ['role'], POLICY)

    def test_modified_frozen_dataset_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path/'dataset.npz').write_bytes(b'changed')
            v.write_json(path/'dataset_manifest.json', {'output_sha256': {'dataset.npz': 'wrong'}})
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                b.load_dataset(path)

    def test_completed_run_is_reused_without_refitting_or_retesting(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            (run/'benchmark_report.md').write_text('existing report')
            v.write_json(run/'test_results.json', {'already_evaluated': True})
            v.write_json(run/'run_manifest.json', {'dataset_contract_sha256': 'contract',
                'output_sha256': {name: v.digest(run/name) for name in ['benchmark_report.md', 'test_results.json']}})
            manifest = {'contract_sha256': 'contract', 'contract': {'policy': POLICY, 'dependencies': {},
                        'code_sha256': v.digest(Path(b.__file__))}}
            with patch.object(b, 'dependency_versions', return_value={}), patch.object(b, 'load_dataset', return_value=(manifest, {})), patch.object(b, 'fit_candidate', side_effect=AssertionError('should not refit')):
                self.assertTrue(b.train(run, run, run)['already_evaluated'])

    def test_inference_catalog_excludes_held_out_molecules(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root/'data/processed/structure_full'
            data.mkdir(parents=True)
            molecules = [{'molecule_id': mid, 'canonical_smiles': smi, 'source_dtxsids': json.dumps([sid])}
                         for mid, smi, sid in [('train-mol', 'CCO', 'DTXSID1'), ('test-mol', 'CCC', 'DTXSID2')]]
            v.write_csv(data/'accepted_molecules.csv', molecules, list(molecules[0]))
            evidence = [{'molecule_id': row['molecule_id'], 'role': 'Solvent', 'source': 'source',
                         'document_id': 'doc', 'document_url': 'https://example.org/document',
                         'dtxsid': json.loads(row['source_dtxsids'])[0], 'raw_functional_use': 'solvent'} for row in molecules]
            v.write_csv(data/'accepted_evidence.csv', evidence, list(evidence[0]))
            catalog = b.reference_catalog(root, ['train-mol'], {'Solvent'})
            self.assertEqual(set(catalog), {'train-mol'})
            self.assertEqual(catalog['train-mol']['recorded_roles']['Solvent'][0]['dtxsid'], 'DTXSID1')


if __name__ == '__main__':
    unittest.main()
