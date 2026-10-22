"""
aurserver — the Auralis HTTP API.

The package is deliberately thin: :mod:`aurserver.app` builds the FastAPI
application, :mod:`aurserver.audio` holds the decoding / encoding / analysis
helpers and :mod:`aurserver.assets` owns the in-process asset store.

Start the development server with::

    uvicorn aurserver.app:app --port 8731
"""

from __future__ import annotations

__all__ = ["app", "audio", "assets"]

__version__ = "0.1.0-alpha"
