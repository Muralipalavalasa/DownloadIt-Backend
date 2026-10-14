import ipaddress
import shutil
import socket
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.core.config import settings
from app.core.network_security import public_network_dns_guard
from app.providers.base import MediaFormat, MediaMetadata, Platform, ProgressCallback
from app.providers.errors import (
    FFmpegUnavailable,
    InvalidMediaUrl,
    MediaNotDownloadable,
    ProviderUnavailable,
    RequestedQualityUnavailable,
    UnsupportedPlatform,
)

QUALITY_HEIGHTS = {'1080p': 1080, '720p': 720, '480p': 480, '360p': 360}


@dataclass(frozen=True)
class _YtDlpPlan:
    selector: str
    requires_ffmpeg: bool = False


class _QuietLogger:
    def debug(self, _message: str) -> None:
        return None

    def warning(self, _message: str) -> None:
        return None

    def error(self, _message: str) -> None:
        return None


class YtDlpProvider:
    platform = Platform.UNKNOWN
    display_name = 'yt-dlp'
    requires_authorization = True

    @property
    def is_enabled(self) -> bool:
        return settings.ytdlp_enabled

    @property
    def supported_platforms(self) -> tuple[Platform, ...]:
        return ()

    def can_handle(self, url: str) -> bool:
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
        except ValueError:
            return False
        return self.is_enabled and parsed.scheme in {'http', 'https'} and bool(hostname)

    def platform_for(self, url: str) -> Platform:
        return Platform.UNKNOWN

    def validate_url(self, url: str) -> None:
        try:
            parsed = urlsplit(url)
            hostname = (parsed.hostname or '').lower().rstrip('.')
            port = parsed.port
        except ValueError as exc:
            raise InvalidMediaUrl('Enter a valid media URL.') from exc
        if parsed.scheme not in {'http', 'https'}:
            raise InvalidMediaUrl('Only HTTP and HTTPS media URLs are accepted.')
        if not hostname or hostname == 'localhost' or hostname.endswith(('.localhost', '.local', '.internal', '.lan')):
            raise InvalidMediaUrl('Local and internal network URLs are not accepted.')
        if parsed.username is not None or parsed.password is not None:
            raise InvalidMediaUrl('Credentials are not accepted in media URLs.')
        allowed_ports = (None, 80) if parsed.scheme == 'http' else (None, 443)
        if port not in allowed_ports:
            raise InvalidMediaUrl('This media URL is not valid.')
        try:
            address = ipaddress.ip_address(hostname)
            addresses = {address}
        except ValueError:
            if '.' not in hostname:
                raise InvalidMediaUrl('Local and internal network URLs are not accepted.')
            try:
                addresses = {
                    ipaddress.ip_address(item[4][0])
                    for item in socket.getaddrinfo(
                        hostname,
                        port or (443 if parsed.scheme == 'https' else 80),
                        type=socket.SOCK_STREAM,
                    )
                }
            except socket.gaierror as exc:
                raise ProviderUnavailable('Media host could not be resolved.') from exc
        if not addresses or any(not item.is_global for item in addresses):
            raise InvalidMediaUrl('Private network addresses are not accepted.')
        if parsed.path.rstrip('/').lower() == '/playlist':
            raise MediaNotDownloadable('Playlists are not supported.')

    def supports_format(self, output_format: str, quality: str) -> bool:
        return (
            output_format == 'mp4' and quality in {'best', *QUALITY_HEIGHTS}
        ) or (output_format == 'm4a' and quality == 'audio')

    def validate_download_options(self, output_format: str, quality: str) -> None:
        if not self.supports_format(output_format, quality):
            raise RequestedQualityUnavailable('Requested format is not available for this video.')

    def select_format(self, metadata: MediaMetadata, output_format: str, quality: str) -> MediaFormat:
        self.validate_download_options(output_format, quality)
        selected = next(
            (item for item in metadata.available_formats if item.format == output_format and item.quality == quality),
            None,
        )
        if selected is not None:
            return selected
        merge_formats = (metadata.provider_data or {}).get('merge_formats', ())
        if quality in merge_formats and shutil.which('ffmpeg') is None:
            raise FFmpegUnavailable('FFmpeg is required to merge this video quality.')
        raise RequestedQualityUnavailable('Requested quality is not available for this video.')

    def validate_selection(self, url: str, output_format: str, quality: str) -> None:
        self.validate_url(url)
        self.validate_download_options(output_format, quality)

    def analyze(self, url: str) -> MediaMetadata:
        self.validate_url(url)
        YoutubeDL, DownloadError = self._load_ytdlp()
        try:
            with public_network_dns_guard(), YoutubeDL(self._base_options()) as ydl:
                info = ydl.extract_info(url, download=False)
        except DownloadError as exc:
            raise self._map_download_error(exc) from exc
        except Exception as exc:
            import traceback
            traceback.print_exc()
            raise ProviderUnavailable('The media provider is temporarily unavailable.') from exc

        if not isinstance(info, dict) or info.get('_type') in {'playlist', 'multi_video'} or info.get('entries'):
            raise MediaNotDownloadable('Playlists are not supported.')

        raw_formats = info.get('formats')
        if not isinstance(raw_formats, list):
            raw_formats = []
        ffmpeg_path = shutil.which('ffmpeg')
        media_formats, merge_formats = self._available_formats(raw_formats, ffmpeg_path is not None)
        if not media_formats and merge_formats and ffmpeg_path is None:
            raise FFmpegUnavailable('FFmpeg is required to merge the available video and audio streams.')
        if not media_formats:
            raise MediaNotDownloadable('No supported downloadable formats were found for this video.')

        duration_value = info.get('duration')
        duration = None
        if isinstance(duration_value, (int, float)) and duration_value >= 0:
            duration = str(timedelta(seconds=int(duration_value)))
        webpage_url = info.get('webpage_url')
        if not isinstance(webpage_url, str) or urlsplit(webpage_url).scheme not in {'http', 'https'}:
            webpage_url = url
        extractor = info.get('extractor_key') or info.get('extractor')
        extractor_name = extractor if isinstance(extractor, str) else 'Unknown'
        platform_name = extractor_name.lower().replace(' ', '_')

        return MediaMetadata(
            title=info.get('title') if isinstance(info.get('title'), str) else None,
            duration=duration,
            thumbnail=info.get('thumbnail') if isinstance(info.get('thumbnail'), str) else None,
            available_formats=tuple(media_formats),
            content_type='video/mp4',
            extension='.mp4',
            webpage_url=webpage_url,
            uploader=(info.get('uploader') or info.get('channel')) if isinstance(info.get('uploader') or info.get('channel'), str) else None,
            provider_data={'merge_formats': tuple(merge_formats)},
            platform=platform_name,
            extractor=extractor_name,
            requires_authorization=True,
        )

    def select_format(self, metadata: MediaMetadata, output_format: str, quality: str) -> MediaFormat:
        self.validate_download_options(output_format, quality)
        selected = next(
            (item for item in metadata.available_formats if item.format == output_format and item.quality == quality),
            None,
        )
        if selected is not None:
            return selected
        if quality in (metadata.provider_data or {}).get('merge_formats', ()) and shutil.which('ffmpeg') is None:
            raise FFmpegUnavailable('FFmpeg is required to merge this video quality.')
        raise RequestedQualityUnavailable('Requested quality is not available for this video.')

    def download(
        self,
        url: str,
        destination: Path,
        media_format: MediaFormat,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self.validate_url(url)
        YoutubeDL, DownloadError = self._load_ytdlp()
        plan = media_format.provider_data
        if not isinstance(plan, _YtDlpPlan):
            raise RequestedQualityUnavailable('Requested format is not available for this video.')
        ffmpeg_path = shutil.which('ffmpeg')
        if plan.requires_ffmpeg and ffmpeg_path is None:
            raise FFmpegUnavailable('FFmpeg is required to merge this video and audio format.')

        def progress_hook(state: dict[str, Any]) -> None:
            if state.get('status') != 'downloading':
                return
            downloaded = state.get('downloaded_bytes')
            total = state.get('total_bytes') or state.get('total_bytes_estimate')
            if not isinstance(downloaded, int):
                return
            if downloaded > settings.download_max_bytes:
                raise DownloadError('Configured maximum file size exceeded.')
            if progress_callback is not None:
                progress_callback(downloaded, total if isinstance(total, int) else None)

        options = self._base_options()
        options.update(
            {
                'format': plan.selector,
                'outtmpl': str(destination.with_suffix('')) + '.%(ext)s',
                'max_filesize': settings.download_max_bytes,
                'progress_hooks': [progress_hook],
                'overwrites': False,
                'nopart': False,
                'skip_download': False,
            }
        )
        if plan.requires_ffmpeg:
            options['ffmpeg_location'] = ffmpeg_path
            options['merge_output_format'] = 'mp4'

        try:
            with public_network_dns_guard(), YoutubeDL(options) as ydl:
                ydl.extract_info(url, download=True)
            if not destination.is_file():
                raise MediaNotDownloadable('The downloaded file is unavailable.')
            if destination.stat().st_size > settings.download_max_bytes:
                raise MediaNotDownloadable('The media file exceeds the configured size limit.')
        except (FFmpegUnavailable, MediaNotDownloadable, RequestedQualityUnavailable):
            self._cleanup_output(destination)
            raise
        except DownloadError as exc:
            self._cleanup_output(destination)
            raise self._map_download_error(exc) from exc
        except Exception as exc:
            self._cleanup_output(destination)
            raise ProviderUnavailable('The media provider is temporarily unavailable.') from exc

    def _base_options(self) -> dict[str, Any]:
        return {
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'skip_download': True,
            'ignoreerrors': False,
            'cachedir': False,
            'proxy': '',
            'concurrent_fragment_downloads': 1,
            'socket_timeout': 20,
            'retries': 2,
            'fragment_retries': 2,
            'restrictfilenames': True,
            'logger': _QuietLogger(),
        }

    @staticmethod
    def _load_ytdlp():
        try:
            from yt_dlp import YoutubeDL
            from yt_dlp.utils import DownloadError
        except ImportError as exc:
            raise ProviderUnavailable('The yt-dlp provider is not installed.') from exc
        return YoutubeDL, DownloadError

    def _available_formats(self, formats: list[dict[str, Any]], ffmpeg_available: bool) -> tuple[list[MediaFormat], list[str]]:
        video = [item for item in formats if self._has_video(item) and item.get('ext') == 'mp4']
        audio = [item for item in formats if self._has_audio(item) and item.get('ext') == 'm4a']
        combined = [item for item in video if self._has_audio(item)]
        separate_video = [item for item in video if not self._has_audio(item)]
        available: list[MediaFormat] = []
        merge_formats: list[str] = []

        best_combined = self._highest(combined)
        best_separate = self._highest(separate_video)
        if best_combined is not None:
            available.append(self._to_media_format(best_combined, 'mp4', 'best', _YtDlpPlan('best[ext=mp4]')))
        elif best_separate is not None and audio:
            if ffmpeg_available:
                plan = _YtDlpPlan('bestvideo[ext=mp4]+bestaudio[ext=m4a]', requires_ffmpeg=True)
                available.append(self._to_media_format(best_separate, 'mp4', 'best', plan, audio[0]))
            else:
                merge_formats.append('best')

        for quality, height in QUALITY_HEIGHTS.items():
            combined_at_height = [item for item in combined if self._display_height(item) == height]
            separate_at_height = [item for item in separate_video if self._display_height(item) == height]
            candidate = self._highest(combined_at_height)
            if candidate is not None:
                selector = self._dimension_selector('best', candidate)
                available.append(self._to_media_format(candidate, 'mp4', quality, _YtDlpPlan(selector)))
            elif separate_at_height and audio:
                candidate = self._highest(separate_at_height)
                if ffmpeg_available:
                    selector = self._dimension_selector('bestvideo', candidate) + '+bestaudio[ext=m4a]'
                    plan = _YtDlpPlan(selector, requires_ffmpeg=True)
                    available.append(self._to_media_format(candidate, 'mp4', quality, plan, audio[0]))
                else:
                    merge_formats.append(quality)

        oversized_formats = [
            item for item in available
            if item.filesize is not None and item.filesize > settings.download_max_bytes
        ]
        available = [
            item for item in available
            if item.filesize is None or item.filesize <= settings.download_max_bytes
        ]
        if not available and oversized_formats:
            raise MediaNotDownloadable('The media file exceeds the configured size limit.')

        if audio:
            audio_format = self._highest(
                [item for item in audio if self._reported_size(item) <= settings.download_max_bytes]
            )
            if audio_format is not None:
                available.append(
                    self._to_media_format(
                        audio_format,
                        'm4a',
                        'audio',
                        _YtDlpPlan('bestaudio[ext=m4a]'),
                    )
                )
        return available, merge_formats

    @staticmethod
    def _reported_size(media_format: dict[str, Any]) -> int:
        size = media_format.get('filesize') or media_format.get('filesize_approx')
        return size if isinstance(size, int) else 0

    @staticmethod
    def _has_video(media_format: dict[str, Any]) -> bool:
        return media_format.get('vcodec') not in (None, 'none')

    @staticmethod
    def _has_audio(media_format: dict[str, Any]) -> bool:
        return media_format.get('acodec') not in (None, 'none')

    @staticmethod
    def _highest(formats: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not formats:
            return None
        return max(formats, key=YtDlpProvider._display_height)

    @staticmethod
    def _display_height(media_format: dict[str, Any]) -> int:
        width = media_format.get('width')
        height = media_format.get('height')
        if isinstance(width, int) and isinstance(height, int):
            return min(width, height)
        return height if isinstance(height, int) else 0

    @staticmethod
    def _dimension_selector(selector: str, media_format: dict[str, Any]) -> str:
        width = media_format.get('width')
        height = media_format.get('height')
        if isinstance(width, int) and isinstance(height, int):
            return f'{selector}[width={width}][height={height}][ext=mp4]'
        if isinstance(height, int):
            return f'{selector}[height={height}][ext=mp4]'
        return f'{selector}[ext=mp4]'

    @staticmethod
    def _to_media_format(
        video_format: dict[str, Any],
        output_format: str,
        quality: str,
        plan: _YtDlpPlan,
        audio_format: dict[str, Any] | None = None,
    ) -> MediaFormat:
        video_size = video_format.get('filesize') or video_format.get('filesize_approx')
        audio_size = (audio_format or {}).get('filesize') or (audio_format or {}).get('filesize_approx')
        filesize = sum(size for size in (video_size, audio_size) if isinstance(size, int)) or None
        width = video_format.get('width')
        height = video_format.get('height')
        resolution = (
            f'{width}x{height}'
            if isinstance(width, int) and isinstance(height, int)
            else f'{height}p' if isinstance(height, int) else None
        )
        return MediaFormat(
            format=output_format,
            quality=quality,
            extension='.m4a' if output_format == 'm4a' else '.mp4',
            resolution=resolution,
            filesize=filesize,
            video_codec=video_format.get('vcodec') if isinstance(video_format.get('vcodec'), str) else None,
            audio_codec=(audio_format or video_format).get('acodec')
            if isinstance((audio_format or video_format).get('acodec'), str)
            else None,
            provider_data=plan,
        )

    @staticmethod
    def _cleanup_output(destination: Path) -> None:
        for path in destination.parent.glob(f'{destination.stem}.*'):
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)

    @staticmethod
    def _map_download_error(error: Exception) -> Exception:
        message = str(error).lower()
        if 'ffmpeg' in message:
            return FFmpegUnavailable('FFmpeg is required for this video/audio operation.')
        if 'max-filesize' in message or 'maximum file size' in message or 'size limit' in message:
            return MediaNotDownloadable('The media file exceeds the configured size limit.')
        if any(term in message for term in ('private video', 'sign in', 'login required', 'age-restricted', 'members-only')):
            return MediaNotDownloadable('This media is unavailable or requires authentication.')
        if 'requested format is not available' in message:
            return RequestedQualityUnavailable('Requested quality is not available for this media.')
        if 'unsupported url' in message or 'no suitable extractor' in message:
            return UnsupportedPlatform()
        return ProviderUnavailable('The media provider is temporarily unavailable.')