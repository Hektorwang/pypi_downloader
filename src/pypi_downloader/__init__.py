"""pypi-downloader: async PyPI package downloader for building offline PyPI mirrors."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__: str = version("pypi-downloader")
except PackageNotFoundError:  # running from a source checkout without installation
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
