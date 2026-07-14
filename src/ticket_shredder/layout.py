"""Shared on-disk layout for Helmsman projects under CARGO_DIR/.hProjects."""

from __future__ import annotations

from pathlib import Path

AGENT_BRANCH = "agents"
DEV_BRANCH = "dev"
BARE_DIRNAME = ".bare"
WT_DIRNAME = "wt"
TICKETS_DIRNAME = "tickets"


def project_home(hprojects_root: Path, name: str) -> Path:
    return hprojects_root / name


def bare_path(home: Path) -> Path:
    return home / BARE_DIRNAME


def agents_path(home: Path) -> Path:
    return home / WT_DIRNAME / AGENT_BRANCH


def dev_path(home: Path) -> Path:
    return home / WT_DIRNAME / DEV_BRANCH


def ticket_path(home: Path, number: int) -> Path:
    return home / WT_DIRNAME / TICKETS_DIRNAME / str(number)


def is_bare_layout(home: Path) -> bool:
    bare = bare_path(home)
    return bare.is_dir() and (
        (bare / "HEAD").exists() or (bare / "refs").exists()
    )


def is_legacy_clone(home: Path) -> bool:
    """True when home is a normal (non-bare) git working tree."""
    if is_bare_layout(home):
        return False
    git_dir = home / ".git"
    return git_dir.exists()
