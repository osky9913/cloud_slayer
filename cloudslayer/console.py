"""Shared console configured not to crash on legacy Windows encodings."""

from __future__ import annotations

import sys

from rich.console import Console


def _make_console() -> Console:
    # Rich may emit Unicode even when the legacy Windows stream is cp1252.
    # Replacing an unsupported glyph is preferable to aborting the command.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
    return Console()


console = _make_console()
error_console = Console(stderr=True)
