#!/usr/bin/env python3
"""Shared fixtures. The app module reads env vars and initializes the auth DB
at import time (no app factory), so the environment must be fully prepared
before `import app` — hence the session-scoped module fixture."""

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REFERENCE_DATA = {
    "source_datasets.json": [
        {"key_name": "testds", "title": "Test Dataset", "title_abbr": "TD"}
    ],
    "themes.json": [
        {
            "key_name": "space",
            "title": "Space",
            "title_abbr": "SP",
            "css_class": "",
            "ui_title": "Space",
            "ui_subtitle": "",
        },
        {
            "key_name": "user_specified",
            "title": "Create Your Own Theme",
            "title_abbr": "Custom",
            "css_class": "",
            "ui_title": "Create Your Own",
            "ui_subtitle": "",
        },
    ],
    "models.json": [
        {"key_name": "test-model", "title": "Test Model", "is_default": True}
    ],
}

TEST_DATASET = {
    "title": "Test Dataset",
    "dataset_title": "Test Dataset",
    "dataset_type": "vocabulary",
    "sections": [
        {
            "section": 1,
            "entries": [
                {
                    "word": "cat",
                    "part_of_speech": "noun",
                    "definition": "a small domesticated animal",
                    "def_num": 1,
                }
            ],
        }
    ],
}


def _seed_database_dir(db_root: Path) -> None:
    reference_dir = db_root / "reference_data"
    reference_dir.mkdir(parents=True)
    for filename, payload in REFERENCE_DATA.items():
        with (reference_dir / filename).open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    datasets_dir = db_root / "source_datasets"
    datasets_dir.mkdir(parents=True)
    with (datasets_dir / "testds.json").open("w", encoding="utf-8") as f:
        json.dump(TEST_DATASET, f, ensure_ascii=False, indent=2)

    for subdir in ("themes", "user_themes", "responses_datastore"):
        (db_root / subdir).mkdir(parents=True)
    (db_root / "prompt.txt").write_text("Test prompt {reading_level}", encoding="utf-8")


@pytest.fixture(scope="session")
def app_module(tmp_path_factory):
    db_root = tmp_path_factory.mktemp("vocab_hunters_db")
    _seed_database_dir(db_root)
    os.environ["VOCAB_HUNTERS_DB_PATH"] = str(db_root)
    os.environ["VOCAB_HUNTERS_SECRET_KEY"] = "test-secret-key"
    os.environ["SESSION_COOKIE_SECURE"] = "0"

    import app as app_module_

    app_module_.app.config.update(TESTING=True)
    return app_module_


@pytest.fixture()
def client(app_module):
    return app_module.app.test_client()


@pytest.fixture(autouse=True)
def _clear_ip_reg_attempts(app_module):
    """Wipe per-IP registration counters before each test so the shared
    session-scoped DB doesn't accumulate 127.0.0.1 attempts across tests."""
    import sqlite3 as _sqlite3

    db_path = Path(os.environ["VOCAB_HUNTERS_DB_PATH"]) / "auth.sqlite3"
    if db_path.exists():
        conn = _sqlite3.connect(str(db_path))
        conn.execute("DELETE FROM ip_reg_attempts")
        conn.commit()
        conn.close()
