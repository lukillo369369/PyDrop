"""Repository entry point for standalone exported-graph builds."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graph_packaging import main


if __name__ == "__main__":
    raise SystemExit(main())
