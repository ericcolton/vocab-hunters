#!/usr/bin/env python3
"""Generation pipeline for user-owned content.

Mirrors Phase 2's cache orchestration (Scripts/phase2.py:process_request) but
roots the cache in the user's private datastore and calls Phase 3 → Phase 4
directly. User content cannot be represented in the bit-packed global
worksheet ID, so payloads keep worksheet_id=None and results are addressed by
the owner-only /my/* routes.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from flask import current_app, has_app_context

from Libraries.datasets import DatasetError, load_dataset
from Libraries.reference_data import lookup_source_dataset, lookup_theme
from Libraries.user_data import (
    UserDataError,
    get_user_cache_dir,
    get_user_source_datasets_dir,
    get_user_theme,
    is_user_key,
    list_user_episodes,
    strip_user_key,
)


class UserPipelineError(Exception):
    def __init__(self, message: str, not_found: bool = False):
        super().__init__(message)
        self.not_found = not_found


def get_logger() -> logging.Logger:
    if has_app_context():
        return current_app.logger
    return logging.getLogger(__name__)


def interpolate_presentation_metadata(
    metadata: Dict[str, Any], variables: Dict[str, Any]
) -> Dict[str, Any]:
    """Replace {placeholder} variables in header/footer/answer_key_footer."""
    interpolated = dict(metadata)
    for key in ("header", "footer", "answer_key_footer"):
        if key in interpolated:
            template = interpolated[key]
            if template is None:
                continue
            text = str(template)
            for var_key, value in variables.items():
                placeholder = "{" + var_key + "}"
                text = text.replace(placeholder, str(value))
            interpolated[key] = text
    return interpolated


def _resolve_dataset(user_id: int, source_dataset: str) -> Tuple[str, Optional[Path], Dict[str, str]]:
    """Return (file_key, datasets_dir, display) for a request dataset key."""
    if is_user_key(source_dataset):
        try:
            stem = strip_user_key(source_dataset)
        except UserDataError as exc:
            raise UserPipelineError(str(exc)) from exc
        datasets_dir = get_user_source_datasets_dir(user_id)
        try:
            dataset = load_dataset(stem, datasets_dir=datasets_dir)
        except DatasetError as exc:
            raise UserPipelineError(str(exc), not_found=True) from exc
        title = dataset.get("title") or dataset.get("dataset_title") or stem.replace("_", " ")
        return stem, datasets_dir, {"source": title, "source_abbr": dataset.get("title_abbr", "")}
    entry = lookup_source_dataset(source_dataset) or {}
    return source_dataset, None, {
        "source": entry.get("title", ""),
        "source_abbr": entry.get("title_abbr", ""),
    }


def _resolve_theme(user_id: int, theme: str, theme_content: Optional[str]) -> Tuple[Optional[str], Dict[str, str]]:
    """Return (theme_content, display) for a request theme key."""
    if is_user_key(theme):
        try:
            stem = strip_user_key(theme)
        except UserDataError as exc:
            raise UserPipelineError(str(exc)) from exc
        record = get_user_theme(user_id, stem)
        if record is None:
            raise UserPipelineError(f"Unknown theme: {theme}", not_found=True)
        if theme_content is None:
            from Libraries.user_data import build_theme_content

            theme_content = build_theme_content(record.get("text") or stem.replace("_", " "))
        title = record.get("title") or stem.replace("_", " ")
        return theme_content, {"theme": title, "theme_abbr": "Custom"}
    entry = lookup_theme(theme) or {}
    # Global theme: leave theme_content to Phase 4's themes_dir resolution.
    return theme_content, {
        "theme": entry.get("title", theme),
        "theme_abbr": entry.get("title_abbr", ""),
    }


def generate_user_worksheet(
    user_id: int,
    source_dataset: str,
    theme: str,
    reading_level: str,
    model: str,
    section: Any,
    seed: Optional[int] = None,
    theme_content: Optional[str] = None,
    presentation_metadata: Optional[Dict[str, Any]] = None,
    allow_generate: bool = True,
) -> Tuple[str, int]:
    """Generate (or replay from the user's cache) a worksheet payload.

    source_dataset/theme are request keys: either global key_names or
    u--prefixed user keys. Returns (phase4-shaped JSON for Phase 5, seed used).
    """
    logger = get_logger()

    # Imported lazily: Scripts/ is appended to sys.path by app.py at startup.
    from phase3 import run_with_json as run_phase3_with_json
    from phase4 import run_phase4_with_json

    reading_level_segment = f"fp_{reading_level}"
    cache_dir = get_user_cache_dir(
        user_id, source_dataset, reading_level_segment, section, theme, model
    )

    if seed is None:
        episodes = list_user_episodes(
            user_id, source_dataset, reading_level_segment, section, theme, model
        )
        seed = episodes[-1]["episode"] + 1 if episodes else 1
    seed = int(seed)

    cache_path = cache_dir / f"{seed}.json"

    dataset_file_key, datasets_dir, dataset_display = _resolve_dataset(user_id, source_dataset)
    theme_content, theme_display = _resolve_theme(user_id, theme, theme_content)

    if not cache_path.is_file():
        if not allow_generate:
            raise UserPipelineError("Episode not found.", not_found=True)

        phase3_payload = {
            "source_dataset": dataset_file_key,
            "theme": theme,
            "reading_level": {"system": "fp", "level": reading_level},
            "model": model,
            "section": int(section),
            "seed": seed,
            "worksheet_id": None,
        }
        try:
            logger.debug("Entering run_phase3_with_json() for user_id=%d", user_id)
            phase3_output = run_phase3_with_json(
                json.dumps(phase3_payload, ensure_ascii=False), datasets_dir=datasets_dir
            )
            logger.debug("Exiting run_phase3_with_json()")
        except (SystemExit, DatasetError) as exc:
            raise UserPipelineError(str(exc)) from exc

        try:
            logger.debug("Entering run_phase4_with_json() for user_id=%d", user_id)
            phase4_output = run_phase4_with_json(phase3_output, theme_content=theme_content)
            logger.debug("Exiting run_phase4_with_json()")
        except SystemExit as exc:
            raise UserPipelineError(str(exc)) from exc

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug("Writing user worksheet cache to %s", cache_path)
        with cache_path.open("w", encoding="utf-8") as f:
            f.write(phase4_output)

    try:
        with cache_path.open("r", encoding="utf-8") as f:
            output_payload = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise UserPipelineError(f"Failed to read/parse cache file: {exc}") from exc

    output_payload["worksheet_id"] = None

    if presentation_metadata:
        variables = {
            "section": section,
            "reading_system": "fp",
            "reading_level": reading_level,
            "model": model,
            "episode": seed,
            "worksheet_id": "",
        }
        variables.update(dataset_display)
        variables.update(theme_display)
        output_payload["presentation_metadata"] = interpolate_presentation_metadata(
            presentation_metadata, variables
        )

    return json.dumps(output_payload, ensure_ascii=False, indent=2), seed
