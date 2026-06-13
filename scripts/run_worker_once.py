import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"

for import_path in (PROJECT_ROOT, BACKEND_DIR):
    import_path_str = str(import_path)
    if import_path_str not in sys.path:
        sys.path.insert(0, import_path_str)

from worker.app.runner import run_once  # noqa: E402

if __name__ == "__main__":
    result = run_once(run_limit=1)
    print(result)
