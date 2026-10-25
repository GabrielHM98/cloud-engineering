import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import catalog_db as c


def fixture(conn):
    molecules = [dict(molecule_id='mol1',canonical_smiles='CCO',computed_inchikey='key',heavy_atoms='3',formal_charge='0')]
    substance = dict(input_dtxsid='DTXSID1',returned_dtxsid='DTXSID1',molecule_id='mol1',status='accepted',
                     preferred_name='Ethanol',source_casrn='64-17-5',dtxcid='DTXCID1',original_smiles='CCO',
                     source_inchikey='key',identifier_check='exact_match',qc_level='1',qc_notes='',
                     source_csv='source.csv',source_csv_sha256='sha',csv_record_number='1',export_record_count='1')
    excluded = dict(substance,input_dtxsid='DTXSID2',returned_dtxsid='DTXSID2',molecule_id='',status='missing_smiles',preferred_name='Unresolved substance',original_smiles='')
    functions = [dict(cpdat_function_category_id='1',function_category='Solvent',function_category_description='Recorded solvent use.',fc_kind='OECD')]
    pair = dict(molecule_id='mol1',role_id='1',role='Solvent',evidence_records='2',distinct_documents='1')
    evidence = dict(molecule_id='mol1',role_id='1',role='Solvent',dtxsid='DTXSID1',source='Source',source_url='https://example.org',document_id='doc1',document_url='https://example.org/doc',raw_functional_use='solvent')
    c.insert_data(conn,molecules,[substance,excluded],functions,[pair],[evidence,dict(evidence)])


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.executescript((c.ROOT/'catalog_schema.sql').read_text())
        fixture(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_one_known_use_keeps_all_original_evidence_and_excluded_substances(self):
        counts = c.verify(self.conn)
        self.assertEqual(counts['molecule'],1)
        self.assertEqual(counts['substance'],2)
        self.assertEqual(counts['recorded_use'],1)
        self.assertEqual(counts['use_evidence'],2)
        detail = c.show(self.conn,'DTXSID1')
        self.assertEqual(detail['recorded_uses'][0]['name'],'Solvent')
        self.assertEqual(detail['recorded_uses'][0]['evidence_records'],2)
        excluded = c.show(self.conn,'DTXSID2')
        self.assertIsNone(excluded['molecule_id'])
        self.assertEqual(excluded['recorded_uses'],[])

    def test_evidence_cannot_reference_an_unresolved_substance(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO use_evidence (molecule_id,function_id,dtxsid,source_name,source_url,document_id,document_url,raw_functional_use) VALUES ('mol1','1','DTXSID2','','','','','solvent')")

    def test_unsupported_use_pair_and_duplicate_identity_are_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO recorded_use VALUES ('missing','1',1,1)")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO molecule VALUES ('mol2','CCO','other',3,0)")

    def test_declared_evidence_counts_are_verified(self):
        self.conn.execute('UPDATE recorded_use SET evidence_records=99')
        with self.assertRaisesRegex(ValueError,'counts disagree'):
            c.verify(self.conn)

    def test_search_values_are_bound_and_do_not_execute_sql(self):
        self.assertEqual(c.find(self.conn,'ethanol')[0]['dtxsid'],'DTXSID1')
        self.assertEqual(c.find(self.conn,"'; DROP TABLE molecule; --"),[])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM molecule').fetchone()[0],1)

    def test_inspection_connection_is_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'catalog.sqlite3'
            disk = sqlite3.connect(path)
            # Commit the fixture before backing up a consistent snapshot.
            self.conn.commit()
            self.conn.backup(disk)
            disk.close()
            with c.readonly(path) as conn:
                self.assertEqual(c.find(conn,'CCO')[0]['preferred_name'],'Ethanol')
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute('DELETE FROM use_evidence')


if __name__=='__main__':
    unittest.main()
