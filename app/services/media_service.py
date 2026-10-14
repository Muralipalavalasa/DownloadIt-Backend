from app.providers.registry import ProviderRegistry, default_provider_registry


def analyze_url(url: str, registry: ProviderRegistry | None = None) -> dict:
    provider_registry = registry or default_provider_registry
    provider = provider_registry.get_provider(url)
    metadata = provider.analyze(url)
    return {
        'success': True,
        'platform': metadata.platform or provider.platform_for(url).value,
        'extractor': metadata.extractor,
        'requires_authorization': metadata.requires_authorization or provider.requires_authorization,
        'title': metadata.title,
        'duration': metadata.duration,
        'thumbnail': metadata.thumbnail,
        'webpage_url': metadata.webpage_url,
        'uploader': metadata.uploader,
        'available_formats': [
            {
                'format': item.format,
                'quality': item.quality,
                'extension': item.extension,
                'resolution': item.resolution,
                'filesize': item.filesize,
                'video_codec': item.video_codec,
                'audio_codec': item.audio_codec,
            }
            for item in metadata.available_formats
        ],
    }
