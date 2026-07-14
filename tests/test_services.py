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
from ticket_shredder.layout import agents_path, bare_path, dev_path, ticket_path
from ticket_shredder.model import Repository, Ticket, TicketStatus


def _git(args: list[str], cwd: Path) -> str:
    return run(["git", *args], cwd=cwd)


def _init_repo(path: Path, *, branch: str = "agents") -> None:
    path.mkdir(parents=True)
    _git(["init", "-b", branch], cwd=path)
    _git(["config", "user.email", "test@example.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)


def _seed_remote(base: Path) -> Path:
    remote = base / "remote.git"
    run(["git", "init", "--bare", "-b", "agents", str(remote)])
    seed = base / "seed"
    _init_repo(seed)
    (seed / "README.md").write_text("base\n", encoding="utf-8")
    _git(["add", "README.md"], cwd=seed)
    _git(["commit", "-m", "base"], cwd=seed)
    _git(["branch", "dev"], cwd=seed)
    _git(["remote", "add", "origin", str(remote)], cwd=seed)
    _git(["push", "-u", "origin", "agents"], cwd=seed)
    _git(["push", "-u", "origin", "dev"], cwd=seed)
    return remote


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


class GitServiceLayoutTests(unittest.TestCase):
    def test_acquire_creates_bare_agents_and_dev_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            remote = _seed_remote(base)
            service = GitService(base / "hprojects")
            repository = service.acquire(str(remote))

            self.assertTrue(bare_path(repository.home).is_dir())
            self.assertEqual(
                _git(["branch", "--show-current"], cwd=repository.agents),
                "agents",
            )
            self.assertEqual(
                _git(["branch", "--show-current"], cwd=repository.dev),
                "dev",
            )
            self.assertEqual(repository.root, repository.agents)

    def test_acquire_migrates_legacy_clone_and_preserves_ticket_branch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            remote = _seed_remote(base)
            hprojects = base / "hprojects"
            # Folder name must match repository_name(remote) so acquire migrates
            # this clone instead of creating a second project directory.
            legacy = hprojects / repository_name(str(remote))
            run(["git", "clone", "--branch", "agents", str(remote), str(legacy)])
            _git(["config", "user.email", "test@example.com"], cwd=legacy)
            _git(["config", "user.name", "Test"], cwd=legacy)

            # Dirty tracked edit on agents (the chronic merge blocker).
            (legacy / "README.md").write_text("human edit\n", encoding="utf-8")

            ticket_wt = hprojects / "worktrees" / legacy.name / "1"
            branch = "ticket-shredder/issue-1"
            run(
                ["git", "worktree", "add", "-b", branch, str(ticket_wt), "agents"],
                cwd=legacy,
            )
            _git(["config", "user.email", "test@example.com"], cwd=ticket_wt)
            _git(["config", "user.name", "Test"], cwd=ticket_wt)
            (ticket_wt / "feature.txt").write_text("done\n", encoding="utf-8")
            _git(["add", "feature.txt"], cwd=ticket_wt)
            _git(["commit", "-m", "feature"], cwd=ticket_wt)

            service = GitService(hprojects)
            repository = service.acquire(str(remote))

            self.assertTrue(bare_path(repository.home).is_dir())
            self.assertFalse((legacy / ".git").exists())
            self.assertEqual(
                _git(["branch", "--show-current"], cwd=repository.agents),
                "agents",
            )
            # Human edit should land on dev, leaving agents clean.
            agents_dirty = _git(["status", "--porcelain", "-uno"], cwd=repository.agents)
            self.assertEqual(agents_dirty, "")
            self.assertIn(
                "human edit",
                (repository.dev / "README.md").read_text(encoding="utf-8"),
            )
            reattached = ticket_path(repository.home, 1)
            self.assertTrue(reattached.exists())
            self.assertTrue((reattached / "feature.txt").exists())

    def test_merge_into_agents_worktree_allows_untracked_and_cleans_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            remote = _seed_remote(base)
            service = GitService(base / "hprojects")
            repository = service.acquire(str(remote))

            branch, worktree = service.create_worktree(
                repository,
                Ticket(1, "Feature", "", "https://example.test/1"),
            )
            _git(["config", "user.email", "test@example.com"], cwd=worktree)
            _git(["config", "user.name", "Test"], cwd=worktree)
            (worktree / "feature.txt").write_text("done\n", encoding="utf-8")
            _git(["add", "feature.txt"], cwd=worktree)
            _git(["commit", "-m", "feature"], cwd=worktree)

            runtime = repository.agents / "data" / "hprojects"
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
            service.merge(repository, ticket)

            self.assertTrue((repository.agents / "feature.txt").exists())
            self.assertTrue((runtime / "hp_local.json").exists())
            self.assertFalse(worktree.exists())

    def test_merge_rejects_tracked_dirty_agents_worktree_with_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            remote = _seed_remote(base)
            service = GitService(base / "hprojects")
            repository = service.acquire(str(remote))
            (repository.agents / "README.md").write_text("dirty\n", encoding="utf-8")

            worktree = ticket_path(repository.home, 1)
            worktree.mkdir(parents=True)
            ticket = Ticket(
                1,
                "Feature",
                "",
                "https://example.test/1",
                branch="ticket-shredder/issue-1",
                worktree=worktree,
            )
            with self.assertRaisesRegex(CommandError, "README.md"):
                service.merge(repository, ticket)

    def test_sync_agents_merges_when_local_and_origin_diverged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            remote = _seed_remote(base)
            service = GitService(base / "hprojects")
            repository = service.acquire(str(remote))
            agents = repository.agents
            _git(["config", "user.email", "test@example.com"], cwd=agents)
            _git(["config", "user.name", "Test"], cwd=agents)

            (agents / "local.txt").write_text("local\n", encoding="utf-8")
            _git(["add", "local.txt"], cwd=agents)
            _git(["commit", "-m", "local-only"], cwd=agents)

            other = base / "other"
            run(["git", "clone", "--branch", "agents", str(remote), str(other)])
            _git(["config", "user.email", "test@example.com"], cwd=other)
            _git(["config", "user.name", "Test"], cwd=other)
            (other / "remote.txt").write_text("remote\n", encoding="utf-8")
            _git(["add", "remote.txt"], cwd=other)
            _git(["commit", "-m", "remote-only"], cwd=other)
            _git(["push", "origin", "agents"], cwd=other)

            run(["git", "fetch", "--prune", "origin"], cwd=agents)
            service._sync_agents_from_origin(agents)

            files = {path.name for path in agents.iterdir() if path.is_file()}
            self.assertIn("local.txt", files)
            self.assertIn("remote.txt", files)
            behind = _git(["rev-list", "--count", "HEAD..origin/agents"], cwd=agents)
            self.assertEqual(behind, "0")


if __name__ == "__main__":
    unittest.main()
