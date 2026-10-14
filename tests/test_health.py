from sqlalchemy.exc import SQLAlchemyError

from app.core.database import get_db
from app.main import app
from app.providers.base import MediaFormat, MediaMetadata, Platform
from app.providers.direct_file import DirectFileProvider
from app.providers.errors import ProviderUnavailable
from app.providers.registry import ProviderRegistry
from app.services import media_service


class SampleTestProvider:
    platform = Platform.DIRECT
    display_name = 'Mock sample provider'
    requires_authorization = False

    @property
    def is_enabled(self) -> bool:
        return True

    def can_handle(self, _url: str) -> bool:
        return True

    def platform_for(self, _url: str) -> Platform:
        return self.platform

    def validate_url(self, _url: str) -> None:
        return None

    def supports_format(self, _output_format: str, _quality: str) -> bool:
        return True

    def analyze(self, _url: str) -> MediaMetadata:
        return MediaMetadata(
            title='Observed sample.mp4',
            duration=None,
            thumbnail=None,
            available_formats=(MediaFormat('mp4', 'source'),),
            content_type='video/mp4',
            extension='.mp4',
        )

    def download(self, _url: str, _destination) -> None:
        return None


def test_health_endpoint(client) -> None:
    response = client.get('/api/health')
    assert response.status_code == 200
    assert response.json() == {
        'status': 'ok',
        'service': 'DownloadIt API',
        'database': 'connected',
    }


def test_health_reports_database_unavailable(client) -> None:
    class UnavailableSession:
        def execute(self, statement):
            raise SQLAlchemyError('private database details')

    def override_get_db():
        yield UnavailableSession()

    original_override = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get('/api/health')
    finally:
        app.dependency_overrides[get_db] = original_override

    assert response.status_code == 503
    assert response.json()['detail']['database'] == 'unavailable'
    assert 'private database details' not in response.text


def test_valid_url_analysis(client, monkeypatch) -> None:
    monkeypatch.setattr(media_service, 'default_provider_registry', ProviderRegistry([SampleTestProvider()]))
    response = client.post('/api/media/analyze', json={'url': 'https://samplelib.com/mp4/sample-5s.mp4'})
    assert response.status_code == 200
    payload = response.json()
    assert payload['success'] is True
    assert payload['platform'] == 'direct'
    assert payload['title'] == 'Observed sample.mp4'
    assert payload['duration'] is None
    assert payload['thumbnail'] is None
    assert payload['available_formats'][0]['format'] == 'mp4'
    assert payload['available_formats'][0]['quality'] == 'source'
    assert payload['available_formats'][0]['extension'] == '.mp4'


def test_provider_failure_is_mapped_to_safe_response(client, monkeypatch) -> None:
    class UnavailableProvider(SampleTestProvider):
        def analyze(self, _url: str) -> MediaMetadata:
            raise ProviderUnavailable('private host and filesystem details')

    monkeypatch.setattr(media_service, 'default_provider_registry', ProviderRegistry([UnavailableProvider()]))
    response = client.post('/api/media/analyze', json={'url': 'https://samplelib.com/mp4/sample-5s.mp4'})

    assert response.status_code == 503
    assert response.json()['detail']['message'] == 'Media provider is temporarily unavailable.'
    assert 'private host and filesystem details' not in response.text


def test_empty_url_validation(client) -> None:
    response = client.post('/api/media/analyze', json={'url': ''})
    assert response.status_code == 422


def test_invalid_url_validation(client) -> None:
    response = client.post('/api/media/analyze', json={'url': 'not-a-valid-url'})
    assert response.status_code == 422


def test_youtube_is_unsupported_when_ytdlp_provider_is_not_registered(client, monkeypatch) -> None:
    monkeypatch.setattr(media_service, 'default_provider_registry', ProviderRegistry([DirectFileProvider()]))
    response = client.post('/api/media/analyze', json={'url': 'https://youtu.be/abc123'})
    assert response.status_code == 400
    assert response.json()['detail'] == {
        'success': False,
        'message': 'Platform is not currently supported.',
        'platform': 'unknown',
    }


def test_non_reel_instagram_url_is_unsupported(client, monkeypatch) -> None:
    monkeypatch.setattr(media_service, 'default_provider_registry', ProviderRegistry([DirectFileProvider()]))
    response = client.post('/api/media/analyze', json={'url': 'https://www.instagram.com/p/abc123/'})
    assert response.status_code == 400
    assert response.json()['detail']['message'] == 'Platform is not currently supported.'
    assert response.json()['detail']['platform'] == 'unknown'


def test_tiktok_url_is_unsupported(client, monkeypatch) -> None:
    monkeypatch.setattr(media_service, 'default_provider_registry', ProviderRegistry([DirectFileProvider()]))
    response = client.post('/api/media/analyze', json={'url': 'https://www.tiktok.com/@user/video/1234567890'})
    assert response.status_code == 400
    assert response.json()['detail']['platform'] == 'unknown'


def test_x_url_is_unsupported(client, monkeypatch) -> None:
    monkeypatch.setattr(media_service, 'default_provider_registry', ProviderRegistry([DirectFileProvider()]))
    response = client.post('/api/media/analyze', json={'url': 'https://x.com/user/status/1234567890'})
    assert response.status_code == 400
    assert response.json()['detail']['platform'] == 'unknown'


def test_unknown_platform_url(client, monkeypatch) -> None:
    monkeypatch.setattr(media_service, 'default_provider_registry', ProviderRegistry([DirectFileProvider()]))
    response = client.post('/api/media/analyze', json={'url': 'https://example.com/video'})
    assert response.status_code == 400
    assert response.json()['detail']['platform'] == 'unknown'
