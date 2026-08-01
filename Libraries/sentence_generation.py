"""AI sentence generation for vocabulary worksheets.

Takes a Phase 3-shaped payload (vocabulary words, definitions, and per-entry
checksums), calls the OpenAI Responses API with the system prompt and optional
theme context, and returns the payload with the generated sentences attached:

    payload["output"]["subtitle"]           - worksheet subtitle
    payload["data"][i]["output"]["sentence"] - sentence for that vocabulary word

Formerly Scripts/phase4.py. This module is library-only: it has no CLI entry
point and never writes to the cache — callers own cache persistence.
"""

import copy
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from flask import current_app, has_app_context
from openai import OpenAI
from pydantic import BaseModel

from Libraries.reference_data import get_prompt_path, get_themes_dir

# Temperature setting for OpenAI API calls
OPENAI_TEMPERATURE = 1.0

READING_LEVEL_TOKEN = "{reading_level}"


class SentenceGenerationError(Exception):
    pass


def get_logger() -> logging.Logger:
    if has_app_context():
        return current_app.logger
    return logging.getLogger(__name__)


class VocabSentence(BaseModel):
    checksum: str
    sentence: str


class JsonOutputFormat(BaseModel):
    subtitle: str
    doc_checksum: str
    data: list[VocabSentence]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SentenceGenerationError(f"Failed to read file '{path}': {exc}") from exc


def _build_reading_level_str(payload: Dict[str, Any]) -> str:
    """Render the payload's reading_level as prose for the system prompt."""
    reading_level = payload.get("reading_level") or {}
    system = reading_level.get("system")
    level = reading_level.get("level")
    if not system or not level:
        raise SentenceGenerationError("Input JSON missing 'reading_level'.")
    if system == "fp":
        return f"Fountas & Pinnell level {level}"
    if system == "grade":
        if level == 1:
            return "1st-grade reading level"
        if level == 2:
            return "2nd-grade reading level"
        return f"{level}th-grade reading level"
    raise SentenceGenerationError(f"Unsupported reading_level system: {system}")


def _build_system_prompt(raw_system_prompt: str, payload: Dict[str, Any]) -> str:
    """Interpolate the reading level into the raw prompt file text."""
    return raw_system_prompt.replace(READING_LEVEL_TOKEN, _build_reading_level_str(payload))


def _load_theme_content(payload: Dict[str, Any], themes_dir: Optional[Path]) -> Optional[str]:
    """Load <theme>.txt from themes_dir, or None when the payload has no theme."""
    theme_name = payload.get("theme")
    if not theme_name:
        return None

    if not themes_dir:
        raise SentenceGenerationError(
            "Input JSON specifies a 'theme', but no themes_dir was available."
        )

    return _read_text(Path(themes_dir) / f"{theme_name}.txt")


def _build_model_input(payload: Dict[str, Any], theme_content: Optional[str]) -> str:
    """Construct the single string passed as `input` to the Responses API.

    Includes the request JSON so the model can see words/definitions, plus the
    raw theme text when one applies. The system prompt tells the model how to
    interpret both sections.
    """
    parts = ["REQUEST JSON:\n", json.dumps(payload, ensure_ascii=False, indent=2)]

    if theme_content is not None:
        parts.append("\n\nTHEME:\n")
        parts.append(theme_content)

    return "".join(parts)


def _call_openai(
    payload: Dict[str, Any],
    system_prompt: str,
    user_input: str,
) -> JsonOutputFormat:
    """Call the OpenAI Responses API and return the parsed structured output."""
    # not-unit-testable: performs a live OpenAI API call; inject call_model to stub it
    model = payload.get("model")
    if not model:
        raise SentenceGenerationError("Input JSON must contain a 'model' field.")

    seed = payload.get("seed")

    logger = get_logger()
    logger.debug("Calling OpenAI with model=%s seed=%s", model, seed)
    client = OpenAI()  # expects OPENAI_API_KEY in env

    # The Responses API takes `instructions` (system-level) and `input` (user-level)
    kwargs: Dict[str, Any] = {
        "model": model,
        "instructions": system_prompt,
        "input": user_input,
        "store": True,
        "temperature": OPENAI_TEMPERATURE,
        "text_format": JsonOutputFormat,
    }

    try:
        response = client.responses.parse(**kwargs)
    except Exception as exc:
        logger.debug("OpenAI API call failed: %s", exc, exc_info=True)
        raise SentenceGenerationError(f"OpenAI API call failed: {exc}") from exc

    output = response.output_parsed
    if output:
        return output

    response_text = getattr(response, "output_text", None)
    logger.debug("OpenAI response parsing failed; raw_text=%s", response_text)
    raise SentenceGenerationError(
        f"OpenAI response could not be parsed into the expected format: {response_text}"
    )


def _merge_sentences(
    payload: Dict[str, Any], response: JsonOutputFormat
) -> Dict[str, Any]:
    """Attach the model's sentences to a copy of the payload.

    Reconciles the response against the payload by checksum: the doc-level
    checksum must match, and every entry checksum must be present exactly once
    with no extras.
    """
    merged = copy.deepcopy(payload)

    doc_checksum = merged.get("doc_checksum")
    if not doc_checksum:
        raise SentenceGenerationError("Input JSON missing 'doc_checksum'.")

    if response.doc_checksum != doc_checksum:
        raise SentenceGenerationError(
            f"doc_checksum mismatch: input={doc_checksum} response={response.doc_checksum}"
        )

    response_by_checksum: Dict[str, VocabSentence] = {}
    for item in response.data:
        if item.checksum in response_by_checksum:
            raise SentenceGenerationError(f"Duplicate checksum in response: {item.checksum}")
        response_by_checksum[item.checksum] = item

    entries = merged.get("data") or []
    missing_checksums = []

    for entry in entries:
        checksum = entry.get("checksum")
        if not checksum:
            raise SentenceGenerationError("Input entry missing 'checksum'.")

        response_entry = response_by_checksum.get(checksum)
        if response_entry is None:
            missing_checksums.append(checksum)
            continue

        entry["output"] = {"sentence": response_entry.sentence}

    extra_checksums = set(response_by_checksum) - {entry.get("checksum") for entry in entries}

    if missing_checksums:
        raise SentenceGenerationError(
            f"Missing response for checksum(s): {', '.join(sorted(missing_checksums))}"
        )

    if extra_checksums:
        raise SentenceGenerationError(
            f"Response contains unexpected checksum(s): {', '.join(sorted(extra_checksums))}"
        )

    merged["output"] = {"subtitle": response.subtitle}
    return merged


def _resolve_default_path(getter: Callable[[], Path]) -> Path:
    try:
        return getter()
    except (RuntimeError, ValueError) as exc:
        raise SentenceGenerationError(str(exc)) from exc


def generate_sentences(
    payload: Dict[str, Any],
    *,
    theme_content: Optional[str] = None,
    prompt_path: Optional[Path] = None,
    themes_dir: Optional[Path] = None,
    call_model: Optional[Callable[..., JsonOutputFormat]] = None,
) -> Dict[str, Any]:
    """Generate worksheet sentences for a Phase 3-shaped payload.

    Returns a new payload with `output.subtitle` and per-entry
    `output.sentence` attached; the input dict is left unmodified. An explicit
    theme_content bypasses themes_dir lookup (used by the custom-theme and
    user-theme flows). Raises SentenceGenerationError on any failure.
    """
    if prompt_path is None:
        prompt_path = _resolve_default_path(get_prompt_path)
    prompt_path = Path(prompt_path)
    if not prompt_path.is_file():
        raise SentenceGenerationError(f"Prompt file not found: {prompt_path}")

    system_prompt = _build_system_prompt(_read_text(prompt_path), payload)

    # Resolve themes_dir lazily: a payload with no theme never needs it, and
    # neither does one whose theme text was supplied directly.
    if theme_content is None and payload.get("theme"):
        if themes_dir is None:
            themes_dir = _resolve_default_path(get_themes_dir)
        theme_content = _load_theme_content(payload, themes_dir)

    model_input = _build_model_input(payload, theme_content)

    call = call_model or _call_openai
    response = call(payload, system_prompt, model_input)

    return _merge_sentences(payload, response)
