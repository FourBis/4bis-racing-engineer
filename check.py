"""Suite reproducible, sin descargar frameworks."""
import sys
import unittest
from pathlib import Path

root = Path(__file__).parent
sys.path.insert(0, str(root / "src"))
result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(root / "tests")))
raise SystemExit(not result.wasSuccessful())
