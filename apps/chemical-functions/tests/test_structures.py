import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from rdkit import Chem
from rdkit.Chem import inchi
from validate_structures import ROOT, aggregate, assess_smiles, compare_keys, load_expected, validate_export, validate_policy

POLICY = json.loads((ROOT / 'structure_policy.json').read_text())


def assess(smiles, key=None):
    if key is None:
        molecule = Chem.MolFromSmiles(smiles)
        key = inchi.MolToInchiKey(molecule) if molecule else 'N/A'
    return assess_smiles(smiles, key, POLICY)


def record(sid, smiles='CCO'):
    mol = Chem.MolFromSmiles(smiles)
    return {'INPUT':sid, 'DTXSID':sid, 'SMILES':smiles, 'INCHIKEY':inchi.MolToInchiKey(mol)}


class StructureTests(unittest.TestCase):
    def test_unimplemented_policy_changes_cannot_silently_change_metadata(self):
        policy = dict(POLICY, strip_counterions=True)
        with self.assertRaisesRegex(ValueError, 'Unsupported policy'):
            validate_policy(policy)

    def test_missing_markers_are_not_parsed_as_structures(self):
        for value in ['', 'N/A', ' null ']:
            self.assertEqual(assess_smiles(value, '', POLICY)['status'], 'missing_smiles')

    def test_valid_structure_is_canonicalized(self):
        self.assertEqual(assess('OCC')['canonical_smiles'], 'CCO')
        self.assertEqual(assess('OCC')['molecule_id'], assess('CCO')['molecule_id'])

    def test_charge_is_preserved_without_neutralization(self):
        result = assess('C[NH3+]')
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['formal_charge'], 1)
        self.assertIn('+', result['canonical_smiles'])

    def test_stereoisomers_and_isotopes_are_not_merged(self):
        a, b = assess('N[C@@H](C)C(=O)O'), assess('N[C@H](C)C(=O)O')
        self.assertEqual(a['status'], 'accepted')
        self.assertEqual(b['status'], 'accepted')
        self.assertNotEqual(a['molecule_id'], b['molecule_id'])
        self.assertNotEqual(assess('[13CH3]CO')['molecule_id'], assess('CCO')['molecule_id'])

    def test_salts_are_flagged_without_stripping_counterions(self):
        result = assess('[Na+].CCCCOS(=O)(=O)[O-]')
        self.assertEqual(result['status'], 'multiple_components')
        self.assertEqual(result['fragment_count'], 2)

    def test_extended_and_wildcard_structures_are_out_of_scope(self):
        self.assertEqual(assess_smiles('CCO |Sg:n:0,1::ht|', '', POLICY)['status'], 'unsupported_extended_smiles')
        self.assertEqual(assess_smiles('CC*', '', POLICY)['status'], 'query_or_wildcard')

    def test_invalid_graph_fails_without_passing(self):
        result = assess_smiles('C1CC', '', POLICY)
        self.assertEqual(result['status'], 'invalid_smiles')
        self.assertFalse(result['structural_pass'])

    def test_only_standard_flag_difference_gets_exception(self):
        key = inchi.MolToInchiKey(Chem.MolFromSmiles('CCO'))
        variant = key[:23] + 'N' + key[24:]
        self.assertEqual(compare_keys(variant, key), 'standard_flag_only')
        self.assertEqual(assess_smiles('CCO', variant, POLICY)['status'], 'accepted')
        changed_hash = ('A' if key[0] != 'A' else 'B') + key[1:]
        self.assertEqual(assess_smiles('CCO', changed_hash, POLICY)['status'], 'identifier_hash_conflict')
        changed_second_hash = key[:15] + ('A' if key[15] != 'A' else 'B') + key[16:]
        self.assertEqual(compare_keys(changed_second_hash, key), 'identifier_hash_conflict')
        changed_protonation = key[:-1] + ('O' if key[-1] != 'O' else 'N')
        self.assertEqual(compare_keys(changed_protonation, key), 'identifier_hash_conflict')

    def test_missing_identifier_is_not_implicitly_accepted(self):
        result = assess_smiles('CCO', 'N/A', POLICY)
        self.assertTrue(result['structural_pass'])
        self.assertEqual(result['status'], 'missing_source_key')

    def test_id_conflicts_missing_rows_extras_and_duplicate_mappings(self):
        expected = ['DTXSID100', 'DTXSID200', 'DTXSID300', 'DTXSID400']
        rows = [record('DTXSID100'), record('DTXSID100'), record('DTXSID200'),
                record('DTXSID200', 'CCC'), record('DTXSID400'), record('DTXSID999')]
        rows[4]['DTXSID'] = 'DTXSID500'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'input.csv'
            with path.open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=['INPUT','DTXSID','SMILES','INCHIKEY'])
                writer.writeheader(); writer.writerows(rows)
            records, substances=validate_export(path,expected,POLICY)
        byid={r['input_dtxsid']:r for r in substances}
        self.assertEqual(byid['DTXSID100']['status'],'accepted')
        self.assertEqual(byid['DTXSID100']['export_record_count'],2)
        self.assertEqual(byid['DTXSID200']['status'],'conflicting_export_records')
        self.assertEqual(byid['DTXSID300']['status'],'missing_export_record')
        self.assertEqual(byid['DTXSID400']['status'],'input_returned_id_conflict')
        self.assertEqual(records[-1]['status'],'unexpected_input_id')
        self.assertEqual(len(substances),len(expected))

    def test_schema_drift_and_repeated_expected_ids_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'input.csv'
            path.write_text('DTXSID,SMILES\nDTXSID100,CCO\n')
            with self.assertRaisesRegex(ValueError,'schema'):
                validate_export(path,['DTXSID100'],POLICY)
            path.write_text('DTXSID100\nDTXSID100\n')
            with self.assertRaisesRegex(ValueError,'unique'):
                load_expected(path)

    def test_structure_role_pairs_merge_duplicate_identities_and_preserve_evidence(self):
        a=dict(assess('CCO'),input_dtxsid='DTXSID100')
        b=dict(assess('OCC'),input_dtxsid='DTXSID200')
        evidence=[{'molecule_id':a['molecule_id'],'role_id':'1','role':'Solvent','dtxsid':sid,'document_id':doc}
                  for sid,doc in [('DTXSID100','doc1'),('DTXSID100','doc1'),('DTXSID200','doc2')]]
        molecules,pairs,coverage=aggregate([a,b],{'DTXSID100':{'Solvent'},'DTXSID200':{'Solvent'}},{'Solvent':'1'},evidence)
        self.assertEqual(len(molecules),1)
        self.assertEqual(len(pairs),1)
        self.assertEqual(pairs[0]['evidence_records'],3)
        self.assertEqual(pairs[0]['distinct_documents'],2)
        self.assertEqual(coverage[0]['accepted_sample_substances_with_role'],2)
        self.assertEqual(coverage[0]['accepted_unique_structures_with_role'],1)


if __name__=='__main__':
    unittest.main()
