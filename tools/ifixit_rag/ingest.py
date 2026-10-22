"""File entry point; also supports `python -m tools.ifixit_rag`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.ifixit_rag.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
