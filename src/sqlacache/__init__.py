"""Public package exports for sqlacache."""

from sqlacache.config import configure
from sqlacache.exceptions import CacheError, ConfigError, TransportError
from sqlacache.manager import CacheManager

__all__ = [
    "CacheError",
    "CacheManager",
    "ConfigError",
    "TransportError",
    "__version__",
    "configure",
]

__version__ = "0.1.2"
