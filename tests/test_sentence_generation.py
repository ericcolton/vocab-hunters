#!/usr/bin/env python3
"""Unit tests for Libraries/sentence_generation.py.

The OpenAI call is never made: generate_sentences() takes a call_model
callable, and the private helpers are exercised directly.
"""

import copy

import pytest

from Libraries.sentence_generation import (
    JsonOutputFormat,
    SentenceGenerationError,
    VocabSentence,
    _build_model_input,
    _build_reading_level_str,
    _build_system_prompt,
    _load_theme_content,
    _merge_sentences,
    generate_sentences,
)


def make_payload(**overrides):
    payload = {
        "source_dataset": "testds",
        "reading_level": {"system": "fp", "level": "C"},
        "section": "1",
        "theme": "space",
        "model": "test-model",
        "seed": 1,
        "doc_checksum": "doc123",
        "data": [
            {"word": "cat", "definition": "a small animal", "checksum": "abc"},
            {"word": "dog", "definition": "a loyal animal", "checksum": "def"},
        ],
    }
    payload.update(overrides)
    return payload


def make_response(**overrides):
    fields = {
        "subtitle": "Space Adventure",
        "doc_checksum": "doc123",
        "data": [
            VocabSentence(checksum="abc", sentence="The cat orbited Mars."),
            VocabSentence(checksum="def", sentence="The dog piloted the shuttle."),
        ],
    }
    fields.update(overrides)
    return JsonOutputFormat(**fields)


# --- reading level -----------------------------------------------------------


def test_reading_level_fountas_pinnell():
    assert (
        _build_reading_level_str(make_payload(reading_level={"system": "fp", "level": "P"}))
        == "Fountas & Pinnell level P"
    )


@pytest.mark.parametrize(
    "level,expected",
    [
        (1, "1st-grade reading level"),
        (2, "2nd-grade reading level"),
        (5, "5th-grade reading level"),
    ],
)
def test_reading_level_grade(level, expected):
    payload = make_payload(reading_level={"system": "grade", "level": level})
    assert _build_reading_level_str(payload) == expected


@pytest.mark.parametrize("reading_level", [None, {}, {"system": "fp"}, {"level": "C"}])
def test_reading_level_missing_raises_typed_error(reading_level):
    """Regression: this used to raise NameError instead of a usable error."""
    payload = make_payload(reading_level=reading_level)
    with pytest.raises(SentenceGenerationError, match="reading_level"):
        _build_reading_level_str(payload)


def test_reading_level_unsupported_system_raises():
    payload = make_payload(reading_level={"system": "lexile", "level": 700})
    with pytest.raises(SentenceGenerationError, match="Unsupported reading_level system"):
        _build_reading_level_str(payload)


def test_system_prompt_interpolates_reading_level():
    prompt = _build_system_prompt("Write at {reading_level} please.", make_payload())
    assert prompt == "Write at Fountas & Pinnell level C please."


# --- theme loading -----------------------------------------------------------


def test_load_theme_content_returns_none_without_theme(tmp_path):
    assert _load_theme_content(make_payload(theme=None), tmp_path) is None


def test_load_theme_content_reads_txt_file(tmp_path):
    (tmp_path / "space.txt").write_text("Rockets and stars.", encoding="utf-8")
    assert _load_theme_content(make_payload(), tmp_path) == "Rockets and stars."


def test_load_theme_content_without_dir_raises():
    with pytest.raises(SentenceGenerationError, match="themes_dir"):
        _load_theme_content(make_payload(), None)


def test_load_theme_content_missing_file_raises(tmp_path):
    with pytest.raises(SentenceGenerationError, match="Failed to read file"):
        _load_theme_content(make_payload(), tmp_path)


# --- model input -------------------------------------------------------------


def test_model_input_includes_request_and_theme():
    text = _build_model_input(make_payload(), "Rockets and stars.")
    assert text.startswith("REQUEST JSON:\n")
    assert '"doc_checksum": "doc123"' in text
    assert text.endswith("\n\nTHEME:\nRockets and stars.")


def test_model_input_omits_theme_section_when_absent():
    assert "THEME:" not in _build_model_input(make_payload(), None)


# --- merging -----------------------------------------------------------------


def test_merge_attaches_subtitle_and_sentences():
    merged = _merge_sentences(make_payload(), make_response())
    assert merged["output"] == {"subtitle": "Space Adventure"}
    assert merged["data"][0]["output"] == {"sentence": "The cat orbited Mars."}
    assert merged["data"][1]["output"] == {"sentence": "The dog piloted the shuttle."}
    # Non-generated fields survive untouched.
    assert merged["data"][0]["word"] == "cat"
    assert merged["doc_checksum"] == "doc123"


def test_merge_does_not_mutate_input():
    payload = make_payload()
    original = copy.deepcopy(payload)
    _merge_sentences(payload, make_response())
    assert payload == original


def test_merge_missing_doc_checksum_raises():
    payload = make_payload()
    del payload["doc_checksum"]
    with pytest.raises(SentenceGenerationError, match="missing 'doc_checksum'"):
        _merge_sentences(payload, make_response())


def test_merge_doc_checksum_mismatch_raises():
    with pytest.raises(SentenceGenerationError, match="doc_checksum mismatch"):
        _merge_sentences(make_payload(), make_response(doc_checksum="other"))


def test_merge_duplicate_checksum_raises():
    response = make_response(
        data=[
            VocabSentence(checksum="abc", sentence="One."),
            VocabSentence(checksum="abc", sentence="Two."),
        ]
    )
    with pytest.raises(SentenceGenerationError, match="Duplicate checksum"):
        _merge_sentences(make_payload(), response)


def test_merge_missing_response_entry_raises():
    response = make_response(data=[VocabSentence(checksum="abc", sentence="One.")])
    with pytest.raises(SentenceGenerationError, match="Missing response for checksum"):
        _merge_sentences(make_payload(), response)


def test_merge_extra_response_entry_raises():
    response = make_response(
        data=list(make_response().data) + [VocabSentence(checksum="zzz", sentence="Extra.")]
    )
    with pytest.raises(SentenceGenerationError, match="unexpected checksum"):
        _merge_sentences(make_payload(), response)


def test_merge_entry_without_checksum_raises():
    payload = make_payload(data=[{"word": "cat", "definition": "a small animal"}])
    with pytest.raises(SentenceGenerationError, match="Input entry missing 'checksum'"):
        _merge_sentences(payload, make_response())


# --- generate_sentences ------------------------------------------------------


@pytest.fixture()
def db_dirs(tmp_path):
    """A prompt file and a themes dir with the 'space' theme."""
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("Write sentences at {reading_level}.", encoding="utf-8")
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    (themes_dir / "space.txt").write_text("Rockets and stars.", encoding="utf-8")
    return prompt_path, themes_dir


def test_generate_sentences_end_to_end(db_dirs):
    prompt_path, themes_dir = db_dirs
    seen = {}

    def fake_call(payload, system_prompt, user_input):
        seen["system_prompt"] = system_prompt
        seen["user_input"] = user_input
        return make_response()

    payload = make_payload()
    result = generate_sentences(
        payload,
        prompt_path=prompt_path,
        themes_dir=themes_dir,
        call_model=fake_call,
    )

    assert seen["system_prompt"] == "Write sentences at Fountas & Pinnell level C."
    assert "Rockets and stars." in seen["user_input"]
    assert result["output"] == {"subtitle": "Space Adventure"}
    assert result["data"][0]["output"]["sentence"] == "The cat orbited Mars."
    assert "output" not in payload, "input payload must not be mutated"


def test_generate_sentences_explicit_theme_content_bypasses_themes_dir(db_dirs, tmp_path):
    prompt_path, _ = db_dirs
    seen = {}

    def fake_call(payload, system_prompt, user_input):
        seen["user_input"] = user_input
        return make_response()

    generate_sentences(
        make_payload(),
        theme_content="A castle in the clouds.",
        prompt_path=prompt_path,
        themes_dir=tmp_path / "does-not-exist",
        call_model=fake_call,
    )

    assert "A castle in the clouds." in seen["user_input"]
    assert "Rockets and stars." not in seen["user_input"]


def test_generate_sentences_without_theme_needs_no_themes_dir(db_dirs, monkeypatch):
    """A themeless payload must not force themes_dir (or the DB env var) to resolve."""
    prompt_path, _ = db_dirs
    monkeypatch.delenv("VOCAB_HUNTERS_DB_PATH", raising=False)
    seen = {}

    def fake_call(payload, system_prompt, user_input):
        seen["user_input"] = user_input
        return make_response()

    result = generate_sentences(
        make_payload(theme=None),
        prompt_path=prompt_path,
        call_model=fake_call,
    )

    assert "THEME:" not in seen["user_input"]
    assert result["output"] == {"subtitle": "Space Adventure"}


def test_generate_sentences_missing_prompt_file_raises(tmp_path):
    with pytest.raises(SentenceGenerationError, match="Prompt file not found"):
        generate_sentences(
            make_payload(),
            prompt_path=tmp_path / "nope.txt",
            themes_dir=tmp_path,
            call_model=lambda *args: make_response(),
        )
