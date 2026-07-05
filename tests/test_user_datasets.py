#!/usr/bin/env python3
import io
import json
from pathlib import Path

import pytest

CSRF = "test-csrf-token"

VALID_DATASET = {
    "title": "My Words",
    "sections": [
        {
            "section": 1,
            "entries": [
                {
                    "word": "harbor",
                    "part_of_speech": "noun",
                    "definition": "a sheltered body of water",
                    "def_num": 1,
                }
            ],
        }
    ],
}


def _register(client, email, password="password123"):
    with client.session_transaction() as sess:
        sess["csrf"] = CSRF
    return client.post(
        "/register", data={"email": email, "password": password, "csrf_token": CSRF}
    )


@pytest.fixture()
def fake_pipeline(app_module, monkeypatch):
    calls = {"phase4": 0}

    def fake_phase4(phase3_json, **kwargs):
        calls["phase4"] += 1
        payload = json.loads(phase3_json)
        payload["output"] = {"subtitle": "Fake Subtitle"}
        return json.dumps(payload, ensure_ascii=False)

    import phase4

    monkeypatch.setattr(phase4, "run_phase4_with_json", fake_phase4)
    monkeypatch.setattr(app_module, "run_phase4_with_json", fake_phase4)
    monkeypatch.setattr(app_module, "run_phase5_with_json", lambda payload: b"%PDF-fake")
    return calls


def test_upload_list_and_sections(client, app_module):
    _register(client, "uploader@example.com")

    resp = client.post("/my/datasets", json={"title": "My Words", "dataset": VALID_DATASET})
    assert resp.status_code == 201
    dataset_id = resp.get_json()["id"]
    assert dataset_id == "u--My_Words"

    listed = client.get("/my/datasets").get_json()["datasets"]
    assert [d["id"] for d in listed] == [dataset_id]
    assert listed[0]["section_count"] == 1

    sections = client.get(f"/sections/{dataset_id}")
    assert sections.status_code == 200
    assert sections.get_json()["sections"] == [1]

    # Duplicate is a conflict unless overwrite is requested.
    assert client.post("/my/datasets", json={"title": "My Words", "dataset": VALID_DATASET}).status_code == 409
    assert client.post(
        "/my/datasets", json={"title": "My Words", "dataset": VALID_DATASET, "overwrite": True}
    ).status_code == 201


def test_upload_rejects_bad_schema(client):
    _register(client, "badschema@example.com")

    resp = client.post("/my/datasets", json={"title": "Bad", "dataset": {"sections": "nope"}})
    assert resp.status_code == 400

    missing_word = {
        "title": "Bad",
        "sections": [{"section": 1, "entries": [{"part_of_speech": "noun", "definition": "x"}]}],
    }
    resp = client.post("/my/datasets", json={"title": "Bad2", "dataset": missing_word})
    assert resp.status_code == 400
    assert "word" in resp.get_json()["error"]


def test_hostile_title_is_sanitized(client, app_module):
    import os

    _register(client, "hostile@example.com")
    resp = client.post(
        "/my/datasets", json={"title": "../../evil", "dataset": VALID_DATASET}
    )
    assert resp.status_code == 201
    stem = resp.get_json()["stem"]
    assert "/" not in stem and ".." not in stem

    users_root = Path(os.environ["VOCAB_HUNTERS_DB_PATH"]) / "users"
    saved = list(users_root.glob(f"*/source_datasets/{stem}.json"))
    assert len(saved) == 1
    assert (Path(os.environ["VOCAB_HUNTERS_DB_PATH"]) / "evil.json").exists() is False


def test_multipart_form_upload_requires_csrf(client):
    _register(client, "formupload@example.com")

    file_data = {
        "dataset_file": (io.BytesIO(json.dumps(VALID_DATASET).encode("utf-8")), "form_set.json")
    }
    resp = client.post("/my/datasets", data=file_data, content_type="multipart/form-data")
    assert resp.status_code == 400

    with client.session_transaction() as sess:
        sess["csrf"] = CSRF
    file_data = {
        "dataset_file": (io.BytesIO(json.dumps(VALID_DATASET).encode("utf-8")), "form_set.json"),
        "csrf_token": CSRF,
    }
    resp = client.post("/my/datasets", data=file_data, content_type="multipart/form-data")
    assert resp.status_code == 302
    assert "dataset_saved" in resp.headers["Location"]

    listed = client.get("/my/datasets").get_json()["datasets"]
    assert any(d["stem"] == "form_set" for d in listed)


def test_oversized_upload_is_rejected(client):
    _register(client, "bigfile@example.com")
    big = {"title": "Big", "sections": [{"section": 1, "entries": [{"word": "x" * 400}]}]}
    blob = json.dumps(big).encode("utf-8") + b" " * (3 * 1024 * 1024)
    resp = client.post("/my/datasets", data=blob, content_type="application/json")
    assert resp.status_code == 413


def test_generate_with_user_dataset(client, app_module, fake_pipeline):
    _register(client, "dsgen@example.com")
    dataset_id = client.post(
        "/my/datasets", json={"title": "Gen Words", "dataset": VALID_DATASET}
    ).get_json()["id"]

    # User dataset + global theme
    resp = client.post(
        "/generate",
        json={
            "source_dataset": dataset_id,
            "theme": "space",
            "reading_level": "C",
            "model": "test-model",
            "section": 1,
        },
    )
    assert resp.status_code == 200
    my_url = resp.headers["X-My-Worksheet-Url"]
    assert f"source_dataset={dataset_id}" in my_url

    # Replay from cache: no extra OpenAI call
    calls_after_generate = fake_pipeline["phase4"]
    pdf = client.get(my_url.replace("/my/worksheet?", "/my/worksheet_pdf?"))
    assert pdf.status_code == 200
    assert fake_pipeline["phase4"] == calls_after_generate

    # User dataset + custom theme text
    resp = client.post(
        "/generate",
        json={
            "source_dataset": dataset_id,
            "theme": "user_specified",
            "reading_level": "C",
            "model": "test-model",
            "section": 1,
            "custom_theme_text": "pirates who run a bakery",
        },
    )
    assert resp.status_code == 200
    assert "theme=u--" in resp.headers["X-My-Worksheet-Url"]


def test_anonymous_cannot_touch_user_datasets(client, fake_pipeline):
    assert client.get("/sections/u--anything").status_code == 404
    resp = client.post(
        "/generate",
        json={
            "source_dataset": "u--anything",
            "theme": "space",
            "reading_level": "C",
            "model": "test-model",
            "section": 1,
        },
    )
    assert resp.status_code == 401
    assert client.post("/my/datasets", json={}).status_code == 401
