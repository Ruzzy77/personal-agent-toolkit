"""Corpus keeps registered sources read-only while indexing and reusing selected context."""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["CorpusService"]

try:
    from ._build import BUILD_ID as __build_id__
    from ._build import PACKAGE_VERSION as __version__
except ImportError:
    __build_id__ = None
    try:
        __version__ = version("corpus")
    except PackageNotFoundError:
        __version__ = "0+unknown"


def __getattr__(name: str):
    if name == "CorpusService":
        from .service import CorpusService

        return CorpusService
    raise AttributeError(name)
