"""Root pytest conftest.

Ensures the backend package (backend/, where main.py and storage.py live),
the repo root, and tests/ (where postgres_test_support.py lives) are all
importable regardless of which directory a given test file happens to sit
in — test_expenses.py is at the repo root, everything else is under tests/,
and both need `import main` and `from postgres_test_support import ...` to
resolve the same way pytest invokes them from any location.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
for _path in (_ROOT / "backend", _ROOT, _ROOT / "tests"):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)
