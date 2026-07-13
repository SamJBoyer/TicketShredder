from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = REPO_ROOT / "hello world"


class HelloWorldFileTests(unittest.TestCase):
    def test_hello_world_file_exists(self) -> None:
        self.assertTrue(HELLO_WORLD.is_file(), f"missing file: {HELLO_WORLD}")

    def test_hello_world_file_contents(self) -> None:
        self.assertEqual(HELLO_WORLD.read_text(encoding="utf-8"), "hello world\n")
