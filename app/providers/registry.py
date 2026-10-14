from urllib.parse import urlsplit

from app.providers.base import MediaProvider, Platform
from app.providers.direct_file import DirectFileProvider
from app.providers.ytdlp_provider import YtDlpProvider
from app.providers.errors import InvalidMediaUrl, UnsupportedPlatform


class ProviderRegistry:
    def __init__(self, providers: tuple[MediaProvider, ...] | list[MediaProvider]) -> None:
        self._providers = tuple(providers)

    @property
    def providers(self) -> tuple[MediaProvider, ...]:
        return self._providers

    @property
    def supported_platforms(self) -> tuple[Platform, ...]:
        return tuple(
            platform
            for provider in self._providers
            for platform in getattr(provider, 'supported_platforms', (provider.platform,))
        )

    def get_provider(self, url: str) -> MediaProvider:
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname or ''
        except ValueError as exc:
            raise InvalidMediaUrl('Enter a valid media URL.') from exc
        if parsed.scheme not in {'http', 'https'} or not hostname:
            raise InvalidMediaUrl('Enter a valid HTTP or HTTPS media URL.')

        for provider in self._providers:
            if provider.is_enabled and provider.can_handle(url):
                provider.validate_url(url)
                return provider
        raise UnsupportedPlatform()


default_provider_registry = ProviderRegistry([DirectFileProvider(), YtDlpProvider()])