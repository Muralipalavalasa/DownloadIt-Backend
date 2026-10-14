from uuid import UUID, uuid4

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.user import User
from sqlalchemy.exc import SQLAlchemyError


def test_create_user(test_session_factory) -> None:
    with test_session_factory() as session:
        user = User(email='person@example.test')
        session.add(user)
        session.commit()
        session.refresh(user)

        assert user.id is not None
        assert user.created_at is not None
        assert user.updated_at is not None


def test_create_download_record(client, test_session_factory, create_authenticated_user) -> None:
    _, headers = create_authenticated_user()
    response = client.post(
        '/api/downloads',
        json={
            'source_url': 'https://samplelib.com/mp4/sample-5s.mp4',
            'format': 'mp4',
            'quality': 'source',
        },
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()['status'] == 'pending'
    with test_session_factory() as session:
        from app.models.download import Download

        download = session.get(Download, UUID(response.json()['id']))
        assert download is not None
        assert download.platform == 'direct'
        assert download.title is None
        assert download.file_path is None


def test_get_download_history(client, create_authenticated_user) -> None:
    _, headers = create_authenticated_user()
    created = client.post(
        '/api/downloads',
        json={'source_url': 'https://samplelib.com/mp4/sample-5s.mp4', 'format': 'mp4', 'quality': 'source'},
        headers=headers,
    )
    response = client.get('/api/downloads', headers=headers)

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]['id'] == created.json()['id']
    assert response.json()[0]['platform'] == 'direct'
    assert response.json()[0]['status'] == 'pending'


def test_get_download_by_id_and_status(client, create_authenticated_user) -> None:
    _, headers = create_authenticated_user()
    created = client.post(
        '/api/downloads',
        json={'source_url': 'https://samplelib.com/mp4/sample-5s.mp4'},
        headers=headers,
    )
    response = client.get(f"/api/downloads/{created.json()['id']}", headers=headers)

    assert response.status_code == 200
    assert response.json()['id'] == created.json()['id']
    assert response.json()['status'] == 'pending'
    assert response.json()['platform'] == 'direct'
    assert response.json()['title'] is None


def test_delete_download_history(client, create_authenticated_user) -> None:
    _, headers = create_authenticated_user()
    created = client.post(
        '/api/downloads',
        json={'source_url': 'https://samplelib.com/mp4/sample-5s.mp4'},
        headers=headers,
    )
    response = client.delete(f"/api/downloads/{created.json()['id']}", headers=headers)

    assert response.status_code == 200
    assert response.json() == {'success': True}
    assert client.get('/api/downloads', headers=headers).json() == []


def test_invalid_download_id(client, create_authenticated_user) -> None:
    _, headers = create_authenticated_user()
    response = client.get('/api/downloads/not-a-uuid', headers=headers)
    assert response.status_code == 422


def test_unknown_download_id(client, create_authenticated_user) -> None:
    _, headers = create_authenticated_user()
    response = client.get(f'/api/downloads/{uuid4()}', headers=headers)
    assert response.status_code == 404


def test_invalid_download_url(client, create_authenticated_user) -> None:
    _, headers = create_authenticated_user()
    response = client.post('/api/downloads', json={'source_url': 'not-a-url'}, headers=headers)
    assert response.status_code == 422


def test_youtube_download_requires_authorization(client, create_authenticated_user, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'ytdlp_enabled', True)
    _, headers = create_authenticated_user('youtube-owner@example.com')
    request_body = {
        'source_url': 'https://www.youtube.com/watch?v=sample12345',
        'format': 'mp4',
        'quality': 'best',
    }

    denied = client.post('/api/downloads', json=request_body, headers=headers)
    accepted = client.post(
        '/api/downloads',
        json={**request_body, 'authorized_to_download': True},
        headers=headers,
    )

    assert denied.status_code == 400
    assert denied.json()['detail']['message'] == (
        'Confirm you are authorized to download this media and that the platform permits it.'
    )
    assert accepted.status_code == 201


def test_instagram_reel_job_requires_authorization_and_uses_instagram_platform(
    client,
    create_authenticated_user,
    test_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, 'ytdlp_enabled', True)
    _, headers = create_authenticated_user('instagram-owner@example.com')
    request_body = {
        'source_url': 'https://www.instagram.com/reel/Dd_kq-ths_U/',
        'format': 'mp4',
        'quality': '1080p',
    }

    denied = client.post('/api/downloads', json=request_body, headers=headers)
    accepted = client.post(
        '/api/downloads',
        json={**request_body, 'authorized_to_download': True},
        headers=headers,
    )

    assert denied.status_code == 400
    assert accepted.status_code == 201
    with test_session_factory() as session:
        from app.models.download import Download

        job = session.get(Download, UUID(accepted.json()['id']))
        assert job is not None
        assert job.platform == 'unknown'


def test_database_errors_are_safe_503(client) -> None:
    def unavailable_database():
        raise SQLAlchemyError('private database details')
        yield

    original_override = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = unavailable_database
    try:
        response = client.get('/api/downloads', headers={'Origin': 'http://localhost:5174'})
    finally:
        app.dependency_overrides[get_db] = original_override

    assert response.status_code == 503
    assert response.json()['detail']['database'] == 'unavailable'
    assert 'private database details' not in response.text
    assert response.headers['access-control-allow-origin'] == 'http://localhost:5174'