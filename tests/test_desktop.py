from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ticket_shredder.desktop import (
    DesktopShortcutError,
    create_desktop_shortcut,
    main,
    project_root,
    resolve_launch_target,
)


class ProjectRootTests(unittest.TestCase):
    def test_project_root_finds_checkout(self) -> None:
        root = project_root()
        self.assertTrue((root / "pyproject.toml").is_file())
        self.assertIn('name = "ticket-shredder"', root.joinpath("pyproject.toml").read_text(encoding="utf-8"))


class ResolveLaunchTargetTests(unittest.TestCase):
    def test_prefers_pythonw_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            exe_dir = Path(directory)
            python = exe_dir / "python.exe"
            pythonw = exe_dir / "pythonw.exe"
            python.write_bytes(b"")
            pythonw.write_bytes(b"")
            with patch("ticket_shredder.desktop.sys.executable", str(python)):
                target, arguments = resolve_launch_target()
            self.assertEqual(target.resolve(), pythonw.resolve())
            self.assertEqual(arguments, "-m ticket_shredder")

    def test_falls_back_to_current_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            exe_dir = Path(directory)
            python = exe_dir / "python.exe"
            python.write_bytes(b"")
            with patch("ticket_shredder.desktop.sys.executable", str(python)):
                target, arguments = resolve_launch_target()
            self.assertEqual(target.resolve(), python.resolve())
            self.assertEqual(arguments, "-m ticket_shredder")


class CreateDesktopShortcutTests(unittest.TestCase):
    def test_rejects_non_windows(self) -> None:
        with patch("ticket_shredder.desktop.sys.platform", "linux"):
            with self.assertRaisesRegex(DesktopShortcutError, "only supported on Windows"):
                create_desktop_shortcut(desktop=Path("unused"))

    def test_creates_shortcut_via_powershell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            desktop = Path(directory)
            workdir = desktop / "project"
            workdir.mkdir()
            shortcut = desktop / "Ticket Shredder.lnk"

            def fake_run(args, **_kwargs):
                self.assertEqual(args[0], "powershell")
                script = args[args.index("-Command") + 1]
                self.assertIn("WScript.Shell", script)
                self.assertIn("-m ticket_shredder", script)
                self.assertIn(str(shortcut).replace("'", "''"), script)
                shortcut.write_bytes(b"lnk")
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            with (
                patch("ticket_shredder.desktop.sys.platform", "win32"),
                patch("ticket_shredder.desktop.subprocess.run", side_effect=fake_run),
                patch(
                    "ticket_shredder.desktop.resolve_launch_target",
                    return_value=(Path(sys.executable), "-m ticket_shredder"),
                ),
            ):
                result = create_desktop_shortcut(
                    desktop=desktop,
                    working_directory=workdir,
                )

            self.assertEqual(result, shortcut)
            self.assertTrue(shortcut.is_file())

    def test_reports_powershell_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            desktop = Path(directory)

            def fake_run(args, **_kwargs):
                return subprocess.CompletedProcess(
                    args, 1, stdout="", stderr="access denied"
                )

            with (
                patch("ticket_shredder.desktop.sys.platform", "win32"),
                patch("ticket_shredder.desktop.subprocess.run", side_effect=fake_run),
                patch(
                    "ticket_shredder.desktop.resolve_launch_target",
                    return_value=(Path(sys.executable), "-m ticket_shredder"),
                ),
            ):
                with self.assertRaisesRegex(DesktopShortcutError, "access denied"):
                    create_desktop_shortcut(desktop=desktop)

    def test_main_prints_created_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shortcut = Path(directory) / "Ticket Shredder.lnk"
            with patch(
                "ticket_shredder.desktop.create_desktop_shortcut",
                return_value=shortcut,
            ):
                code = main([])
            self.assertEqual(code, 0)

    def test_main_returns_error_status(self) -> None:
        with patch(
            "ticket_shredder.desktop.create_desktop_shortcut",
            side_effect=DesktopShortcutError("nope"),
        ):
            code = main([])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
