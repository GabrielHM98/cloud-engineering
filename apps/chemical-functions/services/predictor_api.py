"""HTTP adapter for the saved model; no fitting takes place during startup."""
from contextlib import asynccontextmanager
import os
from fastapi import FastAPI, HTTPException
from services.common import BodyLimit, PredictionRequest
from services.model_runtime import Predictor, UnsupportedQuery


def create_app(run_dir=None):
    path = run_dir or os.environ.get('MODEL_RUN_DIR', 'runs/benchmark-v1')

    @asynccontextmanager
    async def lifespan(app):
        app.state.predictor = Predictor(path)
        yield
        del app.state.predictor

    app = FastAPI(title='Chemical function predictor', version='0.1.0', lifespan=lifespan)
    app.add_middleware(BodyLimit)

    @app.get('/healthz')
    def health():
        return {'status': 'alive', 'service': 'predictor'}

    @app.get('/readyz')
    def ready():
        if not hasattr(app.state, 'predictor'):
            raise HTTPException(503, 'Model is not loaded.')
        return {'status': 'ready', 'service': 'predictor', 'model_sha256': app.state.predictor.model_sha256}

    @app.get('/v1/model')
    def info():
        return app.state.predictor.info()

    @app.post('/v1/predict')
    def predict(body: PredictionRequest):
        try:
            return app.state.predictor.predict(body.smiles, body.top_k)
        except UnsupportedQuery as exc:
            raise HTTPException(422, str(exc)) from exc

    return app


app = create_app()
