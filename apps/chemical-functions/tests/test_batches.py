import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rdkit import Chem
from rdkit.Chem import inchi

import import_structure_batches as batches
import validate_structures as v

POLICY = json.loads((v.ROOT / 'structure_policy.json').read_text())


def raw(sid, smiles='CCO', standard=True):
    key = inchi.MolToInchiKey(Chem.MolFromSmiles(smiles))
    if not standard:
        key = key[:23] + 'N' + key[24:]
    return {'INPUT': sid, 'DTXSID': sid, 'SMILES': smiles, 'INCHIKEY': key}


def write(path, rows):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['INPUT', 'DTXSID', 'SMILES', 'INCHIKEY'])
        writer.writeheader()
        writer.writerows(rows)


class BatchTests(unittest.TestCase):
    def test_overlap_is_reconciled_and_two_ids_for_same_structure_deduplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder)/'a.csv', Path(folder)/'b.csv'
            write(a, [raw('DTXSID1', standard=False)])
            write(b, [raw('DTXSID1'), raw('DTXSID2', 'OCC')])
            expected = ['DTXSID1', 'DTXSID2']
            records, substances, _ = batches.validate_batches([a, b], expected, POLICY)
            swapped, reversed_substances, _ = batches.validate_batches([b, a], expected, POLICY)
            self.assertEqual(substances, reversed_substances)
            self.assertEqual(records, swapped)
            self.assertTrue(all(r['status'] == 'accepted' for r in substances))
            self.assertEqual(substances[0]['identifier_check'], 'exact_match')
            self.assertEqual(substances[0]['export_record_count'], 2)
            self.assertEqual(json.loads(substances[0]['identifier_checks']), ['exact_match', 'standard_flag_only'])
            labels = {'DTXSID1': {'Solvent'}, 'DTXSID2': {'Solvent', 'Humectant'}}
            evidence = [{'molecule_id': row['molecule_id'], 'dtxsid': row['input_dtxsid'],
                         'role_id': '1', 'role': 'Solvent', 'document_id': 'doc'} for row in substances]
            molecules, pairs, _ = v.aggregate(substances, labels, {'Solvent': '1', 'Humectant': '2'}, evidence)
            self.assertEqual(len(molecules), 1)
            self.assertEqual(len(pairs), 1)
            self.assertEqual(pairs[0]['evidence_records'], 2)
            self.assertEqual(len(records), 3)

    def test_conflicting_cross_file_identities_are_quarantined(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder)/'a.csv', Path(folder)/'b.csv'
            write(a, [raw('DTXSID1')])
            write(b, [raw('DTXSID1', 'CCC')])
            _, substances, _ = batches.validate_batches([a, b], ['DTXSID1'], POLICY)
            self.assertEqual(substances[0]['status'], 'conflicting_export_records')
            self.assertEqual(substances[0]['molecule_id'], '')
            self.assertEqual(substances[0]['canonical_smiles'], '')
            self.assertFalse(substances[0]['structural_pass'])

    def test_unresolved_record_is_not_overridden_by_an_accepted_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder)/'a.csv', Path(folder)/'b.csv'
            write(a, [raw('DTXSID1')])
            unresolved = raw('DTXSID1')
            unresolved['SMILES'] = 'N/A'
            write(b, [unresolved])
            _, substances, _ = batches.validate_batches([a, b], ['DTXSID1'], POLICY)
            self.assertEqual(substances[0]['status'], 'conflicting_export_records')

    def test_partial_coverage_separates_pending_ids_from_chemical_exclusions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'a.csv'
            rejected = raw('DTXSID2')
            rejected['SMILES'] = 'N/A'
            write(path, [raw('DTXSID1'), rejected, raw('DTXSID999')])
            records, substances, _ = batches.validate_batches([path], ['DTXSID1', 'DTXSID2', 'DTXSID3'], POLICY)
            labels = {sid: {'Solvent'} for sid in ['DTXSID1', 'DTXSID2', 'DTXSID3']}
            coverage = batches.role_coverage(substances, labels, {'Solvent': '1'})[0]
            self.assertEqual(coverage['requested_substances'], 3)
            self.assertEqual(coverage['exported_substances'], 2)
            self.assertEqual(coverage['pending_export_substances'], 1)
            self.assertEqual(coverage['accepted_substances'], 1)
            metrics = batches.metrics_for(records, substances, [], [], [], labels)
            self.assertFalse(metrics['export_coverage_complete'])
            self.assertEqual(metrics['unexpected_export_records'], 1)
            self.assertEqual(metrics['pending_export_ids'], 1)
            self.assertEqual(batches.completion_exit_code(metrics, True), 2)
            self.assertEqual(batches.completion_exit_code(metrics, False), 0)
            metrics['export_coverage_complete'] = True
            self.assertEqual(batches.completion_exit_code(metrics, True), 0)

    def test_same_file_content_cannot_be_counted_twice(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder)/'a.csv', Path(folder)/'b.csv'
            write(a, [raw('DTXSID1')])
            b.write_bytes(a.read_bytes())
            with self.assertRaisesRegex(ValueError, 'same export content'):
                batches.validate_batches([a, b], ['DTXSID1'], POLICY)

    def test_rdkit_processing_error_is_retained_as_a_review_record(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'a.csv'
            write(path, [raw('DTXSID1')])
            with patch.object(v, 'assess_smiles', side_effect=RuntimeError('test engine failure')):
                records, substances = v.validate_export(path, ['DTXSID1'], POLICY)
            self.assertEqual(substances[0]['status'], 'structure_processing_error')
            self.assertIn('test engine failure', records[0]['processing_error'])
            self.assertEqual(records[0]['molecule_id'], '')


if __name__ == '__main__':
    unittest.main()
