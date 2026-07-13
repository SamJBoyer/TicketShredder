"""Create a Windows desktop shortcut that launches Ticket Shredder."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

SHORTCUT_NAME = "Ticket Shredder.lnk"
SHORTCUT_DESCRIPTION = "Launch Ticket Shredder"


class DesktopShortcutError(RuntimeError):
    """Raised when the desktop shortcut cannot be created."""


def project_root() -> Path:
    """Return the repository root when running from a source checkout."""
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        manifest = candidate / "pyproject.toml"
        if not manifest.is_file():
            continue
        try:
            text = manifest.read_text(encoding="utf-8")
        except OSError:
            continue
        if 'name = "ticket-shredder"' in text:
            return candidate
    return Path.cwd()


def desktop_directory() -> Path:
    """Resolve the current user's Desktop folder on Windows."""
    if sys.platform != "win32":
        raise DesktopShortcutError(
            "Desktop shortcuts are only supported on Windows."
        )

    try:
        import ctypes
    except ImportError as exc:  # pragma: no cover - stdlib on CPython
        raise DesktopShortcutError("ctypes is required on Windows.") from exc

    buf = ctypes.create_unicode_buffer(260)
    # CSIDL_DESKTOPDIRECTORY — the real Desktop folder, including OneDrive.
    result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)
    if result != 0 or not buf.value:
        fallback = Path(os.path.expandvars(r"%USERPROFILE%\Desktop"))
        if fallback.is_dir():
            return fallback
        raise DesktopShortcutError("Could not locate the Desktop folder.")
    return Path(buf.value)


def resolve_launch_target() -> tuple[Path, str]:
    """
    Choose an executable and arguments that start Ticket Shredder.

    Prefer ``pythonw.exe -m ticket_shredder`` so the GUI opens without a
    console window. Fall back to the current interpreter when needed.
    """
    exe_dir = Path(sys.executable).resolve().parent
    pythonw = exe_dir / "pythonw.exe"
    target = pythonw if pythonw.is_file() else Path(sys.executable)
    return target, "-m ticket_shredder"


def _ps_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def create_desktop_shortcut(
    *,
    desktop: Path | None = None,
    working_directory: Path | None = None,
    shortcut_name: str = SHORTCUT_NAME,
) -> Path:
    """
    Create or replace a desktop ``.lnk`` that launches Ticket Shredder.

    Returns the path to the created shortcut.
    """
    if sys.platform != "win32":
        raise DesktopShortcutError(
            "Desktop shortcuts are only supported on Windows."
        )

    desktop_dir = desktop if desktop is not None else desktop_directory()
    desktop_dir.mkdir(parents=True, exist_ok=True)
    shortcut_path = desktop_dir / shortcut_name
    target, arguments = resolve_launch_target()
    workdir = working_directory if working_directory is not None else project_root()
    icon = f"{target},0"

    script = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$shell = New-Object -ComObject WScript.Shell\n"
        f"$shortcut = $shell.CreateShortcut({_ps_single_quote(str(shortcut_path))})\n"
        f"$shortcut.TargetPath = {_ps_single_quote(str(target))}\n"
        f"$shortcut.Arguments = {_ps_single_quote(arguments)}\n"
        f"$shortcut.WorkingDirectory = {_ps_single_quote(str(workdir))}\n"
        f"$shortcut.Description = {_ps_single_quote(SHORTCUT_DESCRIPTION)}\n"
        f"$shortcut.IconLocation = {_ps_single_quote(icon)}\n"
        "$shortcut.Save()\n"
    )

    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DesktopShortcutError(
            f"Could not create desktop shortcut: {exc}"
        ) from exc

    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise DesktopShortcutError(
            message or "PowerShell failed to create the desktop shortcut."
        )
    if not shortcut_path.is_file():
        raise DesktopShortcutError(
            f"Shortcut was not created at {shortcut_path}."
        )
    return shortcut_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a desktop icon that launches Ticket Shredder.",
    )
    parser.add_argument(
        "--desktop",
        type=Path,
        default=None,
        help="Override the Desktop folder (defaults to the user Desktop).",
    )
    parser.add_argument(
        "--working-directory",
        type=Path,
        default=None,
        help="Working directory for the shortcut (defaults to the project root).",
    )
    args = parser.parse_args(argv)

    try:
        path = create_desktop_shortcut(
            desktop=args.desktop,
            working_directory=args.working_directory,
        )
    except DesktopShortcutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Created desktop shortcut: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
