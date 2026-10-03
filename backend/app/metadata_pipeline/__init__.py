"""Release-centric metadata rebuild pipeline.

This package is intentionally independent from the legacy per-track retagger.
"""

from .service import MetadataRebuildService

__all__ = ["MetadataRebuildService"]
