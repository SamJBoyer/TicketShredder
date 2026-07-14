"""Resolve shared Helmsman project storage under CARGO_DIR."""

from __future__ import annotations

import os
from pathlib import Path


def hprojects_root() -> Path:
    """Return ``$CARGO_DIR/.hProjects``, creating it when missing."""
    cargo = os.environ.get("CARGO_DIR", "").strip()
    if not cargo:
        raise RuntimeError(
            "CARGO_DIR is not set. Set it to the Cargo root "
            "(for example C:\\Users\\…\\Desktop\\Cargo)."
        )
    root = Path(cargo).expanduser() / ".hProjects"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()
