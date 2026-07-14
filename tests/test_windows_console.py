from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from ticket_shredder.agent_runner import CursorAgentRunner
from ticket_shredder.model import Ticket
from ticket_shredder.windows_console import (
    bridge_command_without_console,
    launch_bridge_command,
    merge_creationflags,
    subprocess_no_window_flags,
    suppress_console_windows,
)


class SubprocessNoWindowFlagsTests(unittest.TestCase):
    def test_flags_are_create_no_window_on_windows(self) -> None:
        if sys.platform != "win32":
            self.assertEqual(subprocess_no_window_flags(), 0)
            return
        self.assertEqual(
            subprocess_no_window_flags(),
            subprocess.CREATE_NO_WINDOW,
        )
        self.assertEqual(
            merge_creationflags(0x1),
            0x1 | subprocess.CREATE_NO_WINDOW,
        )


class BridgeCommandWithoutConsoleTests(unittest.TestCase):
    def test_rewrites_cmd_launcher_to_node_and_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            script_dir = root / "dist" / "bin"
            bin_dir.mkdir(parents=True)
            script_dir.mkdir(parents=True)
            launcher = bin_dir / "cursor-sdk-bridge.cmd"
            node = bin_dir / "node.exe"
            script = script_dir / "cursor-sdk-bridge.js"
            launcher.write_text("@echo off\n", encoding="utf-8")
            node.write_bytes(b"")
            script.write_text("// bridge\n", encoding="utf-8")

            with patch("ticket_shredder.windows_console.sys.platform", "win32"):
                command = bridge_command_without_console(launcher)

            self.assertEqual(command, [str(node), str(script)])

    def test_returns_none_when_node_layout_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            launcher = Path(directory) / "cursor-sdk-bridge.cmd"
            launcher.write_text("@echo off\n", encoding="utf-8")
            with patch("ticket_shredder.windows_console.sys.platform", "win32"):
                self.assertIsNone(bridge_command_without_console(launcher))

    def test_returns_none_on_non_windows(self) -> None:
        with patch("ticket_shredder.windows_console.sys.platform", "linux"):
            self.assertIsNone(
                bridge_command_without_console(Path("cursor-sdk-bridge.cmd"))
            )

    def test_launch_bridge_command_preserves_explicit_command(self) -> None:
        explicit = ["custom-bridge"]
        self.assertEqual(launch_bridge_command(explicit), explicit)


class SuppressConsoleWindowsTests(unittest.TestCase):
    def test_injects_create_no_window_into_asyncio_exec(self) -> None:
        if sys.platform != "win32":
            self.skipTest("Windows-only creationflags behavior")

        captured: dict[str, object] = {}

        async def fake_exec(*_args, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        async def exercise() -> None:
            with patch("asyncio.create_subprocess_exec", new=fake_exec):
                with suppress_console_windows():
                    await asyncio.create_subprocess_exec("node.exe", "bridge.js")

        asyncio.run(exercise())
        self.assertEqual(
            captured.get("creationflags"),
            subprocess.CREATE_NO_WINDOW,
        )

    def test_is_noop_on_non_windows(self) -> None:
        original = asyncio.create_subprocess_exec
        with patch("ticket_shredder.windows_console.sys.platform", "linux"):
            with suppress_console_windows():
                self.assertIs(asyncio.create_subprocess_exec, original)


class AgentRunnerBridgeLaunchTests(unittest.TestCase):
    def test_prompt_async_launches_bridge_without_console(self) -> None:
        ticket = Ticket(13, "Hide CMD", "body", "https://example.test/13")
        worktree = Path(tempfile.gettempdir())
        rewritten = [r"C:\node.exe", r"C:\bridge.js"]

        fake_run = MagicMock()
        fake_run.wait = AsyncMock(
            return_value=MagicMock(status="finished", result="ok")
        )

        fake_agent = MagicMock()
        fake_agent.send = AsyncMock(return_value=fake_run)
        agent_cm = MagicMock()
        agent_cm.__aenter__ = AsyncMock(return_value=fake_agent)
        agent_cm.__aexit__ = AsyncMock(return_value=None)

        fake_client = MagicMock()
        fake_client.create_agent = AsyncMock(return_value=agent_cm)
        client_cm = MagicMock()
        client_cm.__aenter__ = AsyncMock(return_value=fake_client)
        client_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "cursor_sdk.AsyncClient.launch_bridge",
                new_callable=AsyncMock,
                return_value=client_cm,
            ) as launch_bridge,
            patch("cursor_sdk.LocalAgentOptions") as local_options,
            patch(
                "ticket_shredder.agent_runner.suppress_console_windows"
            ) as suppress_cm,
            patch(
                "ticket_shredder.agent_runner.launch_bridge_command",
                return_value=rewritten,
            ),
        ):
            suppress_cm.return_value.__enter__ = MagicMock(return_value=None)
            suppress_cm.return_value.__exit__ = MagicMock(return_value=False)
            local_options.return_value = MagicMock()

            result = asyncio.run(
                CursorAgentRunner(model="auto")._prompt_async(
                    ticket, worktree, "test-key"
                )
            )

        self.assertEqual(getattr(result, "status", None), "finished")
        launch_bridge.assert_awaited_once()
        kwargs = launch_bridge.await_args.kwargs
        self.assertEqual(kwargs["command"], rewritten)
        self.assertEqual(kwargs["workspace"], worktree)
        suppress_cm.assert_called_once()


if __name__ == "__main__":
    unittest.main()
