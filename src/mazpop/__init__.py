"""mazpop: PSRC's MAZ-level population synthesis pipeline."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mazpop")
except PackageNotFoundError:  # running from a source checkout that isn't installed
    __version__ = "0.0.0"
