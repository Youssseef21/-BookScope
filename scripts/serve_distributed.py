"""Open the BookScope page on the sharded cluster (mongos, port 27200).

    python scripts/serve_distributed.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookscope.web import main

if __name__ == "__main__":
    main(port=8001, distributed=True)
