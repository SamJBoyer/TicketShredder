from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class TicketStatus(str, Enum):
    QUEUED = "Queued"
    WORKING = "Working"
    REVIEW = "Ready for review"
    MERGED = "Merged"
    FAILED = "Failed"


@dataclass(slots=True)
class Ticket:
    number: int
    title: str
    body: str
    url: str
    labels: tuple[str, ...] = ()
    status: TicketStatus = TicketStatus.QUEUED
    branch: str | None = None
    worktree: Path | None = None
    detail: str = ""

    @property
    def cache_name(self) -> str:
        return f"{self.number}.md"


@dataclass(slots=True)
class Repository:
    remote_url: str
    root: Path
    """Agents worktree — TicketShredder merge target and .scratch home."""

    home: Path | None = None
    """Project directory under .hProjects/<name>."""

    bare: Path | None = None
    agents: Path | None = None
    dev: Path | None = None
    default_branch: str = "agents"
    tickets: list[Ticket] = field(default_factory=list)
