"""Read-only HTTP catalog. No ML dependencies are imported here."""
from contextlib import asynccontextmanager, closing
import os
from pathlib import Path
import sqlite3

from fastapi import FastAPI, HTTPException, Query
import catalog_db as catalog
from services.common import BodyLimit


def create_app(database=None):
    path = Path(database or os.environ.get('CATALOG_DB', 'data/catalog/chemicals.sqlite3'))

    @asynccontextmanager
    async def lifespan(app):
        with closing(catalog.readonly(path)) as conn:
            if catalog.metadata(conn, 'schema_version') != 1:
                raise ValueError('Unsupported catalog schema.')
            app.state.summary = catalog.summary(conn)
            app.state.snapshot = catalog.metadata(conn, 'import_contract_sha256')
        yield

    app = FastAPI(title='Chemical catalog', version='0.1.0', lifespan=lifespan)
    app.add_middleware(BodyLimit)

    @app.get('/healthz')
    def health():
        return {'status': 'alive', 'service': 'catalog'}

    @app.get('/readyz')
    def ready():
        try:
            with closing(catalog.readonly(path)) as conn:
                if catalog.metadata(conn, 'import_contract_sha256') != app.state.snapshot:
                    raise ValueError('Snapshot changed; restart the service.')
                conn.execute('SELECT molecule_id FROM molecule LIMIT 1').fetchone()
        except (OSError, ValueError, sqlite3.Error) as exc:
            raise HTTPException(503, 'Catalog snapshot is unavailable; inspect service logs.') from exc
        return {'status': 'ready', 'service': 'catalog', 'snapshot': app.state.snapshot}

    @app.get('/v1/catalog/summary')
    def summary():
        return app.state.summary

    @app.get('/v1/catalog/search')
    def search(q: str = Query(min_length=1, max_length=200), limit: int = Query(default=10, ge=1, le=25)):
        if not q.strip():
            raise HTTPException(422, 'Supply a chemical name, CAS number, ID, or stored canonical SMILES.')
        with closing(catalog.readonly(path)) as conn:
            return {'results': catalog.find(conn, q.strip(), limit)}

    @app.get('/v1/catalog/substances/{dtxsid}')
    def substance(dtxsid: str):
        if len(dtxsid) > 80:
            raise HTTPException(404, 'Substance is absent from this snapshot.')
        with closing(catalog.readonly(path)) as conn:
            try:
                return catalog.show(conn, dtxsid)
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc

    @app.get('/v1/catalog/structure')
    def structure(smiles: str = Query(min_length=1, max_length=4096)):
        # Predictor canonicalizes with the frozen chemistry policy. This endpoint
        # performs only exact lookup, and never invents a source substance identity.
        with closing(catalog.readonly(path)) as conn:
            molecule = conn.execute('SELECT molecule_id FROM molecule WHERE canonical_smiles=?', (smiles,)).fetchone()
            if molecule is None:
                return {'found': False, 'canonical_smiles': smiles, 'substances': [], 'recorded_uses': [],
                        'meaning': 'No exact structure in this snapshot; functions remain unknown.'}
            substances = [dict(r) for r in conn.execute('SELECT dtxsid,preferred_name,casrn FROM substance WHERE molecule_id=? ORDER BY dtxsid', (molecule[0],))]
            uses = catalog.show(conn, substances[0]['dtxsid'])['recorded_uses']
            return {'found': True, 'canonical_smiles': smiles, 'molecule_id': molecule[0],
                    'substances': substances, 'recorded_uses': uses,
                    'meaning': 'Exact structure lookup in the full catalog; not a training-domain claim or identity verification.'}

    return app


app = create_app()
