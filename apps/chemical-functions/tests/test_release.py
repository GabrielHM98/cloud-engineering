"""Release contracts, using synthetic data; no Docker, network, or model deserialization."""
from copy import deepcopy
import gzip
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import runtime_artifacts as runtime
from test_catalog_db import fixture


def project(root):
    for name in ['benchmark.py','requirements-structures.txt','requirements-benchmark.txt']:
        shutil.copyfile(ROOT/'engine'/name,root/name)
    db=root/'data/catalog/chemicals.sqlite3';db.parent.mkdir(parents=True)
    conn=sqlite3.connect(db);conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript((ROOT/'engine/catalog_schema.sql').read_text());fixture(conn)
    counts={name:conn.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ['molecule','substance','function_category','recorded_use','use_evidence']}
    conn.executemany('INSERT INTO catalog_metadata VALUES (?,?)',[('schema_version','1'),('expected_counts',json.dumps(counts))])
    conn.commit();conn.close()
    run=root/'runs/benchmark-v1';run.mkdir(parents=True)
    (run/'model_bundle.joblib').write_bytes(b'unit-test-bytes-not-a-pickle')
    pins={}
    for name in ['requirements-structures.txt','requirements-benchmark.txt']:
        for line in (root/name).read_text().splitlines():
            if line and not line.startswith(('#','-r')):
                key,value=line.split('==');pins[key]=value
    (run/'run_manifest.json').write_text(json.dumps({'dependencies':pins,'code_sha256':runtime.digest(root/'benchmark.py'),
        'output_sha256':{'model_bundle.joblib':runtime.digest(run/'model_bundle.joblib')}}))


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.source=self.base/'source';self.source.mkdir();project(self.source)
        self.lock=self.base/'lock.json';self.output=self.base/'output'

    def tearDown(self): self.tmp.cleanup()

    def pack(self):
        return runtime.pack(self.source,'runtime-v1',self.output,self.lock)

    def target(self):
        root=self.base/'target';root.mkdir()
        for name in ['benchmark.py','requirements-structures.txt','requirements-benchmark.txt']:
            shutil.copyfile(ROOT/'engine'/name,root/name)
        return root

    def test_runtime_archive_is_reproducible_and_has_only_approved_inputs(self):
        result=self.pack()
        other=runtime.pack(self.source,'runtime-v1',self.base/'other',self.base/'other-lock.json')
        self.assertEqual(result['sha256'],other['sha256'])
        with tarfile.open(result['archive']) as archive:
            self.assertEqual(set(archive.getnames()),set(runtime.FILES))

    def test_roundtrip_is_idempotent_and_does_not_deserialize_model(self):
        result=self.pack();root=self.target()
        for _ in range(2):
            checks=runtime.install(result['archive'],self.lock,root)
            self.assertEqual(checks['catalog_counts']['molecule'],1)
        runtime.verify_files(root,runtime.load_lock(self.lock))

    def test_legacy_runtime_installs_into_organized_source_layout(self):
        result=self.pack();root=self.target();(root/'engine').mkdir()
        for name in ['benchmark.py','requirements-structures.txt','requirements-benchmark.txt']:
            (root/name).rename(root/'engine'/name)
        runtime.install(result['archive'],self.lock,root)
        self.assertEqual(runtime.preflight(root)['catalog_counts']['molecule'],1)

    def test_changed_archive_is_rejected_before_any_files_are_published(self):
        result=self.pack();root=self.target();p=Path(result['archive']);p.write_bytes(p.read_bytes()+b'tamper')
        with self.assertRaisesRegex(ValueError,'archive SHA-256 mismatch'):
            runtime.install(p,self.lock,root)
        self.assertFalse((root/'data/catalog/chemicals.sqlite3').exists())

    def test_different_existing_runtime_is_preserved(self):
        result=self.pack();root=self.target()
        path=root/'runs/benchmark-v1/model_bundle.joblib';path.parent.mkdir(parents=True);path.write_bytes(b'keep-me')
        with self.assertRaisesRegex(ValueError,'different runtime'):
            runtime.install(result['archive'],self.lock,root)
        self.assertEqual(path.read_bytes(),b'keep-me')
        self.assertFalse((root/'data/catalog/chemicals.sqlite3').exists())

    def test_archive_traversal_links_and_duplicate_members_are_rejected(self):
        result=self.pack()
        for kind in ['traversal','link','duplicate']:
            root=self.target() if not (self.base/'target').exists() else self.base/'target'
            path=self.base/(kind+'.tar.gz')
            with tarfile.open(path,'w:gz') as archive:
                for name in runtime.FILES:
                    content=(self.source/name).read_bytes();info=tarfile.TarInfo(name);info.size=len(content)
                    archive.addfile(info,io.BytesIO(content))
                extra=tarfile.TarInfo('../outside' if kind=='traversal' else runtime.FILES[0])
                if kind=='link': extra.type=tarfile.SYMTYPE;extra.linkname='/etc/passwd'
                archive.addfile(extra,io.BytesIO(b''))
            lock=runtime.load_lock(self.lock);lock['sha256']=runtime.digest(path)
            invalid_lock=self.base/'bad-lock.json';invalid_lock.write_text(json.dumps(lock))
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'unexpected files'):
                runtime.install(path,invalid_lock,root)

    def test_unseeded_or_incomplete_lock_fails(self):
        unseeded={'schema_version':1,'release_tag':'runtime-v1','asset_name':'runtime-v1.tar.gz','sha256':None,'files':{}}
        self.lock.write_text(json.dumps(unseeded))
        with self.assertRaisesRegex(ValueError,'not seeded'): runtime.load_lock(self.lock)
        self.pack();data=runtime.load_lock(self.lock);data['files'].pop(runtime.FILES[0]);self.lock.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'exactly the three'): runtime.load_lock(self.lock)

    def test_missing_inputs_cannot_be_packaged_as_a_successful_runtime(self):
        (self.source/'runs/benchmark-v1/model_bundle.joblib').unlink()
        with self.assertRaises(OSError): self.pack()
        self.assertFalse(self.lock.exists())

    def test_runtime_version_and_repository_inputs_are_bounded(self):
        for tag in ['../runtime','latest','runtime-v1;bad']:
            with self.subTest(tag=tag),self.assertRaises(ValueError):
                runtime.pack(self.source,tag,self.output,self.lock)
        self.pack()
        with patch('runtime_artifacts.subprocess.run',side_effect=AssertionError('No network subprocess')):
            with self.assertRaises(ValueError): runtime.fetch(self.lock,self.source,'owner/repo;command')


if __name__=='__main__': unittest.main()
