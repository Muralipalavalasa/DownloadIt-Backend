import os
from pathlib import Path
from urllib.parse import unquote, urlsplit
from typing import Callable

import httpx

from app.core.config import settings
from app.core.network_security import public_network_dns_guard
from app.providers.base import MediaFormat, MediaMetadata, Platform, ProgressCallback
from app.providers.errors import (
    InvalidMediaUrl,
    MediaNotDownloadable,
    ProviderUnavailable,
    UnsupportedPlatform,
)


class DirectFileProvider:
    platform = Platform.DIRECT
    display_name = 'Direct MP4'
    requires_authorization = False
    _content_extensions = {'video/mp4': '.mp4'}

    def __init__(
        self,
        http_client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self._http_client_factory = http_client_factory

    @property
    def is_enabled(self) -> bool:
        return bool(settings.allowed_download_hosts)

    @property
    def supported_platforms(self) -> tuple[Platform, ...]:
        return (Platform.DIRECT,) if self.is_enabled else ()

    def can_handle(self, url: str) -> bool:
        try:
            parsed = urlsplit(url)
            hostname = (parsed.hostname or '').lower().rstrip('.')
            port = parsed.port
        except ValueError:
            return False
        return (
            parsed.scheme == 'https'
            and hostname in settings.allowed_download_hosts
            and port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and Path(parsed.path).suffix.lower() == '.mp4'
        )

    def platform_for(self, _url: str) -> Platform:
        return Platform.DIRECT

    def validate_url(self, url: str) -> None:
        try:
            parsed = urlsplit(url)
            hostname = (parsed.hostname or '').lower().rstrip('.')
            port = parsed.port
        except ValueError as exc:
            raise InvalidMediaUrl('Enter a valid media URL.') from exc

        if parsed.scheme != 'https' or not hostname:
            raise InvalidMediaUrl('Media URLs must use HTTPS.')
        if hostname not in settings.allowed_download_hosts:
            raise UnsupportedPlatform()
        if parsed.username is not None or parsed.password is not None or port not in (None, 443):
            raise InvalidMediaUrl('This media URL is not valid.')
        if Path(parsed.path).suffix.lower() != '.mp4':
            raise MediaNotDownloadable('Only direct MP4 files are currently supported.')

    def select_format(self, metadata: MediaMetadata, output_format: str, quality: str) -> MediaFormat:
        selected = next(
            (item for item in metadata.available_formats if item.format == output_format and item.quality == quality),
            None,
        )
        if selected is None:
            raise MediaNotDownloadable('The selected format is not available from this provider.')
        return selected

    def supports_format(self, output_format: str, quality: str) -> bool:
        return output_format.lower() == 'mp4' and quality.lower() == 'source'

    def validate_download_options(self, output_format: str, quality: str) -> None:
        if not self.supports_format(output_format, quality):
            raise MediaNotDownloadable('The selected format is not available from this provider.')

    def validate_selection(self, url: str, output_format: str, quality: str) -> None:
        self.validate_url(url)
        self.validate_download_options(output_format, quality)

    def analyze(self, url: str) -> MediaMetadata:
        self.validate_url(url)
        parsed = urlsplit(url)
        title = unquote(Path(parsed.path).name) or None
        try:
            with self._http_client_factory(timeout=15, follow_redirects=False) as client:
                with public_network_dns_guard():
                    response = client.head(url)
                self._validate_response(response.status_code)
                content_type = response.headers.get('content-type', '').split(';', 1)[0].lower()
                extension = self._content_extensions.get(content_type)
                if extension is None:
                    raise MediaNotDownloadable('The source is not a supported MP4 file.')
                content_length = response.headers.get('content-length')
                self._validate_content_length(content_length)
        except (ProviderUnavailable, MediaNotDownloadable):
            raise
        except httpx.HTTPError as exc:
            raise ProviderUnavailable('The media provider is temporarily unavailable.') from exc

        return MediaMetadata(
            title=title,
            duration=None,
            thumbnail=None,
            available_formats=(MediaFormat(format='mp4', quality='source', filesize=int(content_length) if content_length else None),),
            content_type=content_type,
            extension=extension,
            webpage_url=url,
            platform=Platform.DIRECT.value,
            extractor=self.display_name,
            requires_authorization=False,
        )

    def download(
        self,
        url: str,
        destination: Path,
        media_format: MediaFormat | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self.validate_url(url)
        selected_format = media_format or MediaFormat('mp4', 'source')
        self.validate_download_options(selected_format.format, selected_format.quality)
        bytes_written = 0
        try:
            with self._http_client_factory(timeout=60, follow_redirects=False) as client:
                with public_network_dns_guard(), client.stream('GET', url) as response:
                    self._validate_response(response.status_code)
                    content_type = response.headers.get('content-type', '').split(';', 1)[0].lower()
                    if content_type not in self._content_extensions:
                        raise MediaNotDownloadable('The source returned an unsupported file type.')
                    content_length = response.headers.get('content-length')
                    self._validate_content_length(content_length)
                    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with destination.open('xb') as output:
                        os.chmod(destination, 0o600)
                        for chunk in response.iter_bytes(65536):
                            bytes_written += len(chunk)
                            if bytes_written > settings.download_max_bytes:
                                raise MediaNotDownloadable('The media file exceeds the configured size limit.')
                            output.write(chunk)
                            if progress_callback is not None:
                                progress_callback(bytes_written, int(content_length) if content_length else None)
            if bytes_written == 0:
                raise MediaNotDownloadable('The source returned an empty file.')
        except (ProviderUnavailable, MediaNotDownloadable):
            destination.unlink(missing_ok=True)
            raise
        except httpx.HTTPError as exc:
            destination.unlink(missing_ok=True)
            raise ProviderUnavailable('The media provider is temporarily unavailable.') from exc
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    @staticmethod
    def _validate_response(status_code: int) -> None:
        if 300 <= status_code < 400:
            raise MediaNotDownloadable('Redirects are not enabled for media sources.')
        if status_code >= 500:
            raise ProviderUnavailable('The media provider is temporarily unavailable.')
        if status_code >= 400:
            raise MediaNotDownloadable('The media source is not available for download.')

    @staticmethod
    def _validate_content_length(content_length: str | None) -> None:
        if content_length is None:
            return
        try:
            length = int(content_length)
        except ValueError as exc:
            raise MediaNotDownloadable('The media source returned an invalid size.') from exc
        if length < 0 or length > settings.download_max_bytes:
            raise MediaNotDownloadable('The media file exceeds the configured size limit.')