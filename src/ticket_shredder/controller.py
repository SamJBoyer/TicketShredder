from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from .agent_runner import CursorAgentRunner
from .git_service import GitService
from .github_service import GitHubService
from .model import Repository, Ticket, TicketStatus

TicketCallback = Callable[[Ticket], None]


class TicketController:
    def __init__(self, workspace_root: Path, max_agents: int = 3) -> None:
        self.git = GitService(workspace_root)
        self.github = GitHubService()
        self.agents = CursorAgentRunner()
        self.executor = ThreadPoolExecutor(
            max_workers=max_agents,
            thread_name_prefix="ticket-shredder",
        )
        self.repository: Repository | None = None
        self.git_lock = Lock()

    def connect(self, remote_url: str) -> Repository:
        self.git.validate_remote(remote_url)
        repository = self.git.acquire(remote_url)
        self.github.sync_auto_tickets(repository)
        self.repository = repository
        return repository

    def start(
        self,
        repository: Repository,
        ticket: Ticket,
        callback: TicketCallback,
    ) -> Future[None]:
        if ticket.status not in {TicketStatus.QUEUED, TicketStatus.FAILED}:
            raise RuntimeError(
                f"Ticket #{ticket.number} is already {ticket.status.value}."
            )
        return self.executor.submit(self._implement, repository, ticket, callback)

    def _implement(
        self,
        repository: Repository,
        ticket: Ticket,
        callback: TicketCallback,
    ) -> None:
        try:
            if not ticket.worktree:
                with self.git_lock:
                    ticket.branch, ticket.worktree = self.git.create_worktree(
                        repository, ticket
                    )
            if self.git.has_reviewable_changes(ticket):
                ticket.status = TicketStatus.REVIEW
                ticket.detail = "Existing committed work is ready for review."
                self.github.save_state(repository.root, ticket)
                callback(ticket)
                return
            ticket.status = TicketStatus.WORKING
            ticket.detail = "Cursor is implementing the ticket."
            self.github.save_state(repository.root, ticket)
            callback(ticket)
            outcome = self.agents.implement(ticket)
            ticket.detail = outcome.detail
            ticket.status = (
                TicketStatus.REVIEW if outcome.succeeded else TicketStatus.FAILED
            )
        except Exception as exc:
            ticket.status = TicketStatus.FAILED
            ticket.detail = str(exc)
        self.github.save_state(repository.root, ticket)
        callback(ticket)

    def merge(self, repository: Repository, ticket: Ticket) -> None:
        with self.git_lock:
            warning = self.git.merge(repository, ticket)
        ticket.status = TicketStatus.MERGED
        ticket.detail = warning or "Merged into agents."
        if not warning:
            ticket.worktree = None
            ticket.branch = None
        self.github.save_state(repository.root, ticket)

    def dump(self, repository: Repository, ticket: Ticket) -> None:
        previous_status = ticket.status
        with self.git_lock:
            self.git.dump(repository, ticket)
        ticket.worktree = None
        ticket.branch = None
        if previous_status == TicketStatus.MERGED:
            ticket.status = TicketStatus.MERGED
            ticket.detail = "Merged into agents; leftover worktree cleaned up."
            self.github.save_state(repository.root, ticket)
        else:
            ticket.status = TicketStatus.QUEUED
            ticket.detail = "Discarded. Ready to retry."
            self.github.clear_state(repository.root, ticket)

    def close(self, repository: Repository, ticket: Ticket) -> None:
        if ticket.status == TicketStatus.WORKING:
            raise RuntimeError(
                f"Ticket #{ticket.number} is still being worked on."
            )
        if ticket.worktree:
            with self.git_lock:
                self.git.dump(repository, ticket)
            ticket.worktree = None
            ticket.branch = None
        self.github.close_issue(repository, ticket)
        self.github.remove_cache(repository.root, ticket)
        repository.tickets = [
            item for item in repository.tickets if item.number != ticket.number
        ]

    def shutdown(self) -> None:
        self.agents.cancel_all()
        self.executor.shutdown(wait=False, cancel_futures=True)
