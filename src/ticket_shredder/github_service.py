from __future__ import annotations

import json
from pathlib import Path

from .git_service import CommandError, run
from .model import Repository, Ticket, TicketStatus


class GitHubService:
    def sync_auto_tickets(self, repository: Repository) -> list[Ticket]:
        tickets = self._list_auto_tickets(repository)
        self._write_cache(repository.root, tickets)
        for ticket in tickets:
            self._restore_state(repository.root, ticket)
        repository.tickets = tickets
        return tickets

    def poll_new_auto_tickets(self, repository: Repository) -> list[Ticket]:
        """Fetch open ``auto`` issues and append any not already tracked.

        Existing in-memory ``Ticket`` objects are preserved so a live agent
        run is not replaced or marked interrupted by a poll.
        """
        remote = self._list_auto_tickets(repository)
        self._write_cache(repository.root, remote)
        known = {ticket.number for ticket in repository.tickets}
        new_tickets: list[Ticket] = []
        for ticket in remote:
            if ticket.number in known:
                continue
            self._restore_state(repository.root, ticket)
            new_tickets.append(ticket)
        if new_tickets:
            repository.tickets = sorted(
                [*repository.tickets, *new_tickets],
                key=lambda ticket: ticket.number,
            )
        return new_tickets

    def _list_auto_tickets(self, repository: Repository) -> list[Ticket]:
        raw = run(
            [
                "gh",
                "issue",
                "list",
                "--state",
                "open",
                "--label",
                "auto",
                "--limit",
                "100",
                "--json",
                "number,title,body,url,labels",
            ],
            cwd=repository.root,
            timeout=60,
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CommandError(f"GitHub CLI returned invalid JSON: {exc}") from exc

        tickets = [self._ticket_from_json(item) for item in payload]
        tickets.sort(key=lambda ticket: ticket.number)
        return tickets

    def close_issue(self, repository: Repository, ticket: Ticket) -> None:
        run(
            ["gh", "issue", "close", str(ticket.number)],
            cwd=repository.root,
            timeout=60,
        )

    @staticmethod
    def remove_cache(root: Path, ticket: Ticket) -> None:
        cache = root / ".scratch" / ".itickets" / "auto"
        (cache / ticket.cache_name).unlink(missing_ok=True)
        (cache / f"{ticket.number}.status.json").unlink(missing_ok=True)

    @staticmethod
    def _ticket_from_json(item: dict[str, object]) -> Ticket:
        labels = tuple(
            str(label.get("name", ""))
            for label in item.get("labels", [])
            if isinstance(label, dict) and label.get("name")
        )
        return Ticket(
            number=int(item["number"]),
            title=str(item["title"]),
            body=str(item.get("body") or ""),
            url=str(item.get("url") or ""),
            labels=labels,
        )

    @staticmethod
    def _write_cache(root: Path, tickets: list[Ticket]) -> None:
        cache = root / ".scratch" / ".itickets" / "auto"
        cache.mkdir(parents=True, exist_ok=True)
        current = {ticket.cache_name for ticket in tickets}
        for old_file in cache.glob("*.md"):
            if old_file.name not in current:
                old_file.unlink()
        current_states = {f"{ticket.number}.status.json" for ticket in tickets}
        for old_state in cache.glob("*.status.json"):
            if old_state.name not in current_states:
                old_state.unlink()
        for ticket in tickets:
            labels = ", ".join(ticket.labels)
            content = (
                f"# #{ticket.number}: {ticket.title}\n\n"
                f"- URL: {ticket.url}\n"
                f"- Labels: {labels}\n\n"
                f"{ticket.body.rstrip()}\n"
            )
            temporary = cache / f".{ticket.cache_name}.tmp"
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(cache / ticket.cache_name)

    @staticmethod
    def save_state(root: Path, ticket: Ticket) -> None:
        cache = root / ".scratch" / ".itickets" / "auto"
        cache.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": ticket.status.value,
            "branch": ticket.branch,
            "worktree": str(ticket.worktree) if ticket.worktree else None,
            "detail": ticket.detail,
        }
        destination = cache / f"{ticket.number}.status.json"
        temporary = cache / f".{ticket.number}.status.json.tmp"
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(destination)

    @staticmethod
    def clear_state(root: Path, ticket: Ticket) -> None:
        state = root / ".scratch" / ".itickets" / "auto" / (
            f"{ticket.number}.status.json"
        )
        state.unlink(missing_ok=True)

    @staticmethod
    def _restore_state(root: Path, ticket: Ticket) -> None:
        state = root / ".scratch" / ".itickets" / "auto" / (
            f"{ticket.number}.status.json"
        )
        if not state.exists():
            return
        try:
            payload = json.loads(state.read_text(encoding="utf-8"))
            ticket.status = TicketStatus(str(payload["status"]))
            ticket.branch = str(payload["branch"]) if payload.get("branch") else None
            ticket.worktree = (
                Path(str(payload["worktree"])) if payload.get("worktree") else None
            )
            ticket.detail = str(payload.get("detail") or "")
        except (KeyError, ValueError, json.JSONDecodeError):
            ticket.status = TicketStatus.FAILED
            ticket.detail = "Saved ticket state was invalid; the ticket will retry."
        if ticket.status == TicketStatus.WORKING:
            ticket.status = TicketStatus.FAILED
            ticket.detail = "The previous agent run was interrupted; retrying."
        if (
            ticket.status in {TicketStatus.REVIEW, TicketStatus.FAILED}
            and ticket.worktree
            and not ticket.worktree.exists()
        ):
            ticket.status = TicketStatus.FAILED
            ticket.detail = "The saved worktree is missing; recreating it."
            ticket.worktree = None
