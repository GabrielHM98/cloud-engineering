#!/usr/bin/env python3
"""Package, fetch, and install the approved model/catalog without deserializing a model."""
from __future__ import annotations

import argparse
from contextlib import closing
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parents[1]
FILES=('data/catalog/chemicals.sqlite3','runs/benchmark-v1/model_bundle.joblib','runs/benchmark-v1/run_manifest.json')
SHA=re.compile(r'[a-f0-9]{64}')
VERSION=re.compile(r'(?:[a-z0-9]+(?:-[a-z0-9]+)*-)?runtime-v[0-9]+(?:\.[0-9]+)*')
MAX_TOTAL_BYTES=512*1024*1024


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        while block:=f.read(4*1024*1024): h.update(block)
    return h.hexdigest()


def engine_dir(root):
    root=Path(root)
    return root/'engine' if (root/'engine/benchmark.py').is_file() else root


def preflight(root):
    spec=importlib.util.spec_from_file_location('runtime_preflight',ROOT/'scripts/preflight.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.check(Path(root))


def load_lock(path):
    lock=json.loads(Path(path).read_text())
    if lock.get('schema_version')!=1 or not VERSION.fullmatch(lock.get('release_tag','')):
        raise ValueError('Unsupported runtime lock or release tag.')
    if lock.get('asset_name')!=lock['release_tag']+'.tar.gz':
        raise ValueError('Runtime asset name must match its versioned release tag.')
    if not isinstance(lock.get('sha256'),str) or not SHA.fullmatch(lock['sha256']):
        raise ValueError('Runtime is not seeded. Run scripts/runtime_artifacts.py pack with your working WSL project first.')
    if set(lock.get('files',{}))!=set(FILES) or any(not isinstance(v,str) or not SHA.fullmatch(v) for v in lock['files'].values()):
        raise ValueError('Runtime lock must contain exactly the three approved files and their SHA-256 hashes.')
    return lock


def verify_files(root,lock):
    for name,expected in lock['files'].items():
        path=Path(root)/name
        if path.is_symlink() or not path.is_file() or digest(path)!=expected:
            raise ValueError('Missing or changed runtime file: '+name)


def pack(source,release_tag,output,lock_path):
    source=Path(source).expanduser().resolve()
    if not VERSION.fullmatch(release_tag):
        raise ValueError('Use a runtime version such as runtime-v1.')
    preflight(source)
    hashes={name:digest(source/name) for name in FILES}
    if sum((source/name).stat().st_size for name in FILES)>MAX_TOTAL_BYTES:
        raise ValueError('Runtime inputs exceed the supported archive size limit.')
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    archive=output/(release_tag+'.tar.gz')
    if archive.exists():
        raise ValueError('Runtime archive already exists; use a new version or a separate output directory.')
    fd,temp_name=tempfile.mkstemp(dir=output,prefix='.runtime-',suffix='.tmp');os.close(fd)
    temp=Path(temp_name)
    try:
        with temp.open('wb') as raw, gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as gz, tarfile.open(fileobj=gz,mode='w',format=tarfile.USTAR_FORMAT) as tar:
            for name in sorted(FILES):
                path=source/name
                if path.is_symlink() or not path.is_file(): raise ValueError('Runtime inputs must be regular files.')
                info=tarfile.TarInfo(name);info.size=path.stat().st_size;info.mode=0o644;info.mtime=0
                with path.open('rb') as stream: tar.addfile(info,stream)
        # Refuse to approve inputs that changed while packaging.
        for name in FILES:
            if digest(source/name)!=hashes[name]: raise ValueError('Runtime inputs changed during packaging.')
        lock={'schema_version':1,'release_tag':release_tag,'asset_name':archive.name,'sha256':digest(temp),'files':hashes}
        os.link(temp,archive)
        Path(lock_path).parent.mkdir(parents=True,exist_ok=True)
        Path(lock_path).write_text(json.dumps(lock,indent=2)+'\n')
    finally:
        temp.unlink(missing_ok=True)
    return {'archive':str(archive),'lock':str(lock_path),'sha256':lock['sha256']}


def install(archive,lock_path,root):
    lock=load_lock(lock_path);archive=Path(archive);root=Path(root).resolve()
    if archive.is_symlink() or digest(archive)!=lock['sha256']:
        raise ValueError('Runtime archive SHA-256 mismatch.')
    root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.runtime-stage-',dir=root) as folder:
        stage=Path(folder)
        with tarfile.open(archive,'r:gz') as tar:
            members=tar.getmembers()
            if sorted(m.name for m in members)!=sorted(FILES) or any(not m.isfile() for m in members):
                raise ValueError('Runtime archive contains missing, duplicate, linked, or unexpected files.')
            if sum(m.size for m in members)>MAX_TOTAL_BYTES or any(m.size<0 for m in members):
                raise ValueError('Runtime archive exceeds the supported size limit.')
            for member in members:
                target=stage/member.name;target.parent.mkdir(parents=True,exist_ok=True)
                with tar.extractfile(member) as inp,target.open('wb') as out:
                    shutil.copyfileobj(inp,out)
        verify_files(stage,lock)
        # The benchmark implementation and pins must accompany the artifacts.
        for name in ['benchmark.py','requirements-structures.txt','requirements-benchmark.txt']:
            source=engine_dir(root)/name
            destination=stage/('engine' if engine_dir(root).name=='engine' else '')/name
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,destination)
        result=preflight(stage)
        # Check every destination before publishing any file. Never traverse a symlink.
        for name in FILES:
            target=root/name
            for parent in [target,*target.parents]:
                if parent==root.parent: break
                if parent.is_symlink(): raise ValueError('Runtime destination contains a symlink.')
            if target.exists() and (not target.is_file() or digest(target)!=lock['files'][name]):
                raise ValueError('A different runtime already exists: '+name+'. Use a clean checkout for a new runtime version.')
        for name in FILES:
            target=root/name
            if not target.exists():
                target.parent.mkdir(parents=True,exist_ok=True)
                os.replace(stage/name,target)
    return result


def fetch(lock_path,root,repository):
    lock=load_lock(lock_path)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository):
        raise ValueError('Supply the application GitHub repository as OWNER/REPO.')
    with tempfile.TemporaryDirectory(prefix='runtime-download-') as folder:
        subprocess.run(['gh','release','download',lock['release_tag'],'--repo',repository,
                        '--pattern',lock['asset_name'],'--dir',folder],check=True)
        return install(Path(folder)/lock['asset_name'],lock_path,root)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for command in ['pack','install','fetch','verify']:
        p=sub.add_parser(command)
        p.add_argument('--lock',type=Path,default=ROOT/'runtime/lock.json')
        if command=='pack':
            p.add_argument('--from-project',type=Path,required=True)
            p.add_argument('--release-tag',default='chemical-functions-runtime-v1')
            p.add_argument('--output',type=Path,default=ROOT/'.runtime')
        elif command=='install': p.add_argument('--archive',type=Path,required=True)
        elif command=='fetch': p.add_argument('--repository',required=True)
    args=parser.parse_args()
    try:
        if args.command=='pack': result=pack(args.from_project,args.release_tag,args.output,args.lock)
        elif args.command=='install': result=install(args.archive,args.lock,ROOT)
        elif args.command=='fetch': result=fetch(args.lock,ROOT,args.repository)
        else:
            lock=load_lock(args.lock);verify_files(ROOT,lock);result=preflight(ROOT)
        print(json.dumps(result,indent=2))
    except (OSError,ValueError,KeyError,tarfile.TarError,subprocess.CalledProcessError) as exc:
        parser.exit(1,'Runtime step failed: '+str(exc)+'\n')


if __name__=='__main__': main()
