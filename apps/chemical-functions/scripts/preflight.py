#!/usr/bin/env python3
"""Check local build inputs without importing ML packages on the host."""
from pathlib import Path
import hashlib
import json
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(4*1024*1024):
            h.update(block)
    return h.hexdigest()


def check(root):
    db = root/'data/catalog/chemicals.sqlite3'
    run = root/'runs/benchmark-v1'
    manifest = json.loads((run/'run_manifest.json').read_text())
    if digest(run/'model_bundle.joblib') != manifest['output_sha256']['model_bundle.joblib']:
        raise ValueError('Model hash does not match its run manifest.')
    engine=root/'engine' if (root/'engine/benchmark.py').is_file() else root
    if digest(engine/'benchmark.py') != manifest['code_sha256']:
        raise ValueError('benchmark.py differs from the saved model implementation.')
    pins = {}
    for filename in ['requirements-structures.txt', 'requirements-benchmark.txt']:
        for line in (engine/filename).read_text().splitlines():
            if line and not line.startswith(('#','-r')):
                name, version = line.split('==')
                pins[name] = version
    if pins != manifest['dependencies']:
        raise ValueError('Model dependency pins differ from its saved environment.')
    conn = sqlite3.connect(db.resolve().as_uri()+'?mode=ro', uri=True)
    try:
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Catalog integrity or relationships failed.')
        meta = {k:json.loads(v) for k,v in conn.execute('SELECT * FROM catalog_metadata')}
        counts = {name:conn.execute('SELECT count(*) FROM '+name).fetchone()[0]
                  for name in ['molecule','substance','function_category','recorded_use','use_evidence']}
        if meta['schema_version'] != 1 or counts != meta['expected_counts']:
            raise ValueError('Catalog counts or schema disagree with its metadata.')
    finally:
        conn.close()
    return {'status':'build_inputs_verified', 'catalog_counts':counts,
            'model_sha256':manifest['output_sha256']['model_bundle.joblib'],
            'note':'This checks input artifacts, not container builds or model quality.'}


if __name__=='__main__':
    try:
        print(json.dumps(check(ROOT),indent=2))
    except (OSError,ValueError,KeyError,sqlite3.Error) as exc:
        print('Build input check failed: '+str(exc),file=sys.stderr)
        raise SystemExit(1)
