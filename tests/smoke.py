#!/usr/bin/env python3
"""Fast check for a first install. Skips Piper, faster-whisper, kit renders and strobe renders.

Run from anywhere:

  python3 tests/smoke.py

The full suite is still:

  python3 -m unittest discover -s tests
"""
import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

NAMES = [
    "test_vstudio.Captions",
    "test_vstudio.Transcribe.test_word_match",
    "test_vstudio.Transcribe.test_keyterms",
    "test_vstudio.Config",
    "test_vstudio.DemoFallback",
    "test_first_run",
    "test_install_deps",
    "test_film_grade",
]


def main():
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for name in NAMES:
        suite.addTests(loader.loadTestsFromName(name))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
