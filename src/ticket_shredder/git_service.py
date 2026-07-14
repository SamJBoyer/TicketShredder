from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

from .layout import (
    AGENT_BRANCH,
    BARE_DIRNAME,
    DEV_BRANCH,
    agents_path,
    bare_path,
    dev_path,
    is_bare_layout,
    is_legacy_clone,
    project_home,
    ticket_path,
)
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
    """Manage one bare store + agents/dev/ticket worktrees per hProject."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.expanduser().resolve()

    def validate_remote(self, remote_url: str) -> None:
        if not remote_url.strip():
            raise ValueError("Enter a Git repository URL.")
        run(["git", "ls-remote", remote_url], timeout=30)

    def acquire(self, remote_url: str) -> Repository:
        name = repository_name(remote_url)
        home = project_home(self.workspace_root, name)
        self.workspace_root.mkdir(parents=True, exist_ok=True)

        if is_legacy_clone(home):
            self._migrate_legacy_clone(home, remote_url)
        elif not is_bare_layout(home):
            if home.exists() and any(home.iterdir()):
                raise CommandError(
                    f"{home} exists but is not a Helmsman bare layout or git clone."
                )
            self._clone_bare(remote_url, home)
        else:
            self._assert_remote(home, remote_url)
            run(["git", "fetch", "--prune", "origin"], cwd=bare_path(home), timeout=300)

        self._ensure_integration_branches(home)
        agents = self._ensure_worktree(home, AGENT_BRANCH, agents_path(home))
        dev = self._ensure_worktree(home, DEV_BRANCH, dev_path(home))
        self._exclude_runtime_files(agents)
        self._sync_agents_from_origin(agents)
        self._reclaim_legacy_ticket_worktrees(home)
        return Repository(
            remote_url=remote_url,
            root=agents,
            home=home,
            bare=bare_path(home),
            agents=agents,
            dev=dev,
        )

    def _assert_remote(self, home: Path, remote_url: str) -> None:
        actual = run(["git", "remote", "get-url", "origin"], cwd=bare_path(home))
        if remote_identity(actual) != remote_identity(remote_url):
            raise CommandError(f"{home} is already connected to {actual}.")

    def _clone_bare(self, remote_url: str, home: Path) -> None:
        home.mkdir(parents=True, exist_ok=True)
        bare = bare_path(home)
        run(["git", "clone", "--bare", remote_url, str(bare)], timeout=600)
        run(["git", "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"], cwd=bare)
        run(["git", "fetch", "--prune", "origin"], cwd=bare, timeout=300)

    def _migrate_legacy_clone(self, home: Path, remote_url: str) -> None:
        """Convert a normal clone (+ old ticket worktrees) into bare + wt/*."""
        actual = run(["git", "remote", "get-url", "origin"], cwd=home)
        if remote_identity(actual) != remote_identity(remote_url):
            raise CommandError(f"{home} is already connected to {actual}.")

        run(["git", "fetch", "--prune", "origin"], cwd=home, timeout=300)
        dirty = run(["git", "status", "--porcelain", "-uno"], cwd=home)
        stash_ref = ""
        if dirty:
            # Preserve tracked edits (often made via TicketsPlease on agents).
            run(
                ["git", "stash", "push", "-m", "helmsman-layout-migrate", "--", "."],
                cwd=home,
            )
            stash_ref = "stash@{0}"

        ticket_branches = self._detach_linked_worktrees(home)
        self._ensure_local_branch(home, AGENT_BRANCH)
        self._ensure_local_branch(home, DEV_BRANCH)

        scratch_hold = home.parent / f".{home.name}.scratch-tmp"
        scratch_src = home / ".scratch"
        if scratch_hold.exists():
            shutil.rmtree(scratch_hold)
        if scratch_src.exists():
            shutil.move(str(scratch_src), str(scratch_hold))

        git_dir = home / ".git"
        bare = bare_path(home)
        if bare.exists():
            raise CommandError(f"{bare} already exists during legacy migration.")
        if not git_dir.exists():
            raise CommandError(f"{home} has no .git directory to convert.")
        try:
            git_dir.rename(bare)
        except OSError as exc:
            raise CommandError(
                f"Could not convert {git_dir} to bare layout. "
                "Close Cursor tabs / terminals using this folder and retry. "
                f"Detail: {exc}"
            ) from exc

        run(["git", "config", "core.bare", "true"], cwd=bare)
        run(
            [
                "git",
                "config",
                "remote.origin.fetch",
                "+refs/heads/*:refs/remotes/origin/*",
            ],
            cwd=bare,
        )
        run(["git", "worktree", "prune"], cwd=bare)
        run(["git", "fetch", "--prune", "origin"], cwd=bare, timeout=300)

        # Remove orphaned working-tree files left behind after .git → .bare.
        self._clear_workdir_files(home)

        agents = self._ensure_worktree(home, AGENT_BRANCH, agents_path(home))
        self._ensure_worktree(home, DEV_BRANCH, dev_path(home))

        if scratch_hold.exists():
            scratch_dst = agents / ".scratch"
            if scratch_dst.exists():
                shutil.rmtree(scratch_dst)
            shutil.move(str(scratch_hold), str(scratch_dst))

        self._exclude_runtime_files(agents)
        if stash_ref:
            try:
                run(["git", "stash", "pop"], cwd=dev_path(home))
            except CommandError:
                # Stash stays available on the bare; do not fail migration.
                pass

        for number, branch in ticket_branches:
            target = ticket_path(home, number)
            if target.exists():
                continue
            if run(["git", "branch", "--list", branch], cwd=bare):
                run(
                    ["git", "worktree", "add", str(target), branch],
                    cwd=bare,
                )

    @staticmethod
    def _clear_workdir_files(home: Path) -> None:
        """Delete leftover files after converting ``.git`` into ``.bare``."""
        try:
            for child in list(home.iterdir()):
                if child.name == BARE_DIRNAME:
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        except OSError as exc:
            raise CommandError(
                f"Could not clear {home} during migration. "
                "Close Cursor tabs / terminals using this folder and retry. "
                f"Detail: {exc}"
            ) from exc


    def _detach_linked_worktrees(self, home: Path) -> list[tuple[int, str]]:
        """Remove every linked worktree; keep ticket branches for reattach."""
        recovered: list[tuple[int, str]] = []
        listing = run(["git", "worktree", "list", "--porcelain"], cwd=home)
        blocks = [block for block in listing.split("\n\n") if block.strip()]
        home_resolved = home.resolve()
        for block in blocks:
            entries = {
                line.split(" ", 1)[0]: line.split(" ", 1)[1]
                for line in block.splitlines()
                if " " in line
            }
            path_str = entries.get("worktree")
            branch_ref = entries.get("branch", "")
            if not path_str:
                continue
            path = Path(path_str)
            if path.resolve() == home_resolved:
                continue
            branch = branch_ref.removeprefix("refs/heads/") if branch_ref else ""
            number: int | None = None
            if branch.startswith("ticket-shredder/issue-"):
                try:
                    number = int(branch.rsplit("-", 1)[-1])
                except ValueError:
                    number = None
            if number is None:
                try:
                    number = int(path.name)
                except ValueError:
                    number = None
            if number is not None and branch:
                recovered.append((number, branch))
            run(["git", "worktree", "remove", "--force", str(path)], cwd=home)

        legacy_root = self.workspace_root / "worktrees" / home.name
        if legacy_root.is_dir():
            for child in sorted(legacy_root.iterdir()):
                if not child.is_dir():
                    continue
                try:
                    number = int(child.name)
                except ValueError:
                    continue
                if any(n == number for n, _ in recovered):
                    continue
                try:
                    branch = run(["git", "branch", "--show-current"], cwd=child)
                except CommandError:
                    branch = f"ticket-shredder/issue-{number}"
                if branch:
                    recovered.append((number, branch))
                try:
                    run(["git", "worktree", "remove", "--force", str(child)], cwd=home)
                except CommandError:
                    shutil.rmtree(child, ignore_errors=True)
        return recovered

    def _reclaim_legacy_ticket_worktrees(self, home: Path) -> None:
        """If old worktrees/<name>/<n> still exist, reattach under wt/tickets."""
        legacy_root = self.workspace_root / "worktrees" / home.name
        if not legacy_root.is_dir():
            return
        bare = bare_path(home)
        for child in sorted(legacy_root.iterdir()):
            if not child.is_dir():
                continue
            try:
                number = int(child.name)
            except ValueError:
                continue
            target = ticket_path(home, number)
            if target.exists():
                continue
            try:
                branch = run(["git", "branch", "--show-current"], cwd=child)
            except CommandError:
                branch = f"ticket-shredder/issue-{number}"
            try:
                run(["git", "worktree", "remove", "--force", str(child)], cwd=bare)
            except CommandError:
                shutil.rmtree(child, ignore_errors=True)
            if branch and run(["git", "branch", "--list", branch], cwd=bare):
                if not target.exists():
                    run(["git", "worktree", "add", str(target), branch], cwd=bare)

    def _ensure_integration_branches(self, home: Path) -> None:
        bare = bare_path(home)
        self._ensure_branch_on_bare(bare, AGENT_BRANCH)
        self._ensure_branch_on_bare(bare, DEV_BRANCH)

    def _ensure_branch_on_bare(self, bare: Path, branch: str) -> None:
        local = run(["git", "branch", "--list", branch], cwd=bare)
        if local:
            return
        remote = run(
            ["git", "branch", "--remotes", "--list", f"origin/{branch}"],
            cwd=bare,
        )
        if remote:
            run(["git", "branch", "--track", branch, f"origin/{branch}"], cwd=bare)
            return
        # Prefer creating agents/dev from the other integration branch, else HEAD.
        for candidate in (AGENT_BRANCH, DEV_BRANCH, "main", "master"):
            if candidate == branch:
                continue
            if run(["git", "branch", "--list", candidate], cwd=bare):
                run(["git", "branch", branch, candidate], cwd=bare)
                return
            if run(
                ["git", "branch", "--remotes", "--list", f"origin/{candidate}"],
                cwd=bare,
            ):
                run(["git", "branch", branch, f"origin/{candidate}"], cwd=bare)
                return
        run(["git", "branch", branch, "HEAD"], cwd=bare)

    def _ensure_local_branch(self, cwd: Path, branch: str) -> None:
        if run(["git", "branch", "--list", branch], cwd=cwd):
            return
        remote = run(
            ["git", "branch", "--remotes", "--list", f"origin/{branch}"],
            cwd=cwd,
        )
        if remote:
            run(["git", "branch", "--track", branch, f"origin/{branch}"], cwd=cwd)
            return
        current = run(["git", "branch", "--show-current"], cwd=cwd)
        if current and current != branch:
            run(["git", "branch", branch, current], cwd=cwd)
        else:
            run(["git", "branch", branch], cwd=cwd)

    def _ensure_worktree(self, home: Path, branch: str, path: Path) -> Path:
        bare = bare_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            actual = run(["git", "branch", "--show-current"], cwd=path)
            if actual != branch:
                raise CommandError(f"{path} belongs to {actual}, not {branch}.")
            return path
        # If git still thinks this path is registered, prune first.
        run(["git", "worktree", "prune"], cwd=bare)
        run(["git", "worktree", "add", str(path), branch], cwd=bare)
        return path

    @staticmethod
    def _sync_agents_from_origin(agents: Path) -> None:
        remote = run(
            ["git", "branch", "--remotes", "--list", f"origin/{AGENT_BRANCH}"],
            cwd=agents,
        )
        if not remote:
            return
        run(["git", "merge", "--no-edit", f"origin/{AGENT_BRANCH}"], cwd=agents)

    @staticmethod
    def _exclude_runtime_files(root: Path) -> None:
        raw_path = run(["git", "rev-parse", "--git-path", "info/exclude"], cwd=root)
        exclude = Path(raw_path)
        if not exclude.is_absolute():
            exclude = root / exclude
        exclude.parent.mkdir(parents=True, exist_ok=True)
        content = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        pattern = ".scratch/"
        lines = content.splitlines()
        if pattern in lines:
            return
        lines = [line for line in lines if line != ".scratch/.itickets/auto/"]
        body = "\n".join(lines)
        separator = "" if not body or body.endswith("\n") else "\n"
        if body:
            body = f"{body}{separator}"
        exclude.write_text(f"{body}{pattern}\n", encoding="utf-8")

    def create_worktree(self, repository: Repository, ticket: Ticket) -> tuple[str, Path]:
        if repository.home is None or repository.bare is None:
            raise CommandError("Repository is missing bare-layout paths.")
        branch = f"ticket-shredder/issue-{ticket.number}"
        worktree = ticket_path(repository.home, ticket.number)
        worktree.parent.mkdir(parents=True, exist_ok=True)
        if worktree.exists():
            actual_branch = run(["git", "branch", "--show-current"], cwd=worktree)
            if actual_branch != branch:
                raise CommandError(
                    f"{worktree} belongs to {actual_branch}, not {branch}."
                )
            return branch, worktree
        existing_branch = run(
            ["git", "branch", "--list", branch],
            cwd=repository.bare,
        )
        command = ["git", "worktree", "add"]
        if existing_branch:
            command.extend([str(worktree), branch])
        else:
            command.extend(["-b", branch, str(worktree), AGENT_BRANCH])
        run(command, cwd=repository.bare)
        return branch, worktree

    @staticmethod
    def has_reviewable_changes(ticket: Ticket) -> bool:
        if not ticket.worktree:
            return False
        if run(["git", "status", "--porcelain"], cwd=ticket.worktree):
            return False
        ahead = run(
            ["git", "rev-list", "--count", f"{AGENT_BRANCH}..HEAD"],
            cwd=ticket.worktree,
        )
        return int(ahead) > 0

    def merge(self, repository: Repository, ticket: Ticket) -> str | None:
        if not ticket.branch or not ticket.worktree:
            raise CommandError("This ticket has no worktree to merge.")
        agents = repository.agents or repository.root
        dirty = run(["git", "status", "--porcelain", "-uno"], cwd=agents)
        if dirty:
            paths: list[str] = []
            for line in dirty.splitlines():
                if not line.strip():
                    continue
                # Prefer porcelain v1 ("XY path"); some Git builds emit "X path".
                if len(line) >= 4 and line[2] == " ":
                    path = line[3:]
                else:
                    path = line.split(maxsplit=1)[-1]
                if " -> " in path:
                    path = path.split(" -> ", 1)[-1]
                paths.append(path.strip().strip('"'))
            files = ", ".join(paths)
            raise CommandError(
                "The agents worktree has uncommitted changes "
                f"({files or 'tracked files'}). "
                "Keep human edits on the dev worktree, not agents."
            )
        run(["git", "fetch", "--prune", "origin"], cwd=agents, timeout=300)
        self._sync_agents_from_origin(agents)
        run(["git", "merge", "--no-ff", "--no-edit", ticket.branch], cwd=agents)
        run(["git", "push", "origin", AGENT_BRANCH], cwd=agents, timeout=300)
        try:
            self._remove_worktree(repository, ticket)
        except CommandError as exc:
            return f"Merged into {AGENT_BRANCH}; cleanup needs attention: {exc}"
        return None

    def dump(self, repository: Repository, ticket: Ticket) -> None:
        if not ticket.branch or not ticket.worktree:
            return
        # Push a safety backup when the ticket branch still has unique commits.
        try:
            ahead = run(
                ["git", "rev-list", "--count", f"{AGENT_BRANCH}..{ticket.branch}"],
                cwd=ticket.worktree,
            )
            if int(ahead) > 0:
                run(
                    ["git", "push", "-u", "origin", ticket.branch],
                    cwd=ticket.worktree,
                    timeout=300,
                )
        except CommandError:
            # Dump must still proceed; backup is best-effort.
            pass
        self._remove_worktree(repository, ticket)

    def _remove_worktree(self, repository: Repository, ticket: Ticket) -> None:
        assert ticket.worktree is not None
        assert ticket.branch is not None
        git_cwd = repository.bare or repository.root
        if ticket.worktree.exists():
            run(
                ["git", "worktree", "remove", "--force", str(ticket.worktree)],
                cwd=git_cwd,
            )
        branch = run(["git", "branch", "--list", ticket.branch], cwd=git_cwd)
        if branch:
            run(["git", "branch", "-D", ticket.branch], cwd=git_cwd)

    def open_in_cursor(self, ticket: Ticket) -> None:
        if not ticket.worktree:
            raise CommandError("The ticket does not have a worktree yet.")
        cursor_exe = shutil.which("cursor")
        if cursor_exe is None:
            raise CommandError(
                "Could not find 'cursor' on PATH. "
                "Make sure the Cursor CLI is installed and available."
            )
        try:
            subprocess.Popen(
                [cursor_exe, str(ticket.worktree)],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise CommandError(f"Could not open Cursor: {exc}") from exc
