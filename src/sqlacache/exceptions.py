"""Exception hierarchy for sqlacache."""


class CacheError(Exception):
    """Base exception for sqlacache."""


class ConfigError(CacheError):
    """Raised for invalid cache configuration."""


class TransportError(CacheError):
    """Raised for cache transport failures."""
