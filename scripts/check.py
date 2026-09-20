"""Run tests with an isolated, workspace-owned temporary directory."""
import sys
import uuid
from pathlib import Path
import pytest

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
temp = root / 'test-results'
temp.mkdir(exist_ok=True)
raise SystemExit(pytest.main([str(root / 'tests'), '-q', '--tb=short', '--basetemp=' + str(temp / uuid.uuid4().hex)]))
