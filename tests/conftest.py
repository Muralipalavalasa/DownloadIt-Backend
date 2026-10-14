import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import Base, get_db
from app.main import app


@pytest.fixture
def test_session_factory():
    engine = create_engine(
        'sqlite+pysqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def client(test_session_factory):
    def override_get_db():
        with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def disable_real_download_workers(monkeypatch):
    from app.api.routes import downloads as downloads_route

    monkeypatch.setattr(
        downloads_route.download_job_queue,
        'enqueue',
        lambda _background_tasks, _download_id: None,
    )


@pytest.fixture(autouse=True)
def mock_ytdlp_dns(monkeypatch):
    from app.core.config import settings
    import socket

    from app.core import network_security

    def resolve_public_test_host(_host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('93.184.216.34', port))]

    monkeypatch.setattr(network_security, '_original_getaddrinfo', resolve_public_test_host)
    monkeypatch.setattr(settings, 'ytdlp_enabled', False)


@pytest.fixture
def create_authenticated_user(client):
    def create_user(email: str = 'person@example.com', password: str = 'StrongPassword123!'):
        registration = client.post(
            '/api/auth/register',
            json={'email': email, 'password': password},
        )
        assert registration.status_code == 201
        login = client.post('/api/auth/login', json={'email': email, 'password': password})
        assert login.status_code == 200
        return registration.json(), {'Authorization': f"Bearer {login.json()['access_token']}"}

    return create_user
