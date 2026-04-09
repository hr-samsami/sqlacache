"""Public package exports for sqlacache."""

from sqlacache.config import configure
from sqlacache.exceptions import ConfigError
from sqlacache.manager import CacheManager

__all__ = [
    "CacheManager",
    "ConfigError",
    "__version__",
    "configure",
]

__version__ = "0.1.1"
