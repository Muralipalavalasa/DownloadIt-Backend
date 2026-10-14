import builtins
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from app.core.config import settings
from app.main import app
from app.providers.base import Platform
from app.providers.errors import FFmpegUnavailable, MediaNotDownloadable, ProviderUnavailable, RequestedQualityUnavailable, UnsupportedPlatform
from app.providers.errors import InvalidMediaUrl
from app.providers.ytdlp_provider import YtDlpProvider
from app.providers.registry import default_provider_registry
import app.providers.ytdlp_provider as ytdlp_module

YOUTUBE_URL = 'https://www.youtube.com/watch?v=sample12345'

VIDEO_INFO = {
    'id': 'sample12345',
    'title': 'Observed public test video',
    'duration': 65,
    'thumbnail': 'https://i.ytimg.com/vi/sample12345/default.jpg',
    'uploader': 'Test channel',
    'extractor_key': 'Youtube',
    'webpage_url': YOUTUBE_URL,
    'formats': [
        {
            'format_id': 'muxed720',
            'ext': 'mp4',
            'height': 720,
            'vcodec': 'avc1',
            'acodec': 'mp4a',
            'filesize': 4000,
        },
        {
            'format_id': 'video1080',
            'ext': 'mp4',
            'height': 1080,
            'vcodec': 'avc1',
            'acodec': 'none',
            'filesize': 8000,
        },
        {
            'format_id': 'audio',
            'ext': 'm4a',
            'vcodec': 'none',
            'acodec': 'mp4a',
            'filesize': 2000,
        },
    ],
}


class FakeYoutubeDL:
    instances = []
    info = VIDEO_INFO
    error: Exception | None = None
    output_bytes = b'fake-mp4-content'
    progress_events: list[dict] = []

    def __init__(self, options: dict) -> None:
        self.options = options
        self.calls: list[tuple[str, bool]] = []
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def extract_info(self, url: str, download: bool = False):
        self.calls.append((url, download))
        if self.error is not None:
            raise self.error
        if download:
            output_template = self.options['outtmpl']
            ext = 'm4a' if '[ext=m4a]' in self.options.get('format', '') else 'mp4'
            output_path = Path(output_template.replace('%(ext)s', ext))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(self.output_bytes)
            for event in self.progress_events:
                for hook in self.options.get('progress_hooks', []):
                    hook(event)
        return dict(self.info)


@pytest.fixture(autouse=True)
def reset_fake_ytdlp():
    FakeYoutubeDL.instances = []
    FakeYoutubeDL.info = VIDEO_INFO
    FakeYoutubeDL.error = None
    FakeYoutubeDL.output_bytes = b'fake-mp4-content'
    FakeYoutubeDL.progress_events = []


def provider(monkeypatch, ffmpeg_path='/usr/local/bin/ffmpeg') -> YtDlpProvider:
    monkeypatch.setattr(settings, 'ytdlp_enabled', True)
    monkeypatch.setattr(
        ytdlp_module.socket,
        'getaddrinfo',
        lambda _host, port, **_kwargs: [(2, 1, 6, '', ('93.184.216.34', port))],
    )
    monkeypatch.setattr(
        YtDlpProvider,
        '_load_ytdlp',
        staticmethod(lambda: (FakeYoutubeDL, DownloadError)),
    )
    monkeypatch.setattr(ytdlp_module.shutil, 'which', lambda _name: ffmpeg_path)
    return YtDlpProvider()


def test_provider_recognizes_generic_public_http_urls(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'ytdlp_enabled', True)
    ytdlp = YtDlpProvider()
    for url in (
        'https://youtube.com/watch?v=abc',
        'https://www.instagram.com/reel/example-id/',
        'https://reddit.com/r/example/video/abc',
        'http://media.example/video',
    ):
        assert ytdlp.can_handle(url)


def test_ytdlp_provider_registry_is_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'ytdlp_enabled', False)
    with pytest.raises(UnsupportedPlatform):
        default_provider_registry.get_provider(YOUTUBE_URL)
    monkeypatch.setattr(settings, 'ytdlp_enabled', True)
    monkeypatch.setattr(
        ytdlp_module.socket,
        'getaddrinfo',
        lambda _host, port, **_kwargs: [(2, 1, 6, '', ('93.184.216.34', port))],
    )
    assert isinstance(default_provider_registry.get_provider(YOUTUBE_URL), YtDlpProvider)


def test_enabled_provider_without_optional_dependency_fails_safely(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'ytdlp_enabled', True)
    original_import = builtins.__import__

    def import_without_ytdlp(name, *args, **kwargs):
        if name == 'yt_dlp' or name.startswith('yt_dlp.'):
            raise ImportError('internal import detail')
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', import_without_ytdlp)
    with pytest.raises(ProviderUnavailable) as error:
        YtDlpProvider().analyze(YOUTUBE_URL)
    assert str(error.value) == 'The yt-dlp provider is not installed.'


def test_analyze_api_uses_ytdlp_provider_and_returns_real_metadata(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'ytdlp_enabled', True)
    monkeypatch.setattr(
        ytdlp_module.socket,
        'getaddrinfo',
        lambda _host, port, **_kwargs: [(2, 1, 6, '', ('93.184.216.34', port))],
    )
    monkeypatch.setattr(
        YtDlpProvider,
        '_load_ytdlp',
        staticmethod(lambda: (FakeYoutubeDL, DownloadError)),
    )
    response = client.post('/api/media/analyze', json={'url': YOUTUBE_URL})

    assert response.status_code == 200
    payload = response.json()
    assert payload['platform'] == 'youtube'
    assert payload['title'] == 'Observed public test video'
    assert payload['uploader'] == 'Test channel'
    assert payload['extractor'] == 'Youtube'
    assert payload['requires_authorization'] is True
    assert payload['duration'] == '0:01:05'
    assert any(item['quality'] == '1080p' for item in payload['available_formats'])


def test_unextractable_generic_url_returns_safe_unsupported_response(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'ytdlp_enabled', True)
    monkeypatch.setattr(
        YtDlpProvider,
        '_load_ytdlp',
        staticmethod(lambda: (FakeYoutubeDL, DownloadError)),
    )
    FakeYoutubeDL.error = DownloadError('Unsupported URL: private extractor details')

    response = client.post('/api/media/analyze', json={'url': 'https://future.example/video'})

    assert response.status_code == 400
    assert response.json()['detail']['message'] == 'Platform is not currently supported.'
    assert 'private extractor details' not in response.text


INSTAGRAM_URL = 'https://www.instagram.com/reel/Dd_kq-ths_U/'


def test_instagram_reel_recognition_and_registry_selection(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'ytdlp_enabled', True)
    monkeypatch.setattr(
        ytdlp_module.socket,
        'getaddrinfo',
        lambda _host, port, **_kwargs: [(2, 1, 6, '', ('93.184.216.34', port))],
    )
    provider_instance = YtDlpProvider()

    assert provider_instance.can_handle(INSTAGRAM_URL)
    assert provider_instance.platform_for(INSTAGRAM_URL) == Platform.UNKNOWN
    assert isinstance(default_provider_registry.get_provider(INSTAGRAM_URL), YtDlpProvider)
    assert Platform.INSTAGRAM not in default_provider_registry.supported_platforms


def test_unknown_supported_extractor_is_detected_dynamically(monkeypatch) -> None:
    ytdlp = provider(monkeypatch)
    reddit_url = 'https://reddit.com/r/sample/video/abc'
    FakeYoutubeDL.info = {
        **VIDEO_INFO,
        'webpage_url': reddit_url,
        'extractor_key': 'Reddit',
        'extractor': 'reddit',
    }

    metadata = ytdlp.analyze(reddit_url)

    assert metadata.platform == 'reddit'
    assert metadata.extractor == 'Reddit'


def test_instagram_portrait_formats_map_to_short_edge_quality(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'ytdlp_enabled', True)
    ytdlp = provider(monkeypatch)
    FakeYoutubeDL.info = {
        **VIDEO_INFO,
        'webpage_url': INSTAGRAM_URL,
        'formats': [
            {'ext': 'mp4', 'width': 720, 'height': 1280, 'vcodec': 'avc1', 'acodec': 'none'},
            {'ext': 'mp4', 'width': 1080, 'height': 1920, 'vcodec': 'avc1', 'acodec': 'none'},
            {'ext': 'm4a', 'vcodec': 'none', 'acodec': 'mp4a', 'filesize': 1024},
        ],
    }

    metadata = ytdlp.analyze(INSTAGRAM_URL)
    supported = {(item.quality, item.resolution) for item in metadata.available_formats}
    selected = ytdlp.select_format(metadata, 'mp4', '720p')

    assert ('720p', '720x1280') in supported
    assert ('1080p', '1080x1920') in supported
    assert selected.provider_data.selector == 'bestvideo[width=720][height=1280][ext=mp4]+bestaudio[ext=m4a]'


def test_provider_attempts_generic_public_site_urls(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'ytdlp_enabled', True)
    provider_instance = YtDlpProvider()

    assert provider_instance.can_handle('https://instagram.com/reel/example-id')
    assert provider_instance.can_handle('https://www.instagram.com/p/example-id/')
    assert provider_instance.can_handle('https://www.instagram.com/stories/user/123/')
    assert provider_instance.can_handle('https://future.example/watch/item')


def test_instagram_analyze_api_returns_provider_platform_and_metadata(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'ytdlp_enabled', True)
    monkeypatch.setattr(
        ytdlp_module.socket,
        'getaddrinfo',
        lambda _host, port, **_kwargs: [(2, 1, 6, '', ('93.184.216.34', port))],
    )
    monkeypatch.setattr(
        YtDlpProvider,
        '_load_ytdlp',
        staticmethod(lambda: (FakeYoutubeDL, DownloadError)),
    )
    FakeYoutubeDL.info = {**VIDEO_INFO, 'webpage_url': INSTAGRAM_URL, 'extractor_key': 'Instagram'}
    response = client.post('/api/media/analyze', json={'url': INSTAGRAM_URL})

    assert response.status_code == 200
    payload = response.json()
    assert payload['platform'] == 'instagram'
    assert payload['title'] == 'Observed public test video'
    assert payload['uploader'] == 'Test channel'
    assert payload['extractor'] == 'Instagram'
    assert payload['requires_authorization'] is True
    assert any(item['resolution'] == '1080p' for item in payload['available_formats'])


def test_ytdlp_provider_rejects_local_private_but_accepts_public_platform_urls(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'ytdlp_enabled', True)
    monkeypatch.setattr(
        ytdlp_module.socket,
        'getaddrinfo',
        lambda _host, port, **_kwargs: [(2, 1, 6, '', ('93.184.216.34', port))],
    )
    ytdlp = YtDlpProvider()
    for url in ('file:///etc/passwd', 'ftp://www.youtube.com/video'):
        with pytest.raises(InvalidMediaUrl):
            ytdlp.validate_url(url)
    for url in (
        'http://localhost/video',
        'http://127.0.0.1/video',
        'http://192.168.1.2/video',
    ):
        with pytest.raises(InvalidMediaUrl):
            ytdlp.validate_url(url)
    ytdlp.validate_url('https://www.instagram.com/reel/example')
    ytdlp.validate_url('https://reddit.com/r/example/video/abc')


def test_public_hostname_resolving_to_private_ip_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'ytdlp_enabled', True)
    monkeypatch.setattr(
        ytdlp_module.socket,
        'getaddrinfo',
        lambda _host, port, **_kwargs: [(2, 1, 6, '', ('10.0.0.12', port))],
    )
    with pytest.raises(InvalidMediaUrl):
        YtDlpProvider().validate_url('https://public.example/video')


def test_unsupported_extractor_error_maps_cleanly(monkeypatch) -> None:
    ytdlp = provider(monkeypatch)
    FakeYoutubeDL.error = DownloadError('Unsupported URL: private internal diagnostics')
    url = 'https://public.example/not-supported'

    with pytest.raises(UnsupportedPlatform) as error:
        ytdlp.analyze(url)

    assert str(error.value) == 'Platform is not currently supported.'
    assert 'private internal diagnostics' not in str(error.value)


def test_analyze_extracts_real_metadata_without_downloading(monkeypatch) -> None:
    ytdlp = provider(monkeypatch)
    metadata = ytdlp.analyze(YOUTUBE_URL)

    assert metadata.title == 'Observed public test video'
    assert metadata.duration == '0:01:05'
    assert metadata.thumbnail == VIDEO_INFO['thumbnail']
    assert metadata.uploader == 'Test channel'
    assert metadata.platform == 'youtube'
    assert metadata.webpage_url == YOUTUBE_URL
    assert {item.quality for item in metadata.available_formats} >= {'best', '720p', '1080p', 'audio'}
    analysis_call = FakeYoutubeDL.instances[-1]
    assert analysis_call.calls == [(YOUTUBE_URL, False)]
    assert analysis_call.options['skip_download'] is True
    assert analysis_call.options['noplaylist'] is True
    assert not {'cookiefile', 'cookiesfrombrowser', 'http_headers'} & analysis_call.options.keys()
    assert analysis_call.options['proxy'] == ''


def test_quality_selector_is_server_owned_and_format_specific(monkeypatch) -> None:
    ytdlp = provider(monkeypatch)
    metadata = ytdlp.analyze(YOUTUBE_URL)
    selected = ytdlp.select_format(metadata, 'mp4', '1080p')

    assert selected.format == 'mp4'
    assert selected.quality == '1080p'
    assert selected.provider_data.selector == 'bestvideo[height=1080][ext=mp4]+bestaudio[ext=m4a]'
    assert ytdlp.supports_format('mp4', 'arbitrary format selector') is False


def test_audio_quality_uses_server_owned_m4a_selector(monkeypatch) -> None:
    ytdlp = provider(monkeypatch)
    metadata = ytdlp.analyze(YOUTUBE_URL)
    selected = ytdlp.select_format(metadata, 'm4a', 'audio')

    assert selected.format == 'm4a'
    assert selected.quality == 'audio'
    assert selected.provider_data.selector == 'bestaudio[ext=m4a]'


def test_invalid_quality_is_rejected(monkeypatch) -> None:
    ytdlp = provider(monkeypatch)
    with pytest.raises(RequestedQualityUnavailable):
        ytdlp.validate_download_options('mp4', 'bestvideo+bestaudio')


def test_quality_is_rejected_when_not_reported_by_video(monkeypatch) -> None:
    ytdlp = provider(monkeypatch)
    metadata = ytdlp.analyze(YOUTUBE_URL)
    with pytest.raises(RequestedQualityUnavailable):
        ytdlp.select_format(metadata, 'mp4', '360p')


def test_analysis_filters_advertised_formats_over_size_limit(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'download_max_bytes', 5000)
    ytdlp = provider(monkeypatch)
    metadata = ytdlp.analyze(YOUTUBE_URL)
    qualities = {item.quality for item in metadata.available_formats}

    assert '720p' in qualities
    assert '1080p' not in qualities
    assert 'best' in qualities


def test_private_or_auth_required_video_is_safe_error(monkeypatch) -> None:
    ytdlp = provider(monkeypatch)
    FakeYoutubeDL.error = DownloadError('ERROR: Sign in to confirm you are not a bot; private internal detail')

    with pytest.raises(MediaNotDownloadable) as error:
        ytdlp.analyze(YOUTUBE_URL)

    assert str(error.value) == 'This media is unavailable or requires authentication.'
    assert 'private internal detail' not in str(error.value)


def test_ffmpeg_is_required_for_separate_streams(monkeypatch) -> None:
    ytdlp = provider(monkeypatch, ffmpeg_path=None)
    FakeYoutubeDL.info = {
        **VIDEO_INFO,
        'formats': [VIDEO_INFO['formats'][1], VIDEO_INFO['formats'][2]],
    }

    metadata = ytdlp.analyze(YOUTUBE_URL)
    assert any(item.quality == 'audio' for item in metadata.available_formats)
    with pytest.raises(FFmpegUnavailable):
        ytdlp.select_format(metadata, 'mp4', '1080p')


def test_download_uses_generated_template_noplaylist_and_progress(monkeypatch, tmp_path) -> None:
    ytdlp = provider(monkeypatch)
    metadata = ytdlp.analyze(YOUTUBE_URL)
    selected = ytdlp.select_format(metadata, 'mp4', '720p')
    destination = tmp_path / 'job-id' / 'generated-file.mp4'
    progress: list[tuple[int, int | None]] = []
    FakeYoutubeDL.progress_events = [{'status': 'downloading', 'downloaded_bytes': 8, 'total_bytes': 16}]

    ytdlp.download(YOUTUBE_URL, destination, selected, lambda downloaded, total: progress.append((downloaded, total)))

    download_call = FakeYoutubeDL.instances[-1]
    assert download_call.calls == [(YOUTUBE_URL, True)]
    assert download_call.options['noplaylist'] is True
    assert download_call.options['skip_download'] is False
    assert download_call.options['max_filesize'] == settings.download_max_bytes
    assert Path(download_call.options['outtmpl']).parent == destination.parent
    assert destination.read_bytes() == FakeYoutubeDL.output_bytes
    assert progress == [(8, 16)]


def test_missing_ffmpeg_maps_to_application_error(monkeypatch) -> None:
    ytdlp = provider(monkeypatch)
    metadata = ytdlp.analyze(YOUTUBE_URL)
    selected = ytdlp.select_format(metadata, 'mp4', '1080p')
    monkeypatch.setattr(ytdlp_module.shutil, 'which', lambda _name: None)

    with pytest.raises(FFmpegUnavailable):
        ytdlp.download(YOUTUBE_URL, Path('/tmp/generated.mp4'), selected)


def test_oversized_progress_cleans_partial_output(monkeypatch, tmp_path) -> None:
    ytdlp = provider(monkeypatch)
    metadata = ytdlp.analyze(YOUTUBE_URL)
    selected = ytdlp.select_format(metadata, 'mp4', '720p')
    destination = tmp_path / 'job-id' / 'generated-file.mp4'
    monkeypatch.setattr(settings, 'download_max_bytes', 5)
    FakeYoutubeDL.progress_events = [{'status': 'downloading', 'downloaded_bytes': 6, 'total_bytes': 10}]

    with pytest.raises(MediaNotDownloadable):
        ytdlp.download(YOUTUBE_URL, destination, selected)
    assert list(destination.parent.iterdir()) == []