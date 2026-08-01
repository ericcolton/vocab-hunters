#!/usr/bin/env python3
"""Coverage for the standard (global, anonymous) Phase 2 cache path.

This is the highest-traffic flow in the app: /generate with a global dataset
and global theme goes through Scripts/phase2.py:process_request, which calls
Phase 3, hands the parsed payload to generate_sentences(), serializes the
result to the cache, and replays it from disk on later requests.
"""

import copy
import json
import os
from pathlib import Path

import pytest

GENERATE_PAYLOAD = {
    "source_dataset": "testds",
    "theme": "space",
    "reading_level": "C",
    "model": "test-model",
    "section": 1,
    "header": "{source} - Section {section}",
    "footer": "Episode {episode} - {theme}",
    "answer_key_footer": "Answer Key",
}


@pytest.fixture()
def fake_pipeline(app_module, monkeypatch):
    """Stub sentence generation (OpenAI) and Phase 5 (PDF); count generations."""
    calls = {"generate": 0}

    def fake_generate(payload, **kwargs):
        calls["generate"] += 1
        generated = copy.deepcopy(payload)
        generated["output"] = {"subtitle": "Fake Subtitle"}
        for entry in generated.get("data") or []:
            entry["output"] = {"sentence": "A fake sentence about %s." % entry["word"]}
        return generated

    import phase2

    monkeypatch.setattr(phase2, "generate_sentences", fake_generate)
    monkeypatch.setattr(app_module, "run_phase5_with_json", lambda payload: b"%PDF-fake")
    return calls


@pytest.fixture()
def datastore(app_module):
    """The global responses_datastore, emptied so episode numbering starts at 1."""
    root = Path(os.environ["VOCAB_HUNTERS_DB_PATH"]) / "responses_datastore"
    for path in sorted(root.rglob("*.json")):
        path.unlink()
    return root


CACHE_DIR = ("testds", "fp_C", "1", "space", "test-model")


def test_cache_miss_generates_and_writes_expected_path(client, fake_pipeline, datastore):
    resp = client.post("/generate", json=GENERATE_PAYLOAD)
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert fake_pipeline["generate"] == 1

    cache_file = datastore.joinpath(*CACHE_DIR) / "1.json"
    assert cache_file.is_file(), sorted(str(p) for p in datastore.rglob("*.json"))


def test_cached_payload_round_trips(client, fake_pipeline, datastore):
    client.post("/generate", json=GENERATE_PAYLOAD)
    cache_file = datastore.joinpath(*CACHE_DIR) / "1.json"

    cached = json.loads(cache_file.read_text(encoding="utf-8"))

    # The generated fields Phase 5 consumes.
    assert cached["output"] == {"subtitle": "Fake Subtitle"}
    assert cached["data"][0]["output"]["sentence"] == "A fake sentence about cat."

    # Phase 3 identity fields survive the parse/serialize round trip.
    assert cached["source_dataset"] == "testds"
    assert cached["theme"] == "space"
    assert cached["seed"] == 1
    assert cached["doc_checksum"]
    assert cached["data"][0]["checksum"]
    assert cached["data"][0]["word"] == "cat"

    # Cache files stay human-readable and unescaped.
    assert cache_file.read_text(encoding="utf-8").startswith("{\n  ")

    # presentation_metadata is applied per request, never persisted.
    assert "presentation_metadata" not in cached


def test_replay_reads_cache_without_regenerating(client, fake_pipeline, datastore):
    client.post("/generate", json=GENERATE_PAYLOAD)
    assert fake_pipeline["generate"] == 1

    replay = dict(GENERATE_PAYLOAD)
    replay["section"] = "1"
    replay["episode"] = "1"
    resp = client.post("/fetch_episode", json=replay)

    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert fake_pipeline["generate"] == 1, "cache hit must not call the model"


def test_second_request_generates_the_next_episode(client, fake_pipeline, datastore):
    client.post("/generate", json=GENERATE_PAYLOAD)
    resp = client.post("/generate", json=GENERATE_PAYLOAD)
    assert resp.status_code == 200
    assert fake_pipeline["generate"] == 2

    cache_dir = datastore.joinpath(*CACHE_DIR)
    assert sorted(p.name for p in cache_dir.glob("*.json")) == ["1.json", "2.json"]
    assert json.loads((cache_dir / "2.json").read_text(encoding="utf-8"))["seed"] == 2


def test_fetch_episode_cache_miss_writes_integer_seed(client, fake_pipeline, datastore):
    """Regression: /fetch_episode passes `episode` straight through as the seed.

    JSON sends it as a string, so the cached payload used to get seed="1" while
    every other producer writes an int. Phase 5 then did `seed + 1` on a str
    and raised TypeError, after the bad file had already been written.
    """
    payload = dict(GENERATE_PAYLOAD)
    payload["section"] = "1"
    payload["episode"] = "1"

    resp = client.post("/fetch_episode", json=payload)
    assert resp.status_code == 200
    assert fake_pipeline["generate"] == 1, "no cache entry yet, so this must generate"

    cached = json.loads((datastore.joinpath(*CACHE_DIR) / "1.json").read_text(encoding="utf-8"))
    assert cached["seed"] == 1
    assert isinstance(cached["seed"], int), "Phase 5 does arithmetic on seed"


def test_fetch_episode_rejects_non_numeric_episode(client, fake_pipeline, datastore):
    payload = dict(GENERATE_PAYLOAD)
    payload["section"] = "1"
    payload["episode"] = "not-a-number"

    resp = client.post("/fetch_episode", json=payload)
    assert resp.status_code == 400
    assert "episode" in resp.get_json()["error"]
    assert fake_pipeline["generate"] == 0
    assert not list(datastore.rglob("*.json"))


def test_generation_failure_surfaces_as_json_error(client, app_module, monkeypatch, datastore):
    """A SentenceGenerationError must reach the client as a message, not a bare 500."""
    import phase2
    from Libraries.sentence_generation import SentenceGenerationError

    def boom(payload, **kwargs):
        raise SentenceGenerationError("model exploded")

    monkeypatch.setattr(phase2, "generate_sentences", boom)
    monkeypatch.setattr(app_module, "run_phase5_with_json", lambda payload: b"%PDF-fake")

    resp = client.post("/generate", json=GENERATE_PAYLOAD)
    assert resp.status_code == 400
    assert "model exploded" in resp.get_json()["error"]
    assert not list(datastore.rglob("*.json")), "failed generation must not write a cache file"
