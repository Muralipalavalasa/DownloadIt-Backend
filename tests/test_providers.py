from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import socket

from app.core import network_security
from app.providers.base import MediaFormat, MediaMetadata, Platform
from app.providers.direct_file import DirectFileProvider
from app.providers.direct_file import DirectFileProvider
from app.providers.errors import (
    InvalidMediaUrl,
    MediaNotDownloadable,
    ProviderUnavailable,
    UnsupportedPlatform,
    to_http_exception,
)
from app.providers.registry import ProviderRegistry, default_provider_registry

SAMPLE_URL = 'https://samplelib.com/mp4/sample-5s.mp4'


class FakeResponse:
    def __init__(self, status_code: int = 200, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {'content-type': 'video/mp4', 'content-length': '4'}

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def iter_bytes(self, _chunk_size: int):
        yield b'mp4!'


class FakeClient:
    def __init__(self, response: FakeResponse | None = None) -> None:
        self.response = response or FakeResponse()

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def head(self, _url: str) -> FakeResponse:
        return self.response

    def stream(self, _method: str, _url: str) -> FakeResponse:
        return self.response


class FutureProvider:
    platform = Platform.UNKNOWN
    display_name = 'Future test provider'

    @property
    def is_enabled(self) -> bool:
        return True

    def can_handle(self, url: str) -> bool:
        return 'future.example' in url

    def validate_url(self, _url: str) -> None:
        return None

    def supports_format(self, _output_format: str, _quality: str) -> bool:
        return True

    def analyze(self, _url: str) -> MediaMetadata:
        return MediaMetadata('Future item', None, None, (MediaFormat('mp4', 'source'),), 'video/mp4', '.mp4')

    def download(self, _url: str, destination: Path) -> None:
        destination.write_bytes(b'mp4!')


def test_provider_registry_selects_direct_file_provider() -> None:
    provider = default_provider_registry.get_provider(SAMPLE_URL)
    assert isinstance(provider, DirectFileProvider)
    assert provider.platform == Platform.DIRECT


def test_registry_supports_coexisting_future_providers() -> None:
    future = FutureProvider()
    registry = ProviderRegistry([DirectFileProvider(), future])
    assert registry.get_provider('https://future.example/video').display_name == 'Future test provider'
    assert registry.get_provider(SAMPLE_URL).platform == Platform.DIRECT


def test_direct_only_registry_rejects_unhandled_url_cleanly() -> None:
    registry = ProviderRegistry([DirectFileProvider()])
    with pytest.raises(UnsupportedPlatform) as error:
        registry.get_provider('https://www.youtube.com/watch?v=example')
    assert error.value.platform == 'unknown'
    assert str(error.value) == 'Platform is not currently supported.'


@pytest.mark.parametrize(
    ('address', 'blocked'),
    [('10.0.0.8', True), ('93.184.216.34', False)],
)
def test_public_network_guard_checks_connection_time_dns(monkeypatch, address: str, blocked: bool) -> None:
    answer = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (address, 443))]
    monkeypatch.setattr(network_security, '_original_getaddrinfo', lambda *_args, **_kwargs: answer)

    with network_security.public_network_dns_guard():
        if blocked:
            with pytest.raises(socket.gaierror):
                socket.getaddrinfo('public.example', 443)
        else:
            assert socket.getaddrinfo('public.example', 443) == answer


def test_registry_does_not_claim_yt_dlp_extractor_support_ahead_of_analysis() -> None:
    direct_registry = ProviderRegistry([DirectFileProvider()])
    assert Platform.INSTAGRAM not in direct_registry.supported_platforms


def test_direct_provider_rejects_http_url() -> None:
    provider = DirectFileProvider()
    with pytest.raises(InvalidMediaUrl):
        provider.validate_url('http://samplelib.com/mp4/sample-5s.mp4')


def test_direct_provider_rejects_non_allowlisted_host() -> None:
    registry = ProviderRegistry([DirectFileProvider()])
    with pytest.raises(UnsupportedPlatform):
        registry.get_provider('https://evil.example/mp4/video.mp4')


def test_direct_provider_rejects_non_mp4_path() -> None:
    provider = DirectFileProvider()
    with pytest.raises(MediaNotDownloadable):
        provider.validate_url('https://samplelib.com/mp4/video.m3u8')


def test_direct_provider_analyzes_only_available_metadata() -> None:
    provider = DirectFileProvider(http_client_factory=lambda **_kwargs: FakeClient())
    metadata = provider.analyze(SAMPLE_URL)
    assert metadata.title == 'sample-5s.mp4'
    assert metadata.duration is None
    assert metadata.thumbnail is None
    assert metadata.available_formats == (MediaFormat('mp4', 'source', filesize=4),)


def test_direct_provider_downloads_to_caller_generated_path(tmp_path) -> None:
    provider = DirectFileProvider(http_client_factory=lambda **_kwargs: FakeClient())
    destination = tmp_path / str(uuid4()) / f'{uuid4().hex}.mp4'
    provider.download(SAMPLE_URL, destination)
    assert destination.read_bytes() == b'mp4!'


def test_direct_provider_rejects_redirects() -> None:
    provider = DirectFileProvider(
        http_client_factory=lambda **_kwargs: FakeClient(
            FakeResponse(status_code=302, headers={'location': 'https://other.example/file.mp4'})
        )
    )
    with pytest.raises(MediaNotDownloadable, match='Redirects are not enabled'):
        provider.analyze(SAMPLE_URL)


def test_direct_provider_maps_network_errors_safely() -> None:
    class UnavailableClient(FakeClient):
        def head(self, _url: str) -> FakeResponse:
            raise httpx.ConnectError('private host details')

    provider = DirectFileProvider(http_client_factory=lambda **_kwargs: UnavailableClient())
    with pytest.raises(ProviderUnavailable) as error:
        provider.analyze(SAMPLE_URL)
    response = to_http_exception(error.value)
    assert response.status_code == 503
    assert 'private host details' not in str(response.detail)