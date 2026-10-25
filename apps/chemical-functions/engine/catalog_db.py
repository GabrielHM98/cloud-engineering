#!/usr/bin/env python3
"""Build and inspect a local SQLite catalog from the curated CPDat CSVs."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
TABLES = ('molecule', 'substance', 'function_category', 'recorded_use', 'use_evidence')


def digest(path):
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(4*1024*1024):
            sha.update(chunk)
    return sha.hexdigest()


def read_csv(path, required):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not set(required).issubset(reader.fieldnames or []):
            raise ValueError(f'Missing required columns in {path.name}.')
        rows = list(reader)
    if any(None in r or any(value is None for value in r.values()) for r in rows):
        raise ValueError(f'Malformed record in {path.name}.')
    return rows


def readonly(path):
    conn = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA query_only=ON')
    return conn


def metadata(conn, key):
    row = conn.execute('SELECT value_json FROM catalog_metadata WHERE key=?', (key,)).fetchone()
    if row is None:
        raise ValueError('This file is not a complete chemical catalog snapshot.')
    return json.loads(row[0])


def verify(conn):
    if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Database integrity check failed.')
    if conn.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Database contains broken chemical/evidence relationships.')
    mismatch = conn.execute('''
        SELECT r.molecule_id FROM recorded_use r LEFT JOIN (
            SELECT molecule_id, function_id, count(*) AS n,
                   count(DISTINCT nullif(document_id, '')) AS docs
            FROM use_evidence GROUP BY molecule_id, function_id
        ) e USING (molecule_id, function_id)
        WHERE r.evidence_records <> coalesce(e.n, 0)
           OR r.distinct_documents <> coalesce(e.docs, 0) LIMIT 1
    ''').fetchone()
    if mismatch:
        raise ValueError('Recorded-use counts disagree with their supporting evidence.')
    counts = {table: conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in TABLES}
    expected = conn.execute("SELECT value_json FROM catalog_metadata WHERE key='expected_counts'").fetchone()
    if expected is not None and counts != json.loads(expected[0]):
        raise ValueError('Database counts differ from the imported snapshot metadata.')
    return counts


def insert_data(conn, molecules, substances, functions, pairs, evidence):
    function_names = {r['cpdat_function_category_id']: r['function_category'] for r in functions}
    for row in pairs + evidence:
        if function_names.get(row['role_id']) != row['role']:
            raise ValueError('A function identifier/name disagrees with the source vocabulary.')
    conn.executemany('INSERT INTO molecule VALUES (?, ?, ?, ?, ?)', [
        (r['molecule_id'], r['canonical_smiles'], r['computed_inchikey'], int(r['heavy_atoms']), int(r['formal_charge'])) for r in molecules])
    conn.executemany('INSERT INTO substance VALUES ('+','.join('?' for _ in range(16))+')', [
        (r['input_dtxsid'], r['returned_dtxsid'], r['molecule_id'] if r['status']=='accepted' else None,
         r['preferred_name'], r['source_casrn'], r['dtxcid'], r['status'], r['original_smiles'],
         r['source_inchikey'], r['identifier_check'], r['qc_level'], r['qc_notes'], r['source_csv'],
         r['source_csv_sha256'], r['csv_record_number'], int(r['export_record_count']))
        for r in substances])
    conn.executemany('INSERT INTO function_category VALUES (?, ?, ?, ?)', [
        (r['cpdat_function_category_id'], r['function_category'], r['function_category_description'], r['fc_kind']) for r in functions])
    conn.executemany('INSERT INTO recorded_use VALUES (?, ?, ?, ?)', [
        (r['molecule_id'], r['role_id'], int(r['evidence_records']), int(r['distinct_documents'])) for r in pairs])
    conn.executemany('''INSERT INTO use_evidence
        (molecule_id,function_id,dtxsid,source_name,source_url,document_id,document_url,raw_functional_use)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', [
        (r['molecule_id'], r['role_id'], r['dtxsid'], r['source'], r['source_url'], r['document_id'],
         r['document_url'], r['raw_functional_use']) for r in evidence])


def build(root, output):
    data = root/'data/processed/structure_full'
    paths = {name: data/name for name in ['accepted_molecules.csv', 'substance_validation.csv', 'accepted_role_pairs.csv', 'accepted_evidence.csv']}
    paths.update(vocabulary=root/'data/interim/function_vocabulary.csv',
                 audit=root/'reports/structure_full/structure_audit.json',
                 source_manifest=root/'reports/structure_full/structure_manifest.json',
                 source_lock=root/'source-lock.json', structure_policy=root/'structure_policy.json')
    contract = {'schema_version': 1, 'loader_sha256': digest(Path(__file__)),
                'schema_sha256': digest(ROOT/'catalog_schema.sql'),
                'input_sha256': {str(p.relative_to(root)): digest(p) for p in paths.values()}}
    contract_sha = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
    if output.exists():
        with readonly(output) as conn:
            if metadata(conn, 'import_contract_sha256') != contract_sha:
                raise ValueError('Database belongs to a different import. Choose a new --database filename; existing data was preserved.')
            counts = verify(conn)
        print(json.dumps({'database': str(output), 'status': 'existing_snapshot_verified', 'counts': counts}, indent=2))
        return counts
    audit = json.loads(paths['audit'].read_text())
    if not audit['export_coverage_complete']:
        raise ValueError('Complete source export coverage is required for this catalog snapshot.')
    molecules = read_csv(paths['accepted_molecules.csv'], ['molecule_id','canonical_smiles','computed_inchikey','heavy_atoms','formal_charge'])
    substances = read_csv(paths['substance_validation.csv'], ['input_dtxsid','returned_dtxsid','molecule_id','status','preferred_name','source_casrn','dtxcid','original_smiles','source_inchikey','identifier_check','qc_level','qc_notes','source_csv','source_csv_sha256','csv_record_number','export_record_count'])
    functions = read_csv(paths['vocabulary'], ['cpdat_function_category_id','function_category','function_category_description','fc_kind'])
    pairs = read_csv(paths['accepted_role_pairs.csv'], ['molecule_id','role_id','role','evidence_records','distinct_documents'])
    evidence = read_csv(paths['accepted_evidence.csv'], ['molecule_id','role_id','role','dtxsid','source','source_url','document_id','document_url','raw_functional_use'])
    expected = {'molecule': audit['accepted_unique_structures'], 'substance': audit['requested_ids'],
                'function_category': len(functions), 'recorded_use': audit['accepted_structure_role_pairs'],
                'use_evidence': audit['accepted_evidence_records']}
    actual = dict(zip(TABLES, map(len, [molecules, substances, functions, pairs, evidence])))
    if actual != expected:
        raise ValueError('CSV counts differ from their structure audit.')
    if sum(r['status']=='accepted' for r in substances) != audit['accepted_substances']:
        raise ValueError('Accepted substance count differs from the audit.')
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.catalog-', suffix='.sqlite3', dir=output.parent)
    os.close(fd)
    temporary = Path(name)
    conn = None
    try:
        conn = sqlite3.connect(temporary)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript((ROOT/'catalog_schema.sql').read_text())
        with conn:
            insert_data(conn, molecules, substances, functions, pairs, evidence)
            values = {'schema_version': 1, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
                      'import_contract_sha256': contract_sha, 'import_contract': contract,
                      'source_lock': json.loads(paths['source_lock'].read_text()),
                      'structure_policy': json.loads(paths['structure_policy'].read_text()),
                      'source_manifest': json.loads(paths['source_manifest'].read_text()),
                      'source_audit': audit, 'expected_counts': expected,
                      'recorded_use_meaning': 'source annotation; not a model prediction or experimental validation'}
            conn.executemany('INSERT INTO catalog_metadata VALUES (?, ?)', [(k, json.dumps(value, ensure_ascii=False)) for k,value in values.items()])
            counts = verify(conn)
            if counts != expected:
                raise ValueError('Imported database counts differ from the source audit.')
        conn.close()
        conn = None
        # Publish the completed file without replacing an existing user database.
        os.link(temporary, output)
    finally:
        if conn is not None:
            conn.close()
        temporary.unlink(missing_ok=True)
    print(json.dumps({'database': str(output), 'status': 'created', 'counts': counts}, indent=2))
    return counts


def summary(conn):
    counts = verify(conn)
    return {'source_dataset': metadata(conn, 'source_lock')['dataset'], 'counts': counts,
            'functions_with_accepted_uses': conn.execute('SELECT count(DISTINCT function_id) FROM recorded_use').fetchone()[0],
            'substance_statuses': dict(conn.execute('SELECT validation_status,count(*) FROM substance GROUP BY validation_status')),
            'model_predictions_stored': False}


def find(conn, query, limit=10):
    if not 1 <= limit <= 100:
        raise ValueError('Limit must be between 1 and 100.')
    return [dict(row) for row in conn.execute('''
        SELECT s.dtxsid,s.preferred_name,s.casrn,s.validation_status,m.canonical_smiles
        FROM substance s LEFT JOIN molecule m USING (molecule_id)
        WHERE s.preferred_name LIKE ? OR s.casrn=? OR s.dtxsid=? OR m.canonical_smiles=?
        ORDER BY (lower(s.preferred_name)=lower(?)) DESC,s.preferred_name,s.dtxsid LIMIT ?
    ''', ('%'+query+'%',query,query,query,query,limit))]


def show(conn, dtxsid):
    row = conn.execute('''SELECT s.*,m.canonical_smiles,m.computed_inchikey
        FROM substance s LEFT JOIN molecule m USING (molecule_id) WHERE s.dtxsid=?''', (dtxsid,)).fetchone()
    if row is None:
        raise ValueError('Substance ID is absent from this catalog snapshot.')
    result = dict(row)
    result['recorded_uses'] = []
    if row['molecule_id']:
        uses = conn.execute('''SELECT f.function_id,f.name,r.evidence_records,r.distinct_documents
            FROM recorded_use r JOIN function_category f USING (function_id)
            WHERE r.molecule_id=? ORDER BY f.name''', (row['molecule_id'],)).fetchall()
        for use in uses:
            item = dict(use)
            item['evidence_examples'] = [dict(r) for r in conn.execute('''
                SELECT DISTINCT dtxsid,source_name,source_url,document_id,document_url,raw_functional_use
                FROM use_evidence WHERE molecule_id=? AND function_id=?
                ORDER BY source_name,document_id,raw_functional_use LIMIT 2
            ''', (row['molecule_id'],use['function_id']))]
            result['recorded_uses'].append(item)
    result['use_meaning'] = 'recorded source annotations; prediction is a separate model operation'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ['build','summary','find','show','functions']:
        cmd = sub.add_parser(name)
        cmd.add_argument('--database', type=Path, default=ROOT/'data/catalog/chemicals.sqlite3')
        if name=='build':
            cmd.add_argument('--project-dir', type=Path, default=ROOT)
        elif name=='find':
            cmd.add_argument('query')
        elif name=='show':
            cmd.add_argument('dtxsid')
    args = parser.parse_args()
    try:
        if args.command=='build':
            build(args.project_dir.resolve(), args.database.resolve())
            return 0
        with readonly(args.database) as conn:
            if args.command=='summary':
                result = summary(conn)
            elif args.command=='find':
                result = find(conn,args.query)
            elif args.command=='show':
                result = show(conn,args.dtxsid)
            else:
                result = [dict(row) for row in conn.execute('SELECT name,accepted_molecules,supporting_records FROM function_support WHERE accepted_molecules>0 ORDER BY accepted_molecules DESC,name LIMIT 20')]
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (OSError,ValueError,KeyError,sqlite3.Error,csv.Error) as exc:
        print(f'Catalog operation stopped: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
