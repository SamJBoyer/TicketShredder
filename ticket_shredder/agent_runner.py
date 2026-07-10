from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from .git_service import CommandError, run
from .model import Ticket


@dataclass(frozen=True, slots=True)
class AgentOutcome:
    succeeded: bool
    detail: str


class CursorAgentRunner:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.getenv("TICKET_SHREDDER_MODEL", "composer-2.5")
        self._active: list[object] = []
        self._active_lock = threading.Lock()

    def implement(self, ticket: Ticket) -> AgentOutcome:
        if not ticket.worktree:
            return AgentOutcome(False, "No worktree was assigned.")
        try:
            result = self._prompt(ticket, ticket.worktree)
            status = str(getattr(result, "status", "error"))
            detail = str(getattr(result, "result", "") or status)
            if status != "finished":
                return AgentOutcome(False, detail)
            self._ensure_commit(ticket)
            return AgentOutcome(True, detail)
        except Exception as exc:
            return AgentOutcome(False, str(exc))

    def _prompt(self, ticket: Ticket, worktree: Path) -> object:
        try:
            from cursor_sdk import Agent, LocalAgentOptions
        except ImportError as exc:
            raise RuntimeError(
                "Cursor SDK is not installed. Run: python -m pip install -e ."
            ) from exc

        prompt = f"""Implement GitHub issue #{ticket.number} in this worktree.

Title: {ticket.title}

Description:
{ticket.body or "(No description provided.)"}

Requirements:
- Inspect and follow the repository's own instructions.
- Implement only this issue with production-quality code.
- Add or update focused tests when the repository has a test system.
- Run the relevant checks and fix failures caused by your work.
- Do not merge branches, delete this worktree, or modify another checkout.
- Finish with a concise summary and the checks you ran.
"""
        option_values: dict[str, object] = {
            "model": self.model,
            "local": LocalAgentOptions(cwd=str(worktree)),
        }
        api_key = os.getenv("CURSOR_API_KEY")
        if api_key:
            option_values["api_key"] = api_key
        with Agent.create(**option_values) as agent:
            agent_run = agent.send(prompt)
            with self._active_lock:
                self._active.append(agent_run)
            try:
                return agent_run.wait()
            finally:
                with self._active_lock:
                    if agent_run in self._active:
                        self._active.remove(agent_run)

    def cancel_all(self) -> None:
        with self._active_lock:
            active = list(self._active)
        for agent_run in active:
            try:
                if getattr(agent_run, "supports", lambda _operation: True)("cancel"):
                    agent_run.cancel()
            except Exception:
                continue

    @staticmethod
    def _ensure_commit(ticket: Ticket) -> None:
        assert ticket.worktree is not None
        dirty = run(["git", "status", "--porcelain"], cwd=ticket.worktree)
        if dirty:
            run(["git", "add", "--all"], cwd=ticket.worktree)
            run(
                ["git", "commit", "-m", f"Implement issue #{ticket.number}"],
                cwd=ticket.worktree,
            )
        ahead = run(
            ["git", "rev-list", "--count", "dev..HEAD"],
            cwd=ticket.worktree,
        )
        if int(ahead) == 0:
            raise CommandError("The agent finished without creating any changes.")
