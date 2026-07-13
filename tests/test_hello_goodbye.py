from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HELLO_GOODBYE = REPO_ROOT / "hello goodbye"


class HelloGoodbyeFileTests(unittest.TestCase):
    def test_hello_goodbye_file_exists(self) -> None:
        self.assertTrue(HELLO_GOODBYE.is_file(), f"missing file: {HELLO_GOODBYE}")

    def test_hello_goodbye_file_contents(self) -> None:
        self.assertEqual(HELLO_GOODBYE.read_text(encoding="utf-8"), "hello goodbye\n")
