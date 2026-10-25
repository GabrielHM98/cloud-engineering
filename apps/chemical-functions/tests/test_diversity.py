import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from rdkit import DataStructs

import audit_diversity as d
import validate_structures as v

POLICY = json.loads((v.ROOT / 'diversity_policy.json').read_text())
STRUCTURE_POLICY = json.loads((v.ROOT / 'structure_policy.json').read_text())


def fingerprint(*on_bits):
    fp = DataStructs.ExplicitBitVect(8)
    for bit in on_bits:
        fp.SetBit(bit)
    return fp


def molecule(smiles):
    mol = v.Chem.MolFromSmiles(smiles)
    canonical = v.Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    payload = v.policy_hash(STRUCTURE_POLICY)+'|'+v.rdBase.rdkitVersion+'|'+canonical
    return {'molecule_id': 'mol_'+hashlib.sha256(payload.encode()).hexdigest(), 'canonical_smiles': canonical}


class DiversityTests(unittest.TestCase):
    def test_transitive_paths_form_components_without_claiming_all_pairs_similar(self):
        # A-B and B-C each score 2/3, while A-C scores 1/3.
        fps = [fingerprint(0, 1), fingerprint(0, 1, 2), fingerprint(1, 2)]
        ids = ['a', 'b', 'c']
        members, summary, nearest = d.similarity_groups(ids, fps, [0.6, 0.8], 'policy')
        self.assertEqual(len(set(members[0.6].values())), 1)
        self.assertEqual(len(set(members[0.8].values())), 3)
        self.assertEqual(summary[0]['edges_at_or_above_threshold'], 2)
        np.testing.assert_allclose(nearest, [2/3, 2/3, 2/3])
        reversed_members, _, _ = d.similarity_groups(list(reversed(ids)), list(reversed(fps)), [0.6, 0.8], 'policy')
        self.assertEqual(members, reversed_members)

    def test_threshold_boundary_is_inclusive_and_self_similarity_is_excluded(self):
        members, _, nearest = d.similarity_groups(['a', 'b'], [fingerprint(0), fingerprint(0, 1)], [0.5, 0.6], 'policy')
        self.assertEqual(len(set(members[0.5].values())), 1)
        self.assertEqual(len(set(members[0.6].values())), 2)
        np.testing.assert_allclose(nearest, [0.5, 0.5])

    def test_acyclic_structures_have_no_scaffold_and_fingerprint_rows_align_with_ids(self):
        inputs = [molecule('CCCC'), molecule('CCO'), molecule('Cc1ccccc1')]
        rows, fps, bits = d.representations(inputs, POLICY, STRUCTURE_POLICY)
        self.assertEqual([r['molecule_id'] for r in rows], sorted(r['molecule_id'] for r in inputs))
        self.assertEqual(sum(r['scaffold_status'] == 'acyclic_not_applicable' for r in rows), 2)
        self.assertEqual({r['ring_scaffold_smiles'] for r in rows}, {'', 'c1ccccc1'})
        for index, fp in enumerate(fps):
            self.assertEqual(set(np.flatnonzero(bits[index])), set(fp.GetOnBits()))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'features.npz'
            np.savez_compressed(path, molecule_ids=np.asarray([r['molecule_id'] for r in rows]), morgan_bits=bits)
            with np.load(path, allow_pickle=False) as saved:
                self.assertEqual(saved['molecule_ids'].tolist(), [r['molecule_id'] for r in rows])
                np.testing.assert_array_equal(saved['morgan_bits'], bits)

    def test_tampered_or_repeated_accepted_identity_is_rejected(self):
        row = molecule('CCO')
        with self.assertRaisesRegex(ValueError, 'identities'):
            d.representations([row, row], POLICY, STRUCTURE_POLICY)
        with self.assertRaisesRegex(ValueError, 'identities'):
            d.representations([dict(row, canonical_smiles='CCC')], POLICY, STRUCTURE_POLICY)

    def test_duplicate_evidence_does_not_inflate_source_coverage_and_roles_can_overlap(self):
        rows = [{'molecule_id': 'a', 'ring_scaffold_smiles': ''},
                {'molecule_id': 'b', 'ring_scaffold_smiles': 'c1ccccc1'}]
        pairs = [{'molecule_id': mid, 'role': role, 'role_id': rid}
                 for mid, role, rid in [('a', 'Solvent', '1'), ('b', 'Solvent', '1'), ('a', 'Humectant', '2')]]
        evidence = [dict(pair, source='source1', document_id='doc1') for pair in pairs]
        evidence += [dict(evidence[0]), dict(evidence[0], source='source2', document_id='doc2')]
        roles, sources, documents, overlap = d.role_diagnostics(rows, pairs, evidence, {0.7: {'a': 'g1', 'b': 'g2'}}, 0.7)
        solvent = next(row for row in roles if row['role'] == 'Solvent')
        self.assertEqual(solvent['unique_molecules'], 2)
        self.assertEqual(solvent['effective_component_count'], 2)
        self.assertEqual(solvent['top_source_molecule_fraction'], 1)
        self.assertEqual(solvent['distinct_named_sources'], 2)
        second_source = next(row for row in sources if row['role'] == 'Solvent' and row['source'] == 'source2')
        self.assertEqual(second_source['unique_molecules'], 1)
        self.assertEqual(overlap[0]['shared_molecules'], 1)
        self.assertEqual(overlap[0]['jaccard'], 0.5)
        self.assertEqual(overlap[0]['fraction_of_a_annotated_b'], 1)

    def test_unbacked_pairs_and_unknown_evidence_do_not_silently_pass(self):
        rows = [{'molecule_id': 'a', 'ring_scaffold_smiles': ''}]
        pairs = [{'molecule_id': 'a', 'role': 'Solvent', 'role_id': '1'}]
        with self.assertRaisesRegex(ValueError, 'no supporting evidence'):
            d.role_diagnostics(rows, pairs, [], {0.7: {'a': 'g'}}, 0.7)
        bad = [{'molecule_id': 'b', 'role': 'Solvent', 'role_id': '1', 'source': '', 'document_id': ''}]
        with self.assertRaisesRegex(ValueError, 'unknown molecule-role'):
            d.role_diagnostics(rows, pairs, bad, {0.7: {'a': 'g'}}, 0.7)

    def test_policy_and_schema_drift_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, 'Morgan'):
            d.validate_policy(dict(POLICY, morgan=dict(POLICY['morgan'], radius=3)))
        with self.assertRaisesRegex(ValueError, 'thresholds'):
            d.validate_policy(dict(POLICY, similarity_thresholds=[0.7, 0.6]))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'wrong.csv'
            path.write_text('name\nchemical\n')
            with self.assertRaisesRegex(ValueError, 'Missing required columns'):
                d.read_csv(path, ['molecule_id'])


if __name__ == '__main__':
    unittest.main()
