"""Legacy alias for TailCam (formerly AnyCam).

This shim keeps pre-rename installs working across `anycam update`:
- old service units run `python -m anycam run`
- the old `anycam` console script imports `anycam.cli`
- old clients detect updates by fetching this file's __version__ from GitHub

Keep __version__ in sync with src/tailcam/__init__.py on every release.
"""

__version__ = "0.5.0"
