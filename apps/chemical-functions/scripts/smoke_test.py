#!/usr/bin/env python3
"""Exercise the complete app through its public gateway; stdlib only."""
import argparse
import json
import urllib.error
import urllib.parse
import urllib.request


def check(base):
    base = base.rstrip('/')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, body=None, expected=200):
        req = urllib.request.Request(base+path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type':'application/json'} if body is not None else {})
        try:
            with opener.open(req, timeout=45) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        if status != expected:
            raise RuntimeError(f'{path}: expected HTTP {expected}, got {status}: {raw[:300]!r}')
        return json.loads(raw)

    request('/readyz')
    summary = request('/api/catalog/summary')
    if summary['counts']['molecule'] != 8335:
        raise RuntimeError('Unexpected catalog snapshot.')
    results = request('/api/catalog/search?'+urllib.parse.urlencode({'q':'64-17-5'}))['results']
    if not any(r['dtxsid']=='DTXSID9020584' for r in results):
        raise RuntimeError('Ethanol did not resolve in the catalog.')
    substance = request('/api/catalog/substances/DTXSID9020584')
    if not any(r['name']=='Solvent' for r in substance['recorded_uses']):
        raise RuntimeError('Expected source annotations are missing.')
    result = request('/api/predict',{'smiles':'OCC','top_k':3})
    prediction = result['prediction']
    if prediction['canonical_smiles']!='CCO' or prediction['ranked_functions'][0]['role']!='Solvent':
        raise RuntimeError('Unexpected canonicalization or model ranking.')
    if result['catalog_lookup']['status']!='available' or not result['catalog_lookup']['found']:
        raise RuntimeError('Exact catalog lookup failed after prediction.')
    request('/api/predict',{'smiles':'CCO.[Na+]'},expected=422)
    return {'status':'passed','gateway':base,'accepted_molecules':summary['counts']['molecule'],
            'selected_model':prediction['selected_model'],'model_sha256':prediction['model_sha256'],
            'top_roles':[{k:r[k] for k in ['role','annotation_rank_score']} for r in prediction['ranked_functions']],
            'unsupported_chemistry_http':422}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8080')
    args=parser.parse_args()
    try:
        print(json.dumps(check(args.url),indent=2))
    except (OSError,RuntimeError,ValueError,KeyError) as exc:
        parser.exit(1,'Smoke test failed: '+str(exc)+'\n')
