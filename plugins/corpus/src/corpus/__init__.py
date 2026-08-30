"""Corpus connects saved context to exact current sources."""

__all__ = ["CorpusService"]

__version__ = "0.14.0"
__build_id__ = __version__


def __getattr__(name: str):
    if name == "CorpusService":
        from .service import CorpusService

        return CorpusService
    raise AttributeError(name)
