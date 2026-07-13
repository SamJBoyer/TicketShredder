from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GOODBYE_WORLD = REPO_ROOT / "goodbye world"


class GoodbyeWorldFileTests(unittest.TestCase):
    def test_goodbye_world_file_exists(self) -> None:
        self.assertTrue(GOODBYE_WORLD.is_file(), f"missing file: {GOODBYE_WORLD}")

    def test_goodbye_world_file_contents(self) -> None:
        self.assertEqual(GOODBYE_WORLD.read_text(encoding="utf-8"), "goodbye world\n")
