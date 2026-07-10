from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

from .model import Repository, Ticket


class CommandError(RuntimeError):
    pass


def run(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 120,
) -> str:
    try:
        completed = subprocess.run(
            list(args),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CommandError(f"Could not run {args[0]}: {exc}") from exc
    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise CommandError(message or f"{args[0]} exited with {completed.returncode}")
    return completed.stdout.strip()


def repository_name(remote_url: str) -> str:
    value = remote_url.strip()
    parsed = urlsplit(value)
    if parsed.scheme and parsed.netloc:
        name = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    else:
        name = re.split(r"[/\\:]", value.rstrip("/\\"))[-1]
    if name.endswith(".git"):
        name = name[:-4]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    if not safe:
        raise ValueError("The Git URL does not contain a repository name.")
    return safe


def remote_identity(remote_url: str) -> tuple[str, str]:
    value = remote_url.strip()
    parsed = urlsplit(value)
    if parsed.scheme and parsed.hostname:
        host = parsed.hostname
        path = parsed.path
    else:
        scp_style = re.fullmatch(r"(?:[^@/]+@)?([^:/]+):(.+)", value)
        if not scp_style:
            return "", value.rstrip("/\\").casefold().removesuffix(".git")
        host, path = scp_style.groups()
    return (
        host.casefold(),
        path.strip("/\\").casefold().removesuffix(".git"),
    )


class GitService:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.expanduser().resolve()

    def validate_remote(self, remote_url: str) -> None:
        if not remote_url.strip():
            raise ValueError("Enter a Git repository URL.")
        run(["git", "ls-remote", remote_url], timeout=30)

    def acquire(self, remote_url: str) -> Repository:
        name = repository_name(remote_url)
        root = self.workspace_root / "repos" / name
        root.parent.mkdir(parents=True, exist_ok=True)
        if root.exists():
            if not (root / ".git").exists():
                raise CommandError(f"{root} exists but is not a Git repository.")
            actual_remote = run(["git", "remote", "get-url", "origin"], cwd=root)
            if remote_identity(actual_remote) != remote_identity(remote_url):
                raise CommandError(f"{root} is already connected to {actual_remote}.")
            run(["git", "fetch", "--prune", "origin"], cwd=root, timeout=300)
        else:
            run(["git", "clone", remote_url, str(root)], timeout=600)
        self._exclude_runtime_files(root)
        self._checkout_dev(root)
        return Repository(remote_url=remote_url, root=root)

    def _checkout_dev(self, root: Path) -> None:
        local = run(["git", "branch", "--list", "dev"], cwd=root)
        if local:
            run(["git", "checkout", "dev"], cwd=root)
            remote = run(
                ["git", "branch", "--remotes", "--list", "origin/dev"],
                cwd=root,
            )
            if remote:
                run(["git", "merge", "--ff-only", "origin/dev"], cwd=root)
            return
        remote = run(["git", "branch", "--remotes", "--list", "origin/dev"], cwd=root)
        if remote:
            run(["git", "checkout", "--track", "origin/dev"], cwd=root)
            return
        run(["git", "checkout", "-b", "dev"], cwd=root)

    @staticmethod
    def _exclude_runtime_files(root: Path) -> None:
        raw_path = run(["git", "rev-parse", "--git-path", "info/exclude"], cwd=root)
        exclude = Path(raw_path)
        if not exclude.is_absolute():
            exclude = root / exclude
        exclude.parent.mkdir(parents=True, exist_ok=True)
        content = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        pattern = ".scratch/.itickets/auto/"
        if pattern not in content.splitlines():
            separator = "" if not content or content.endswith("\n") else "\n"
            exclude.write_text(
                f"{content}{separator}{pattern}\n",
                encoding="utf-8",
            )

    def create_worktree(self, repository: Repository, ticket: Ticket) -> tuple[str, Path]:
        branch = f"ticket-shredder/issue-{ticket.number}"
        worktree = self.workspace_root / "worktrees" / repository.root.name / str(ticket.number)
        worktree.parent.mkdir(parents=True, exist_ok=True)
        if worktree.exists():
            actual_branch = run(
                ["git", "branch", "--show-current"],
                cwd=worktree,
            )
            if actual_branch != branch:
                raise CommandError(
                    f"{worktree} belongs to {actual_branch}, not {branch}."
                )
            return branch, worktree
        existing_branch = run(
            ["git", "branch", "--list", branch],
            cwd=repository.root,
        )
        command = ["git", "worktree", "add"]
        if existing_branch:
            command.extend([str(worktree), branch])
        else:
            command.extend(["-b", branch, str(worktree), "dev"])
        run(command, cwd=repository.root)
        return branch, worktree

    @staticmethod
    def has_reviewable_changes(ticket: Ticket) -> bool:
        if not ticket.worktree:
            return False
        if run(["git", "status", "--porcelain"], cwd=ticket.worktree):
            return False
        ahead = run(
            ["git", "rev-list", "--count", "dev..HEAD"],
            cwd=ticket.worktree,
        )
        return int(ahead) > 0

    def merge(self, repository: Repository, ticket: Ticket) -> str | None:
        if not ticket.branch or not ticket.worktree:
            raise CommandError("This ticket has no worktree to merge.")
        if run(["git", "status", "--porcelain"], cwd=repository.root):
            raise CommandError("The dev checkout has uncommitted changes.")
        run(["git", "checkout", "dev"], cwd=repository.root)
        run(["git", "merge", "--no-ff", "--no-edit", ticket.branch], cwd=repository.root)
        try:
            self._remove_worktree(repository, ticket)
        except CommandError as exc:
            return f"Merged into dev; cleanup needs attention: {exc}"
        return None

    def dump(self, repository: Repository, ticket: Ticket) -> None:
        if not ticket.branch or not ticket.worktree:
            return
        self._remove_worktree(repository, ticket)

    def _remove_worktree(self, repository: Repository, ticket: Ticket) -> None:
        assert ticket.worktree is not None
        assert ticket.branch is not None
        if ticket.worktree.exists():
            run(
                ["git", "worktree", "remove", "--force", str(ticket.worktree)],
                cwd=repository.root,
            )
        branch = run(
            ["git", "branch", "--list", ticket.branch],
            cwd=repository.root,
        )
        if branch:
            run(["git", "branch", "-D", ticket.branch], cwd=repository.root)

    def open_in_cursor(self, ticket: Ticket) -> None:
        if not ticket.worktree:
            raise CommandError("The ticket does not have a worktree yet.")
        try:
            subprocess.Popen(
                ["cursor", str(ticket.worktree)],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise CommandError(f"Could not open Cursor: {exc}") from exc
