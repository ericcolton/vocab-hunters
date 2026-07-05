#!/usr/bin/env python3
"""Per-user content storage on disk.

Logged-in users get a private subtree mirroring the global layout:

    {VOCAB_HUNTERS_DB_PATH}/users/{user_id}/
        user_themes/{stem}.json
        source_datasets/{stem}.json
        responses_datastore/{dataset}/{reading_level}/{section}/{theme}/{model}/{seed}.json

User-owned items are exposed in the UI/API with a "u--" key prefix so they can
never collide with (or spoof) global key_names. user_id always comes from the
session, never from request input.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import current_app, has_app_context

from Libraries.reference_data import get_database_path, validate_key_component

USER_KEY_PREFIX = "u--"
STEM_MAX_LENGTH = 100
THEME_TITLE_MAX_LENGTH = 60

DATASET_MAX_SECTIONS = 100
DATASET_MAX_ENTRIES_PER_SECTION = 200
DATASET_MAX_FIELD_LENGTH = 500
DATASET_TITLE_MAX_LENGTH = 120


class UserDataError(Exception):
    pass


def get_logger() -> logging.Logger:
    if has_app_context():
        return current_app.logger
    return logging.getLogger(__name__)


def get_user_root(user_id: int) -> Path:
    return get_database_path() / "users" / str(int(user_id))


def get_user_themes_dir(user_id: int) -> Path:
    return get_user_root(user_id) / "user_themes"


def get_user_source_datasets_dir(user_id: int) -> Path:
    return get_user_root(user_id) / "source_datasets"


def get_user_responses_dir(user_id: int) -> Path:
    return get_user_root(user_id) / "responses_datastore"


def sanitize_stem(text: str) -> str:
    """Collapse free text to a filesystem- and cache-key-safe stem."""
    stem = re.sub(r"\s+", "_", text.strip())
    stem = re.sub(r"[^A-Za-z0-9_\-]", "", stem)
    stem = stem.lstrip(".-_")[:STEM_MAX_LENGTH]
    return stem or "custom"


def is_user_key(key: str) -> bool:
    return isinstance(key, str) and key.startswith(USER_KEY_PREFIX)


def make_user_key(stem: str) -> str:
    return USER_KEY_PREFIX + stem


def strip_user_key(key: str) -> str:
    """Return the stem behind a u-- key, validated as a safe path component."""
    if not is_user_key(key):
        raise UserDataError(f"Not a user-owned key: {key}")
    stem = key[len(USER_KEY_PREFIX):]
    try:
        return validate_key_component(stem, "user key")
    except ValueError as exc:
        raise UserDataError(str(exc)) from exc


def _theme_title(text: str) -> str:
    title = " ".join(text.split())
    if len(title) > THEME_TITLE_MAX_LENGTH:
        title = title[: THEME_TITLE_MAX_LENGTH - 1] + "…"
    return title


def save_user_theme(user_id: int, text: str) -> str:
    """Persist a custom theme; returns its stem. Saving the same text twice is
    idempotent (the original record is kept)."""
    cleaned = text.strip()
    if not cleaned:
        raise UserDataError("Theme text is empty.")
    stem = sanitize_stem(cleaned)
    themes_dir = get_user_themes_dir(user_id)
    themes_dir.mkdir(parents=True, exist_ok=True)
    theme_path = themes_dir / f"{stem}.json"
    if not theme_path.is_file():
        record = {
            "title": _theme_title(cleaned),
            "text": cleaned,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with theme_path.open("w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        get_logger().debug("Saved user theme user_id=%d stem=%s", user_id, stem)
    return stem


def get_user_theme(user_id: int, stem: str) -> Optional[Dict[str, Any]]:
    theme_path = get_user_themes_dir(user_id) / f"{stem}.json"
    if not theme_path.is_file():
        return None
    try:
        with theme_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise UserDataError(f"Failed to read user theme '{stem}': {exc}") from exc


def build_theme_content(theme_text: str) -> str:
    """The theme context string sent to the model, matching the anonymous
    custom-theme flow in app.py."""
    return "The sentences should take place in a world where " + " ".join(theme_text.split())


def list_user_themes(user_id: int) -> List[Dict[str, Any]]:
    themes_dir = get_user_themes_dir(user_id)
    if not themes_dir.is_dir():
        return []
    themes = []
    for path in sorted(themes_dir.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, json.JSONDecodeError):
            get_logger().debug("Skipping unreadable user theme file: %s", path)
            continue
        key = make_user_key(path.stem)
        themes.append(
            {
                "id": key,
                "key_name": key,
                "stem": path.stem,
                "title": record.get("title") or path.stem.replace("_", " "),
                "text": record.get("text", ""),
                "created_at": record.get("created_at", ""),
            }
        )
    return themes


def get_user_cache_dir(
    user_id: int,
    source_dataset: str,
    reading_level_segment: str,
    section: Any,
    theme: str,
    model: str,
) -> Path:
    return (
        get_user_responses_dir(user_id)
        / str(source_dataset)
        / reading_level_segment
        / str(section)
        / str(theme)
        / str(model)
    )


def _validated_entry_field(entry: Dict[str, Any], field: str, index: int) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise UserDataError(f"Entry {index}: '{field}' must be a non-empty string.")
    if len(value) > DATASET_MAX_FIELD_LENGTH:
        raise UserDataError(
            f"Entry {index}: '{field}' exceeds {DATASET_MAX_FIELD_LENGTH} characters."
        )
    return value.strip()


def validate_dataset_schema(data: Any) -> Dict[str, Any]:
    """Validate an uploaded dataset against the source-dataset shape used by
    Phase 3 (sections[].entries[] with word/part_of_speech/definition).
    Returns a normalized copy containing only known fields. Raises
    UserDataError with a user-facing message on any problem."""
    if not isinstance(data, dict):
        raise UserDataError("Dataset must be a JSON object.")

    title = data.get("title") or data.get("dataset_title") or ""
    if not isinstance(title, str):
        raise UserDataError("'title' must be a string.")
    title = " ".join(title.split())[:DATASET_TITLE_MAX_LENGTH]

    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        raise UserDataError("Dataset must contain a non-empty 'sections' list.")
    if len(sections) > DATASET_MAX_SECTIONS:
        raise UserDataError(f"Dataset exceeds {DATASET_MAX_SECTIONS} sections.")

    normalized_sections = []
    seen_section_numbers = set()
    for section in sections:
        if not isinstance(section, dict):
            raise UserDataError("Each section must be a JSON object.")
        section_number = section.get("section")
        if not isinstance(section_number, int) or isinstance(section_number, bool) or section_number < 1:
            raise UserDataError("Each section needs a positive integer 'section' number.")
        if section_number in seen_section_numbers:
            raise UserDataError(f"Duplicate section number: {section_number}.")
        seen_section_numbers.add(section_number)

        entries = section.get("entries")
        if not isinstance(entries, list) or not entries:
            raise UserDataError(f"Section {section_number} needs a non-empty 'entries' list.")
        if len(entries) > DATASET_MAX_ENTRIES_PER_SECTION:
            raise UserDataError(
                f"Section {section_number} exceeds {DATASET_MAX_ENTRIES_PER_SECTION} entries."
            )

        normalized_entries = []
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                raise UserDataError(f"Section {section_number}, entry {index}: must be an object.")
            normalized_entry = {
                "word": _validated_entry_field(entry, "word", index),
                "part_of_speech": _validated_entry_field(entry, "part_of_speech", index),
                "definition": _validated_entry_field(entry, "definition", index),
            }
            def_num = entry.get("def_num")
            if def_num is not None:
                if not isinstance(def_num, int) or isinstance(def_num, bool):
                    raise UserDataError(
                        f"Section {section_number}, entry {index}: 'def_num' must be an integer."
                    )
                normalized_entry["def_num"] = def_num
            normalized_entries.append(normalized_entry)

        normalized_sections.append({"section": section_number, "entries": normalized_entries})

    return {
        "title": title,
        "dataset_title": title,
        "dataset_type": "user_upload",
        "sections": normalized_sections,
    }


def save_user_dataset(
    user_id: int, title: str, data: Dict[str, Any], overwrite: bool = False
) -> str:
    """Validate and persist an uploaded dataset; returns its stem.
    Raises UserDataError (with .already_exists set) on a duplicate stem."""
    normalized = validate_dataset_schema(data)
    if title:
        normalized["title"] = normalized["dataset_title"] = " ".join(title.split())[
            :DATASET_TITLE_MAX_LENGTH
        ]
    if not normalized["title"]:
        raise UserDataError("Dataset needs a title.")

    stem = sanitize_stem(normalized["title"])
    datasets_dir = get_user_source_datasets_dir(user_id)
    datasets_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = datasets_dir / f"{stem}.json"
    if dataset_path.is_file() and not overwrite:
        error = UserDataError(f"A vocabulary set named '{normalized['title']}' already exists.")
        error.already_exists = True
        raise error
    with dataset_path.open("w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
    get_logger().debug("Saved user dataset user_id=%d stem=%s", user_id, stem)
    return stem


def list_user_datasets(user_id: int) -> List[Dict[str, Any]]:
    datasets_dir = get_user_source_datasets_dir(user_id)
    if not datasets_dir.is_dir():
        return []
    datasets = []
    for path in sorted(datasets_dir.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, json.JSONDecodeError):
            get_logger().debug("Skipping unreadable user dataset file: %s", path)
            continue
        key = make_user_key(path.stem)
        datasets.append(
            {
                "id": key,
                "key_name": key,
                "stem": path.stem,
                "title": record.get("title") or path.stem.replace("_", " "),
                "title_abbr": "",
                "section_count": len(record.get("sections") or []),
            }
        )
    return datasets


def list_user_worksheet_groups(user_id: int) -> List[Dict[str, Any]]:
    """Walk the user's responses datastore and group cached worksheets by
    parameter combination. Path layout:
    {dataset}/{reading_level_segment}/{section}/{theme}/{model}/{seed}.json"""
    root = get_user_responses_dir(user_id)
    if not root.is_dir():
        return []
    groups: Dict[tuple, Dict[str, Any]] = {}
    for seed_file in root.glob("*/*/*/*/*/*.json"):
        if not seed_file.stem.isdigit():
            continue
        dataset, level_segment, section, theme, model = seed_file.relative_to(root).parts[:5]
        key = (dataset, level_segment, section, theme, model)
        group = groups.get(key)
        if group is None:
            group = {
                "source_dataset": dataset,
                "reading_level": level_segment.split("_", 1)[-1],
                "section": section,
                "theme": theme,
                "model": model,
                "episodes": [],
            }
            groups[key] = group
        group["episodes"].append(int(seed_file.stem))
    for group in groups.values():
        group["episodes"].sort()
    return sorted(
        groups.values(),
        key=lambda g: (g["theme"], g["source_dataset"], g["section"], g["reading_level"]),
    )


def list_user_episodes(
    user_id: int,
    source_dataset: str,
    reading_level_segment: str,
    section: Any,
    theme: str,
    model: str,
) -> List[Dict[str, Any]]:
    """Same shape as app.list_cached_episodes: [{episode, subtitle}, ...]."""
    cache_dir = get_user_cache_dir(
        user_id, source_dataset, reading_level_segment, section, theme, model
    )
    if not cache_dir.is_dir():
        return []
    episodes = []
    for path in cache_dir.iterdir():
        if path.is_file() and path.suffix == ".json" and path.stem.isdigit():
            subtitle = ""
            try:
                with path.open("r", encoding="utf-8") as f:
                    payload = json.load(f)
                subtitle = (
                    (payload.get("output") or {}).get("subtitle")
                    or (payload.get("presentation_metadata") or {}).get("subtitle")
                    or ""
                )
            except (OSError, json.JSONDecodeError):
                subtitle = ""
            episodes.append({"episode": int(path.stem), "subtitle": subtitle})
    return sorted(episodes, key=lambda item: item["episode"])
