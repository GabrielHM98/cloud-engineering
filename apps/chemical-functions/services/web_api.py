"""Single browser entry point; communicates with both backends over HTTP."""
import asyncio
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
import httpx
from services.common import BodyLimit, PredictionRequest

log = logging.getLogger(__name__)


def create_app(client=None):
    catalog_url = os.environ.get('CATALOG_URL', 'http://catalog:8080').rstrip('/')
    predictor_url = os.environ.get('PREDICTOR_URL', 'http://predictor:8080').rstrip('/')

    @asynccontextmanager
    async def lifespan(app):
        app.state.client = client or httpx.AsyncClient(timeout=httpx.Timeout(30, connect=3), trust_env=False)
        yield
        if client is None:
            await app.state.client.aclose()

    app = FastAPI(title='Chemical function workbench', version='0.1.0', lifespan=lifespan)
    app.add_middleware(BodyLimit)

    async def upstream(base, path, method='GET', **kwargs):
        try:
            response = await app.state.client.request(method, base+path, **kwargs)
        except httpx.TimeoutException as exc:
            raise HTTPException(504, 'A backend timed out. Retry shortly.') from exc
        except httpx.RequestError as exc:
            log.warning('Backend request failed: %s', type(exc).__name__)
            raise HTTPException(503, 'A backend service is unavailable.') from exc
        if response.status_code in {404, 422}:
            try:
                detail = response.json().get('detail', 'Backend rejected the request.')
            except ValueError:
                detail = 'Backend rejected the request.'
            raise HTTPException(response.status_code, detail)
        if response.status_code >= 400:
            raise HTTPException(503, 'A backend service is not ready.')
        try:
            return response.json()
        except ValueError as exc:
            raise HTTPException(502, 'Backend returned an invalid response.') from exc

    @app.get('/')
    def index():
        return FileResponse(Path(__file__).with_name('index.html'))

    @app.get('/healthz')
    def health():
        return {'status': 'alive', 'service': 'web'}

    @app.get('/readyz')
    async def ready():
        checks = await asyncio.gather(upstream(catalog_url, '/readyz', timeout=3),
                                      upstream(predictor_url, '/readyz', timeout=3), return_exceptions=True)
        if any(isinstance(r, Exception) for r in checks):
            raise HTTPException(503, 'Catalog and predictor must both be ready.')
        return {'status': 'ready', 'services': ['catalog', 'predictor']}

    @app.get('/api/catalog/summary')
    async def summary():
        return await upstream(catalog_url, '/v1/catalog/summary')

    @app.get('/api/catalog/search')
    async def search(q: str = Query(min_length=1, max_length=200), limit: int = Query(default=10, ge=1, le=25)):
        return await upstream(catalog_url, '/v1/catalog/search', params={'q': q, 'limit': limit})

    @app.get('/api/catalog/substances/{dtxsid}')
    async def substance(dtxsid: str):
        if not dtxsid.startswith('DTXSID') or not dtxsid[6:].isdigit() or len(dtxsid) > 80:
            raise HTTPException(404, 'Supply a catalog substance ID.')
        return await upstream(catalog_url, '/v1/catalog/substances/'+dtxsid)

    @app.get('/api/model')
    async def model():
        return await upstream(predictor_url, '/v1/model')

    @app.post('/api/predict')
    async def predict(body: PredictionRequest):
        prediction = await upstream(predictor_url, '/v1/predict', method='POST', json=body.model_dump())
        try:
            lookup = await upstream(catalog_url, '/v1/catalog/structure', params={'smiles': prediction['canonical_smiles']})
            catalog_result = {'status': 'available', **lookup}
        except HTTPException:
            catalog_result = {'status': 'unavailable', 'meaning': 'Catalog lookup failed; no conclusion about recorded uses.'}
        return {'prediction': prediction, 'catalog_lookup': catalog_result}

    return app


app = create_app()
