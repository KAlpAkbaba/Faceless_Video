"""Faceless YouTube video automation pipeline."""

import sys

__version__ = "0.1.0"

# Checked before any submodule is imported: on an older interpreter the failure
# would otherwise surface as a bare ModuleNotFoundError for zoneinfo (3.9+) or a
# syntax error, neither of which points at the real problem.
if sys.version_info < (3, 11):
    raise SystemExit(
        "This pipeline needs Python 3.11 or newer, but it is running on "
        f"{sys.version.split()[0]} ({sys.executable}).\n\n"
        "If the virtualenv was built with an older Python, delete it and rebuild:\n"
        "    Windows:      rmdir /s /q .venv  &&  make install\n"
        "    Linux/macOS:  rm -rf .venv && make install\n\n"
        "Install a current Python from https://www.python.org/downloads/ first, "
        'ticking "Add python.exe to PATH".'
    )
