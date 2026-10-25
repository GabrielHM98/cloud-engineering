"""Small request contract shared by the web gateway and predictor."""
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.responses import JSONResponse


class PredictionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    smiles: str = Field(min_length=1, max_length=4096, strict=True)
    top_k: int = Field(default=5, ge=1, le=9, strict=True)

    @field_validator('smiles')
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Supply a SMILES string.')
        return value


class BodyLimit:
    """Bound request bodies even when a client uses chunked transfer."""
    def __init__(self, app, limit=8192):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            event = await receive()
            if event['type'] == 'http.disconnect':
                return
            body.extend(event.get('body', b''))
            if len(body) > self.limit:
                return await JSONResponse({'detail': 'Request body exceeds 8192 bytes.'}, status_code=413)(scope, receive, send)
            if not event.get('more_body', False):
                break
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()
        await self.app(scope, replay, send)
