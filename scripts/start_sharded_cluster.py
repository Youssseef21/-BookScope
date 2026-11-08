"""Démarre le cluster fragmenté décrit dans compose.sharded.yml.

    .venv\\Scripts\\python.exe scripts\\start_sharded_cluster.py

mongos : 27200. shard1 : 27101. shard2 : 27102. Configuration : 27100.
Le serveur unique du projet, sur le port 27017, n'est pas modifié.
Les scores F1 à F5 restent ceux de la base principale.
"""

from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("start_sharded.py")), run_name="__main__")
