"""Hide Windows console windows spawned for helper processes.

The Cursor SDK launches ``cursor-sdk-bridge.cmd`` via ``asyncio.create_subprocess_exec``.
On Windows that opens a visible CMD window; closing it kills the bridge and the
agent run. Ticket Shredder avoids that by:

1. Preferring ``node.exe`` + the bridge script directly (no ``cmd.exe`` wrapper).
2. Injecting ``CREATE_NO_WINDOW`` into asyncio / subprocess launches while the
   bridge starts, so console-subsystem binaries stay invisible.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path


def subprocess_no_window_flags() -> int:
    """Return Windows creation flags that suppress a new console window."""
    if sys.platform != "win32":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def merge_creationflags(existing: int | None = None) -> int:
    """OR existing creation flags with ``CREATE_NO_WINDOW`` on Windows."""
    return int(existing or 0) | subprocess_no_window_flags()


def bridge_command_without_console(
    launcher: str | Path | None = None,
) -> list[str] | None:
    """
    Resolve a bridge argv that does not go through a ``.cmd`` wrapper.

    Returns ``None`` when the default SDK launcher should be used unchanged
    (non-Windows, or when the expected ``node.exe`` + script layout is absent).
    """
    if sys.platform != "win32":
        return None

    path = Path(launcher) if launcher is not None else _default_bridge_launcher()
    if path is None:
        return None
    if path.suffix.lower() != ".cmd":
        return None

    node = path.parent / "node.exe"
    script = path.parent.parent / "dist" / "bin" / "cursor-sdk-bridge.js"
    if not node.is_file() or not script.is_file():
        return None
    return [str(node), str(script)]


def _default_bridge_launcher() -> Path | None:
    try:
        from cursor_sdk._vendor import resolve_bridge_path
    except ImportError:
        return None
    try:
        return Path(resolve_bridge_path())
    except Exception:
        return None


def launch_bridge_command(
    command: Sequence[str] | str | None = None,
) -> Sequence[str] | str | None:
    """
    Choose the command passed to ``AsyncClient.launch_bridge``.

    When ``command`` is omitted, rewrite the SDK's Windows ``.cmd`` launcher
    into a direct ``node.exe`` invocation when possible.
    """
    if command is not None:
        return command
    return bridge_command_without_console()


@contextmanager
def suppress_console_windows() -> Iterator[None]:
    """
    Temporarily force Windows helper processes to start without a console.

    Patches ``asyncio.create_subprocess_exec`` and ``subprocess.Popen`` so the
    Cursor SDK bridge (and any immediate Python-spawned helpers) inherit
    ``CREATE_NO_WINDOW``. No-op on non-Windows platforms.
    """
    if sys.platform != "win32" or subprocess_no_window_flags() == 0:
        yield
        return

    original_async_exec = asyncio.create_subprocess_exec
    original_popen = subprocess.Popen

    async def create_subprocess_exec_no_window(*args, **kwargs):
        kwargs["creationflags"] = merge_creationflags(kwargs.get("creationflags"))
        return await original_async_exec(*args, **kwargs)

    def popen_no_window(*args, **kwargs):
        kwargs["creationflags"] = merge_creationflags(kwargs.get("creationflags"))
        return original_popen(*args, **kwargs)

    asyncio.create_subprocess_exec = create_subprocess_exec_no_window  # type: ignore[assignment]
    subprocess.Popen = popen_no_window  # type: ignore[assignment,misc]
    try:
        yield
    finally:
        asyncio.create_subprocess_exec = original_async_exec  # type: ignore[assignment]
        subprocess.Popen = original_popen  # type: ignore[assignment,misc]
