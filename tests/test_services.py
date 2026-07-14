from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ticket_shredder.agent_runner import CursorAgentRunner
from ticket_shredder.controller import TicketController
from ticket_shredder.git_service import (
    CommandError,
    GitService,
    remote_identity,
    repository_name,
    run,
)
from ticket_shredder.github_service import GitHubService
from ticket_shredder.model import Repository, Ticket, TicketStatus


def _git(args: list[str], cwd: Path) -> str:
    return run(["git", *args], cwd=cwd)


def _init_repo(path: Path, *, branch: str = "agents") -> None:
    path.mkdir(parents=True)
    _git(["init", "-b", branch], cwd=path)
    _git(["config", "user.email", "test@example.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)


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

    def test_remote_identity_ignores_git_suffix_and_trailing_slash(self) -> None:
        self.assertEqual(
            remote_identity("https://github.com/acme/widgets.git"),
            remote_identity("https://github.com/acme/widgets/"),
        )

    def test_remote_identity_matches_ssh_and_https(self) -> None:
        self.assertEqual(
            remote_identity("git@github.com:acme/widgets.git"),
            remote_identity("https://github.com/acme/widgets"),
        )


class CursorAgentRunnerTests(unittest.TestCase):
    def test_missing_api_key_is_reported_before_agent_launch(self) -> None:
        ticket = Ticket(1, "Test", "", "https://example.test/1")
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"CURSOR_API_KEY": ""}):
                with self.assertRaisesRegex(RuntimeError, "CURSOR_API_KEY is not set"):
                    CursorAgentRunner()._prompt(ticket, Path(directory))

    def test_prompt_asks_for_finish_git_branch_description(self) -> None:
        ticket = Ticket(
            7,
            "Finish git description",
            "Write what you did.",
            "https://example.test/7",
        )
        prompt = CursorAgentRunner.build_prompt(ticket)
        self.assertIn("Implement GitHub issue #7", prompt)
        self.assertIn("Finish git description", prompt)
        self.assertIn("Write what you did.", prompt)
        self.assertIn("git branch description", prompt)
        self.assertIn("branch.<name>.description", prompt)


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

    def test_close_issue_invokes_gh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = Repository("remote", root)
            ticket = Ticket(
                6,
                "Close me",
                "",
                "https://example.test/6",
            )
            with patch("ticket_shredder.github_service.run") as mocked:
                GitHubService().close_issue(repository, ticket)
            mocked.assert_called_once_with(
                ["gh", "issue", "close", "6"],
                cwd=root,
                timeout=60,
            )

    def test_remove_cache_deletes_ticket_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / ".scratch" / ".itickets" / "auto"
            cache.mkdir(parents=True)
            (cache / "6.md").write_text("cached", encoding="utf-8")
            (cache / "6.status.json").write_text("{}", encoding="utf-8")
            (cache / "7.md").write_text("keep", encoding="utf-8")
            ticket = Ticket(6, "Close me", "", "https://example.test/6")

            GitHubService.remove_cache(root, ticket)

            self.assertFalse((cache / "6.md").exists())
            self.assertFalse((cache / "6.status.json").exists())
            self.assertTrue((cache / "7.md").exists())


class TicketControllerCloseTests(unittest.TestCase):
    def test_close_removes_cache_worktree_and_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = root / "worktree"
            worktree.mkdir()
            cache = root / ".scratch" / ".itickets" / "auto"
            cache.mkdir(parents=True)
            (cache / "6.md").write_text("cached", encoding="utf-8")
            (cache / "6.status.json").write_text("{}", encoding="utf-8")
            ticket = Ticket(
                6,
                "Close me",
                "",
                "https://example.test/6",
                status=TicketStatus.MERGED,
                branch="ticket-shredder/issue-6",
                worktree=worktree,
                detail="Merged",
            )
            other = Ticket(7, "Keep me", "", "https://example.test/7")
            repository = Repository("remote", root, tickets=[ticket, other])
            controller = TicketController(root)
            try:
                with patch.object(controller.git, "dump") as dump:
                    with patch.object(controller.github, "close_issue") as close_issue:
                        controller.close(repository, ticket)

                dump.assert_called_once_with(repository, ticket)
                close_issue.assert_called_once_with(repository, ticket)
                self.assertIsNone(ticket.worktree)
                self.assertIsNone(ticket.branch)
                self.assertEqual(repository.tickets, [other])
                self.assertFalse((cache / "6.md").exists())
                self.assertFalse((cache / "6.status.json").exists())
            finally:
                controller.shutdown()

    def test_close_rejects_working_tickets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ticket = Ticket(
                6,
                "Busy",
                "",
                "https://example.test/6",
                status=TicketStatus.WORKING,
            )
            repository = Repository("remote", root, tickets=[ticket])
            controller = TicketController(root)
            try:
                with self.assertRaisesRegex(RuntimeError, "still being worked"):
                    controller.close(repository, ticket)
            finally:
                controller.shutdown()


class GitServiceCheckoutTests(unittest.TestCase):
    def test_checkout_agents_merges_when_local_and_origin_diverged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            remote = base / "remote.git"
            run(["git", "init", "--bare", "-b", "agents", str(remote)])

            seed = base / "seed"
            _init_repo(seed)
            (seed / "README.md").write_text("base\n", encoding="utf-8")
            _git(["add", "README.md"], cwd=seed)
            _git(["commit", "-m", "base"], cwd=seed)
            _git(["remote", "add", "origin", str(remote)], cwd=seed)
            _git(["push", "-u", "origin", "agents"], cwd=seed)

            clone = base / "repos" / "widgets"
            run(["git", "clone", "--branch", "agents", str(remote), str(clone)])
            _git(["config", "user.email", "test@example.com"], cwd=clone)
            _git(["config", "user.name", "Test"], cwd=clone)

            # Divergent local commit (simulates a ticket merge that never pushed).
            (clone / "local.txt").write_text("local\n", encoding="utf-8")
            _git(["add", "local.txt"], cwd=clone)
            _git(["commit", "-m", "local-only"], cwd=clone)

            # Divergent remote commit.
            other = base / "other"
            run(["git", "clone", "--branch", "agents", str(remote), str(other)])
            _git(["config", "user.email", "test@example.com"], cwd=other)
            _git(["config", "user.name", "Test"], cwd=other)
            (other / "remote.txt").write_text("remote\n", encoding="utf-8")
            _git(["add", "remote.txt"], cwd=other)
            _git(["commit", "-m", "remote-only"], cwd=other)
            _git(["push", "origin", "agents"], cwd=other)

            service = GitService(base)
            run(["git", "fetch", "--prune", "origin"], cwd=clone)
            service._checkout_agents(clone)

            self.assertEqual(_git(["branch", "--show-current"], cwd=clone), "agents")
            files = {path.name for path in clone.iterdir() if path.is_file()}
            self.assertIn("local.txt", files)
            self.assertIn("remote.txt", files)
            # Merge must have integrated origin/agents (no longer behind).
            behind = _git(["rev-list", "--count", "HEAD..origin/agents"], cwd=clone)
            self.assertEqual(behind, "0")

    def test_merge_allows_untracked_files_in_agents_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            remote = base / "remote.git"
            run(["git", "init", "--bare", "-b", "agents", str(remote)])

            seed = base / "seed"
            _init_repo(seed)
            (seed / "README.md").write_text("base\n", encoding="utf-8")
            _git(["add", "README.md"], cwd=seed)
            _git(["commit", "-m", "base"], cwd=seed)
            _git(["remote", "add", "origin", str(remote)], cwd=seed)
            _git(["push", "-u", "origin", "agents"], cwd=seed)

            clone = base / "widgets"
            run(["git", "clone", "--branch", "agents", str(remote), str(clone)])
            _git(["config", "user.email", "test@example.com"], cwd=clone)
            _git(["config", "user.name", "Test"], cwd=clone)

            worktree = base / "worktrees" / "widgets" / "1"
            branch = "ticket-shredder/issue-1"
            run(
                ["git", "worktree", "add", "-b", branch, str(worktree), "agents"],
                cwd=clone,
            )
            _git(["config", "user.email", "test@example.com"], cwd=worktree)
            _git(["config", "user.name", "Test"], cwd=worktree)
            (worktree / "feature.txt").write_text("done\n", encoding="utf-8")
            _git(["add", "feature.txt"], cwd=worktree)
            _git(["commit", "-m", "feature"], cwd=worktree)

            # Runtime untracked files in the agents checkout (e.g. TicketsPlease data/).
            runtime = clone / "data" / "hprojects"
            runtime.mkdir(parents=True)
            (runtime / "hp_local.json").write_text("{}\n", encoding="utf-8")

            ticket = Ticket(
                1,
                "Feature",
                "",
                "https://example.test/1",
                branch=branch,
                worktree=worktree,
            )
            repository = Repository(str(remote), clone)
            service = GitService(base)
            service.merge(repository, ticket)

            self.assertEqual(_git(["branch", "--show-current"], cwd=clone), "agents")
            self.assertTrue((clone / "feature.txt").exists())
            self.assertTrue((runtime / "hp_local.json").exists())
            self.assertFalse(worktree.exists())

    def test_merge_rejects_tracked_dirty_agents_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            remote = base / "remote.git"
            run(["git", "init", "--bare", "-b", "agents", str(remote)])

            seed = base / "seed"
            _init_repo(seed)
            (seed / "README.md").write_text("base\n", encoding="utf-8")
            _git(["add", "README.md"], cwd=seed)
            _git(["commit", "-m", "base"], cwd=seed)
            _git(["remote", "add", "origin", str(remote)], cwd=seed)
            _git(["push", "-u", "origin", "agents"], cwd=seed)

            clone = base / "widgets"
            run(["git", "clone", "--branch", "agents", str(remote), str(clone)])
            (clone / "README.md").write_text("dirty\n", encoding="utf-8")

            worktree = base / "wt"
            worktree.mkdir()
            ticket = Ticket(
                1,
                "Feature",
                "",
                "https://example.test/1",
                branch="ticket-shredder/issue-1",
                worktree=worktree,
            )
            with self.assertRaisesRegex(CommandError, "uncommitted changes"):
                GitService(base).merge(Repository(str(remote), clone), ticket)


if __name__ == "__main__":
    unittest.main()
