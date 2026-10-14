from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt

from app.core.config import settings
from app.models.user import User


def test_register_valid_user(client, test_session_factory) -> None:
    response = client.post(
        '/api/auth/register',
        json={'email': 'User@Example.com', 'password': 'StrongPassword123!'},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload['email'] == 'user@example.com'
    assert payload['is_active'] is True
    assert 'password_hash' not in payload
    with test_session_factory() as session:
        user = session.get(User, UUID(payload['id']))
        assert user is not None
        assert user.password_hash is not None
        assert user.password_hash.startswith('$argon2id$')
        assert user.password_hash != 'StrongPassword123!'


def test_register_duplicate_email_is_case_insensitive(client) -> None:
    body = {'email': 'user@example.com', 'password': 'StrongPassword123!'}
    assert client.post('/api/auth/register', json=body).status_code == 201
    duplicate = client.post(
        '/api/auth/register',
        json={'email': 'USER@EXAMPLE.COM', 'password': 'StrongPassword123!'},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()['detail'] == 'An account with this email already exists.'


def test_register_invalid_email(client) -> None:
    response = client.post(
        '/api/auth/register',
        json={'email': 'not-an-email', 'password': 'StrongPassword123!'},
    )
    assert response.status_code == 422


def test_register_weak_password(client) -> None:
    response = client.post(
        '/api/auth/register',
        json={'email': 'user@example.com', 'password': 'weak'},
    )
    assert response.status_code == 422


def test_login_correct_credentials(client) -> None:
    client.post(
        '/api/auth/register',
        json={'email': 'user@example.com', 'password': 'StrongPassword123!'},
    )
    response = client.post(
        '/api/auth/login',
        json={'email': 'USER@example.com', 'password': 'StrongPassword123!'},
    )
    assert response.status_code == 200
    assert response.json()['token_type'] == 'bearer'
    assert len(response.json()['access_token']) > 20


def test_login_wrong_and_unknown_credentials_share_error(client) -> None:
    client.post(
        '/api/auth/register',
        json={'email': 'user@example.com', 'password': 'StrongPassword123!'},
    )
    wrong_password = client.post(
        '/api/auth/login',
        json={'email': 'user@example.com', 'password': 'WrongPassword123!'},
    )
    unknown_email = client.post(
        '/api/auth/login',
        json={'email': 'unknown@example.com', 'password': 'WrongPassword123!'},
    )
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


def test_current_user_endpoint(client, create_authenticated_user) -> None:
    registered, headers = create_authenticated_user()
    response = client.get('/api/auth/me', headers=headers)

    assert response.status_code == 200
    assert response.json()['id'] == registered['id']
    assert response.json()['email'] == registered['email']
    assert response.json()['is_active'] is True
    assert 'password_hash' not in response.json()


def test_current_user_requires_token(client) -> None:
    response = client.get('/api/auth/me')
    assert response.status_code == 401
    assert response.headers['www-authenticate'] == 'Bearer'


def test_current_user_rejects_invalid_token(client) -> None:
    response = client.get('/api/auth/me', headers={'Authorization': 'Bearer invalid.token.value'})
    assert response.status_code == 401


def test_current_user_rejects_expired_token(client, create_authenticated_user) -> None:
    registered, _ = create_authenticated_user()
    expired_token = jwt.encode(
        {
            'sub': registered['id'],
            'exp': datetime.now(timezone.utc) - timedelta(seconds=1),
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    response = client.get('/api/auth/me', headers={'Authorization': f'Bearer {expired_token}'})
    assert response.status_code == 401


def test_download_history_requires_authentication(client) -> None:
    response = client.get('/api/downloads')
    assert response.status_code == 401


def test_users_cannot_read_or_delete_another_users_download(client, create_authenticated_user) -> None:
    _, owner_headers = create_authenticated_user('owner@example.com')
    _, other_headers = create_authenticated_user('other@example.com')
    created = client.post(
        '/api/downloads',
        json={'source_url': 'https://samplelib.com/mp4/sample-5s.mp4'},
        headers=owner_headers,
    )
    download_id = created.json()['id']

    read_response = client.get(f'/api/downloads/{download_id}', headers=other_headers)
    delete_response = client.delete(f'/api/downloads/{download_id}', headers=other_headers)

    assert read_response.status_code == 404
    assert read_response.json()['detail'] == 'Download not found.'
    assert delete_response.status_code == 404
    assert client.get(f'/api/downloads/{download_id}', headers=owner_headers).status_code == 200


def test_user_history_contains_only_their_downloads(client, create_authenticated_user) -> None:
    _, first_headers = create_authenticated_user('first@example.com')
    _, second_headers = create_authenticated_user('second@example.com')
    first_job = client.post(
        '/api/downloads',
        json={'source_url': 'https://samplelib.com/mp4/first-item.mp4'},
        headers=first_headers,
    )
    second_job = client.post(
        '/api/downloads',
        json={'source_url': 'https://samplelib.com/mp4/second-item.mp4'},
        headers=second_headers,
    )

    first_history = client.get('/api/downloads', headers=first_headers).json()
    second_history = client.get('/api/downloads', headers=second_headers).json()

    assert [item['id'] for item in first_history] == [first_job.json()['id']]
    assert [item['id'] for item in second_history] == [second_job.json()['id']]