#!/usr/bin/env python3
import copy
from pathlib import Path

import pytest

CSRF = "test-csrf-token"

GENERATE_PAYLOAD = {
    "source_dataset": "testds",
    "theme": "user_specified",
    "reading_level": "C",
    "model": "test-model",
    "section": 1,
    "custom_theme_text": "a castle in the clouds",
}


def _register(client, email, password="password123"):
    with client.session_transaction() as sess:
        sess["csrf"] = CSRF
    return client.post(
        "/register", data={"email": email, "password": password, "csrf_token": CSRF}
    )


@pytest.fixture()
def fake_pipeline(app_module, monkeypatch):
    """Stub sentence generation (OpenAI) and Phase 5 (PDF); count generations."""
    calls = {"generate": 0}

    def fake_generate(payload, **kwargs):
        calls["generate"] += 1
        generated = copy.deepcopy(payload)
        generated["output"] = {"subtitle": "Fake Subtitle"}
        return generated

    # Each caller binds generate_sentences into its own namespace at import
    # time, so every binding has to be patched.
    import phase2
    from Libraries import user_pipeline

    monkeypatch.setattr(phase2, "generate_sentences", fake_generate)
    monkeypatch.setattr(user_pipeline, "generate_sentences", fake_generate)
    monkeypatch.setattr(app_module, "generate_sentences", fake_generate)
    monkeypatch.setattr(app_module, "run_phase5_with_json", lambda payload: b"%PDF-fake")
    return calls


def _db_root(app_module):
    import os

    return Path(os.environ["VOCAB_HUNTERS_DB_PATH"])


def test_logged_in_custom_theme_persists_and_caches(client, app_module, fake_pipeline):
    _register(client, "creator@example.com")
    resp = client.post("/generate", json=GENERATE_PAYLOAD)
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    my_url = resp.headers.get("X-My-Worksheet-Url")
    assert my_url and my_url.startswith("/my/worksheet?")
    assert "theme=u--" in my_url

    users_root = _db_root(app_module) / "users"
    theme_files = list(users_root.glob("*/user_themes/*.json"))
    assert len(theme_files) == 1
    cache_files = list(users_root.glob("*/responses_datastore/**/*.json"))
    assert len(cache_files) == 1
    assert cache_files[0].name == "1.json"
    # Logged-in generation must not touch the legacy global user_themes dir.
    assert list((_db_root(app_module) / "user_themes").glob("*")) == []

    # Second generation with the same text -> episode 2, same theme record.
    resp = client.post("/generate", json=GENERATE_PAYLOAD)
    assert resp.status_code == 200
    cache_files = sorted(
        p.name for p in users_root.glob("*/responses_datastore/**/*.json")
    )
    assert cache_files == ["1.json", "2.json"]
    assert len(list(users_root.glob("*/user_themes/*.json"))) == 1
    assert fake_pipeline["generate"] == 2


def test_my_routes_list_and_replay_from_cache(client, app_module, fake_pipeline):
    _register(client, "replayer@example.com")
    resp = client.post("/generate", json=GENERATE_PAYLOAD)
    my_url = resp.headers["X-My-Worksheet-Url"]
    generation_calls = fake_pipeline["generate"]

    page = client.get("/my/worksheets")
    assert page.status_code == 200
    assert b"castle" in page.data.lower()

    viewer = client.get(my_url)
    assert viewer.status_code == 200
    assert b'"mode": "user"' in viewer.data

    pdf_url = my_url.replace("/my/worksheet?", "/my/worksheet_pdf?")
    pdf = client.get(pdf_url)
    assert pdf.status_code == 200
    assert pdf.data == b"%PDF-fake"
    # Replay must come from the cache, not a new OpenAI call.
    assert fake_pipeline["generate"] == generation_calls

    episodes = client.get(my_url.replace("/my/worksheet?", "/my/episodes?"))
    assert episodes.status_code == 200
    assert len(episodes.get_json()["episodes"]) == 1

    themes = client.get("/my/themes").get_json()["themes"]
    assert len(themes) == 1
    assert themes[0]["id"].startswith("u--")


def test_user_content_is_private(client, app_module, fake_pipeline):
    _register(client, "owner@example.com")
    my_url = client.post("/generate", json=GENERATE_PAYLOAD).headers["X-My-Worksheet-Url"]
    pdf_url = my_url.replace("/my/worksheet?", "/my/worksheet_pdf?")

    other = app_module.app.test_client()
    _register(other, "other@example.com")
    assert other.get(pdf_url).status_code == 404
    assert b"Nothing saved yet" in other.get("/my/worksheets").data


def test_anonymous_custom_theme_unchanged(client, app_module, fake_pipeline):
    payload = dict(GENERATE_PAYLOAD)
    payload["custom_theme_text"] = "a lighthouse on the moon"
    resp = client.post("/generate", json=payload)
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.headers.get("X-My-Worksheet-Url") is None

    legacy = _db_root(app_module) / "user_themes" / "a_lighthouse_on_the_moon.txt"
    assert legacy.is_file()


def test_anonymous_cannot_use_user_keys(client, app_module, fake_pipeline):
    payload = dict(GENERATE_PAYLOAD)
    payload["theme"] = "u--anything"
    del payload["custom_theme_text"]
    resp = client.post("/generate", json=payload)
    assert resp.status_code == 401

    assert client.get("/my/worksheets", headers={"Accept": "text/html"}).status_code == 302
