#!/usr/bin/env python3
"""Developer check: launch the three real HTTP processes without Docker."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from smoke_test import check

ROOT=Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def main():
    processes=[]
    with tempfile.TemporaryDirectory() as folder:
        logs=[]
        try:
            ports={service:free_port() for service in ['catalog','predictor','web']}
            env={**os.environ,'PYTHONUNBUFFERED':'1','PYTHONDONTWRITEBYTECODE':'1',
                 'PYTHONPATH':str(ROOT/'engine'),'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1',
                 'CATALOG_URL':f'http://127.0.0.1:{ports["catalog"]}',
                 'PREDICTOR_URL':f'http://127.0.0.1:{ports["predictor"]}'}
            for service,port in ports.items():
                log=(Path(folder)/(service+'.log')).open('w+')
                logs.append(log)
                processes.append(subprocess.Popen([sys.executable,'-m','uvicorn',f'services.{service}_api:app',
                    '--host','127.0.0.1','--port',str(port),'--workers','1','--no-access-log'],cwd=ROOT,env=env,
                    stdout=log,stderr=subprocess.STDOUT))
            base=f'http://127.0.0.1:{ports["web"]}'
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline=time.monotonic()+45
            while True:
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError('A service exited before becoming ready.')
                try:
                    with opener.open(base+'/readyz',timeout=2) as r:
                        if r.status==200:
                            break
                except OSError:
                    pass
                if time.monotonic()>deadline:
                    raise RuntimeError('Services did not become ready.')
                time.sleep(.1)
            result=check(base)
            result['execution']='three local Uvicorn processes, not containers'
            print(json.dumps(result,indent=2))
        except Exception:
            for log in logs:
                log.flush();log.seek(0)
                print(log.read(),file=sys.stderr)
            raise
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait()
            for log in logs:
                log.close()


if __name__=='__main__':
    main()
