from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ticket_shredder.git_service import repository_name
from ticket_shredder.github_service import GitHubService
from ticket_shredder.model import Repository, Ticket, TicketStatus


class RepositoryNameTests(unittest.TestCase):
    def test_https_url(self) -> None:
        self.assertEqual(
            repository_name("https://github.com/acme/widgets.git"), "widgets"
        )

    def test_ssh_url(self) -> None:
        self.assertEqual(repository_name("git@github.com:acme/widgets.git"), "widgets")

    def test_empty_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            repository_name("https://github.com/")


class GitHubServiceTests(unittest.TestCase):
    def test_sync_parses_and_caches_auto_tickets(self) -> None:
        payload = [
            {
                "number": 12,
                "title": "Add the thing",
                "body": "Acceptance criteria",
                "url": "https://github.com/acme/widgets/issues/12",
                "labels": [{"name": "auto"}, {"name": "feature"}],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = Repository("remote", root)
            cache = root / ".scratch" / ".itickets" / "auto"
            cache.mkdir(parents=True)
            (cache / "99.md").write_text("stale", encoding="utf-8")

            with patch(
                "ticket_shredder.github_service.run",
                return_value=json.dumps(payload),
            ):
                tickets = GitHubService().sync_auto_tickets(repository)

            self.assertEqual(len(tickets), 1)
            self.assertEqual(tickets[0].number, 12)
            self.assertEqual(tickets[0].status, TicketStatus.QUEUED)
            self.assertEqual(tickets[0].labels, ("auto", "feature"))
            self.assertFalse((cache / "99.md").exists())
            cached = (cache / "12.md").read_text(encoding="utf-8")
            self.assertIn("# #12: Add the thing", cached)
            self.assertIn("Acceptance criteria", cached)

    def test_invalid_cli_json_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Repository("remote", Path(directory))
            with patch("ticket_shredder.github_service.run", return_value="{"):
                with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                    GitHubService().sync_auto_tickets(repository)

    def test_ticket_state_survives_a_restart(self) -> None:
        payload = [
            {
                "number": 7,
                "title": "Persist me",
                "body": "",
                "url": "https://example.test/7",
                "labels": [{"name": "auto"}],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = root / "worktree"
            worktree.mkdir()
            saved = Ticket(
                7,
                "Persist me",
                "",
                "https://example.test/7",
                status=TicketStatus.REVIEW,
                branch="ticket-shredder/issue-7",
                worktree=worktree,
                detail="Ready",
            )
            GitHubService.save_state(root, saved)
            repository = Repository("remote", root)

            with patch(
                "ticket_shredder.github_service.run",
                return_value=json.dumps(payload),
            ):
                restored = GitHubService().sync_auto_tickets(repository)[0]

            self.assertEqual(restored.status, TicketStatus.REVIEW)
            self.assertEqual(restored.branch, "ticket-shredder/issue-7")
            self.assertEqual(restored.worktree, worktree)


if __name__ == "__main__":
    unittest.main()
