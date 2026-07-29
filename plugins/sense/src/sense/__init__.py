"""Sense shared work-profile core."""

from importlib.metadata import PackageNotFoundError, version

try:
    from ._build import BUILD_ID, PACKAGE_VERSION
except ImportError:
    BUILD_ID = "source"
    try:
        PACKAGE_VERSION = version("sense")
    except PackageNotFoundError:
        PACKAGE_VERSION = "0+unknown"

__version__ = PACKAGE_VERSION
__all__ = ["BUILD_ID", "PACKAGE_VERSION", "__version__"]
