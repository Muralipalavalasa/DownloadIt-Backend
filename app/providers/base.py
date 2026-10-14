from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Callable, Protocol

ProgressCallback = Callable[[int, int | None], None]


class Platform(StrEnum):
    DIRECT = 'direct'
    YOUTUBE = 'youtube'
    INSTAGRAM = 'instagram'
    TIKTOK = 'tiktok'
    TWITTER = 'twitter'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class MediaFormat:
    format: str
    quality: str
    extension: str = '.mp4'
    resolution: str | None = None
    filesize: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    provider_data: object | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class MediaMetadata:
    title: str | None
    duration: str | None
    thumbnail: str | None
    available_formats: tuple[MediaFormat, ...]
    content_type: str
    extension: str
    webpage_url: str | None = None
    uploader: str | None = None
    provider_data: object | None = field(default=None, repr=False, compare=False)
    platform: str | None = None
    extractor: str | None = None
    requires_authorization: bool = False


class MediaProvider(Protocol):
    platform: Platform
    display_name: str
    requires_authorization: bool

    @property
    def supported_platforms(self) -> tuple[Platform, ...]: ...

    @property
    def is_enabled(self) -> bool: ...

    def can_handle(self, url: str) -> bool: ...

    def platform_for(self, url: str) -> Platform: ...

    def validate_url(self, url: str) -> None: ...

    def supports_format(self, output_format: str, quality: str) -> bool: ...

    def validate_download_options(self, output_format: str, quality: str) -> None: ...

    def validate_selection(self, url: str, output_format: str, quality: str) -> None: ...

    def select_format(self, metadata: MediaMetadata, output_format: str, quality: str) -> MediaFormat: ...

    def analyze(self, url: str) -> MediaMetadata: ...

    def download(
        self,
        url: str,
        destination: Path,
        media_format: MediaFormat | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None: ...