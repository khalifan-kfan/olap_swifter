"""
Runs every test in a throwaway repo root: the routes and config are copied,
the registry is seeded from tests/fixtures/schemas, and the workspace (review
page, warehouses, process log) is temporary, so tests never touch the live
registry or the user's workspace.
"""

import os
import shutil
from pathlib import Path

import pytest

RESOURCES = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True, scope="session")
def isolated_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("repo")
    (root / "resources").mkdir()
    for name in ("ROUTES.yaml", "config.yaml"):
        shutil.copy(RESOURCES / name, root / "resources" / name)
    shutil.copytree(Path(__file__).parent / "fixtures" / "schemas", root / "results_workspace" / "schemas")
    env = {"OLAP_SWIFTER_ROOT": str(root), "OLAP_SWIFTER_WORKSPACE": str(root / "results_workspace")}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    yield root
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
