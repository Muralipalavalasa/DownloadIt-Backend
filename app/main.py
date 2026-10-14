from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes.health import router as health_router
from app.api.routes.auth import router as auth_router
from app.api.routes.downloads import router as downloads_router
from app.api.routes.media import router as media_router
from app.core.config import settings

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description='Backend API for DownloadIt URL analysis and metadata discovery.',
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, 'http://localhost:5173', 'http://localhost:5174', 'http://127.0.0.1:5173', 'http://127.0.0.1:5174'],
    allow_origin_regex=r'https?://(localhost|127\.0\.0\.1):\d+',
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(_request: Request, _exc: SQLAlchemyError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={'detail': {'status': 'unavailable', 'database': 'unavailable'}},
    )

app.include_router(health_router, prefix='/api')
app.include_router(auth_router, prefix='/api/auth', tags=['auth'])
app.include_router(downloads_router, prefix='/api')
app.include_router(media_router, prefix='/api')


@app.get('/')
def root() -> dict:
    return {'service': settings.app_name, 'version': settings.app_version}
