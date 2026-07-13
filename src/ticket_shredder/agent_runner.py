from __future__ import annotations

import asyncio
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
        self.model = model or os.getenv("TICKET_SHREDDER_MODEL", "auto")
        self._active: list[tuple[asyncio.AbstractEventLoop, object]] = []
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
        api_key = os.getenv("CURSOR_API_KEY")
        if not api_key:
            raise RuntimeError(
                "CURSOR_API_KEY is not set. Add it to .env in the project root."
            )
        return asyncio.run(self._prompt_async(ticket, worktree, api_key))

    @staticmethod
    def build_prompt(ticket: Ticket) -> str:
        return f"""Implement GitHub issue #{ticket.number} in this worktree.

Title: {ticket.title}

Description:
{ticket.body or "(No description provided.)"}

Requirements:
- Inspect and follow the repository's own instructions.
- Implement only this issue with production-quality code.
- Add or update focused tests when the repository has a test system.
- Run the relevant checks and fix failures caused by your work.
- Do not merge branches, delete this worktree, or modify another checkout.
- When you finish, write a descriptive git branch description on this worktree summarizing what you did (git config branch.<name>.description "...").
- Finish with a concise summary and the checks you ran.
"""

    async def _prompt_async(
        self, ticket: Ticket, worktree: Path, api_key: str
    ) -> object:
        try:
            from cursor_sdk import AsyncClient, LocalAgentOptions
        except ImportError as exc:
            raise RuntimeError(
                "Cursor SDK is not installed. Run: python -m pip install -e ."
            ) from exc

        prompt = self.build_prompt(ticket)
        local = LocalAgentOptions(cwd=str(worktree))
        async with await AsyncClient.launch_bridge(
            workspace=worktree,
            local=local,
        ) as client:
            async with await client.create_agent(
                model=self.model,
                api_key=api_key,
                local=local,
            ) as agent:
                agent_run = await agent.send(prompt)
                active = (asyncio.get_running_loop(), agent_run)
                with self._active_lock:
                    self._active.append(active)
                try:
                    return await agent_run.wait()
                finally:
                    with self._active_lock:
                        if active in self._active:
                            self._active.remove(active)

    def cancel_all(self) -> None:
        with self._active_lock:
            active = list(self._active)
        for loop, agent_run in active:
            try:
                if getattr(agent_run, "supports", lambda _operation: True)("cancel"):
                    cancellation = asyncio.run_coroutine_threadsafe(
                        agent_run.cancel(), loop
                    )
                    cancellation.result(timeout=5)
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
            ["git", "rev-list", "--count", "agents..HEAD"],
            cwd=ticket.worktree,
        )
        if int(ahead) == 0:
            raise CommandError("The agent finished without creating any changes.")
