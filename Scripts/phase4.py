#!/usr/bin/env python3
import argparse
import json
import os
import logging
from pydantic import BaseModel
import sys
from typing import Any, Dict, Optional

from flask import current_app, has_app_context
from openai import OpenAI

# Temperature setting for OpenAI API calls
OPENAI_TEMPERATURE = 1.0


def get_logger():
    if has_app_context():
        return current_app.logger
    return logging.getLogger(__name__)


def load_default_paths() -> Dict[str, Optional[str]]:
    """
    Load defaults from HOMEWORK_HERO_CONFIG_PATH, if set.
    Expected keys: "prompt_path" and "themes_dir".
    """
    config_path = os.environ.get("HOMEWORK_HERO_CONFIG_PATH")
    if not config_path:
        return {"prompt_path": None, "themes_dir": None}

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"Failed to load HOMEWORK_HERO_CONFIG_PATH='{config_path}': {exc}"
        ) from exc

    if "prompt_path" not in config:
        raise SystemExit(
            f"Config at HOMEWORK_HERO_CONFIG_PATH='{config_path}' missing 'prompt_path'."
        )
    if "themes_dir" not in config:
        raise SystemExit(
            f"Config at HOMEWORK_HERO_CONFIG_PATH='{config_path}' missing 'themes_dir'."
        )

    return {"prompt_path": config.get("prompt_path"), "themes_dir": config.get("themes_dir")}


def parse_args(
    argv=None,
    default_prompt_path: Optional[str] = None,
    default_themes_dir: Optional[str] = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read a phase_3-style request JSON from stdin, call the OpenAI API "
            "with a system prompt and optional theme, and emit a JSON wrapper "
            "containing both the original request and the model's response."
        )
    )
    parser.add_argument(
        "-p",
        "--prompt-path",
        default=default_prompt_path,
        required=default_prompt_path is None,
        help="Path to a text file containing the system prompt/instructions.",
    )
    parser.add_argument(
        "-t",
        "--themes-dir",
        required=False,
        default=default_themes_dir,
        help=(
            "Optional directory containing theme JSON files. "
            'If the input JSON has a "theme" field, this script will look for '
            '"<theme>.json" inside this directory and include its contents in '
            "the user input."
        ),
    )
    return parser.parse_args(argv)


def read_stdin_json() -> Dict[str, Any]:
    raw = sys.stdin.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Failed to parse JSON from stdin: {exc}") from exc


def read_request_json(request_json: str) -> Dict[str, Any]:
    try:
        return json.loads(request_json)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Failed to parse JSON from request_json: {exc}") from exc
    
def build_reading_level_str(request_json: Dict[str, Any]) -> str:
    reading_level = request_json.get("reading_level")
    if reading_level:
        system = reading_level.get("system")
        level = reading_level.get("level")
    if (not system or not level):
        raise SystemExit("Input JSON missing 'reading_level'.")
    if system == "fp":
        return f"Fountas & Pinnell level {level}"
    elif system == "grade":
        if level == 1:
            return "1st-grade reading level"
        elif level == 2:
            return "2nd-grade reading level"
        return f"{level}th-grade reading level"
    else:
        raise SystemExit(f"Unsupported reading_level system: {system}")
    
def read_file_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as exc:
        raise SystemExit(f"Failed to read file '{path}': {exc}") from exc

def flesh_out_system_prompt(raw_system_prompt: str, request_json: Dict[str, Any]) -> str:
    reading_level = build_reading_level_str(request_json)
    system_prompt = raw_system_prompt.replace("{reading_level}", reading_level)
    return system_prompt

def load_theme_content(request: Dict[str, Any], theme_dir: Optional[str]) -> Optional[str]:
    """
    If request['theme'] is present, load <theme>.json from theme_dir and return its raw text.
    If theme is specified but theme_dir is missing, exit with an error.
    """
    theme_name = request.get("theme")
    if not theme_name:
        return None

    if not theme_dir:
        raise SystemExit(
            "Input JSON specifies a 'theme', but no --theme-dir was provided."
        )

    theme_path = os.path.join(theme_dir, f"{theme_name}.txt")
    return read_file_text(theme_path)


def build_model_input(request: Dict[str, Any], theme_content: Optional[str]) -> str:
    """
    Construct the single string that will be passed as `input` to the Responses API.

    This includes the original request JSON and, if present, the raw theme file
    contents. Your prompt file should tell the model how to interpret these.
    """
    parts = []

    # The raw request JSON, so the model can see words/definitions/etc.
    parts.append("REQUEST JSON:\n")
    parts.append(json.dumps(request, ensure_ascii=False, indent=2))

    if theme_content is not None:
        parts.append("\n\nTHEME:\n")
        parts.append(theme_content)

    return "".join(parts)

class VocabSentence(BaseModel):
    checksum: str
    sentence: str

class JsonOutputFormat(BaseModel):
    subtitle: str
    doc_checksum: str
    data: list[VocabSentence]

def call_openai(
    request: Dict[str, Any],
    system_prompt: str,
    user_input: str,
) -> JsonOutputFormat:
    """
    Call the OpenAI Responses API using:
      - model from request['model']
      - system_prompt as `instructions`
      - user_input as `input`
    """
    model = request.get("model")
    if not model:
        raise SystemExit("Input JSON must contain a 'model' field.")

    seed = request.get("seed")

    logger = get_logger()
    client = OpenAI()  # expects OPENAI_API_KEY in env

    # The Responses API takes `instructions` (system-level) and `input` (user-level) :contentReference[oaicite:3]{index=3}
    kwargs: Dict[str, Any] = {
        "model": model,
        "instructions": system_prompt,
        "input": user_input,
        "store": True,
        "temperature": OPENAI_TEMPERATURE,
        "text_format": JsonOutputFormat,
        }

    try:
        logger.debug("Calling OpenAI")
        response = client.responses.parse(**kwargs)
    except Exception as exc:
        logger.debug("OpenAI API call failed: %s", exc, exc_info=True)
        raise SystemExit(f"OpenAI API call failed: {exc}") from exc
    
    output = response.output_parsed
    
    if output:
        return output

    response_text = getattr(response, "output_text", None)
    logger.debug("OpenAI response parsing failed; raw_text=%s", response_text)
    exit(1)

def append_response_json(request_json: Dict[str, Any], response_json: JsonOutputFormat):
    req_doc_checksum = request_json.get("doc_checksum")
    if not req_doc_checksum:
        raise SystemExit("Input JSON missing 'doc_checksum'.")

    if response_json.doc_checksum != req_doc_checksum:
        raise SystemExit(
            f"doc_checksum mismatch: input={req_doc_checksum} response={response_json.doc_checksum}"
        )

    response_by_checksum: Dict[str, VocabSentence] = {}
    for item in response_json.data:
        if item.checksum in response_by_checksum:
            raise SystemExit(f"Duplicate checksum in response: {item.checksum}")
        response_by_checksum[item.checksum] = item

    input_entries = request_json.get("data") or []
    missing_checksums = []

    for entry in input_entries:
        checksum = entry.get("checksum")
        if not checksum:
            raise SystemExit("Input entry missing 'checksum'.")

        response_entry = response_by_checksum.get(checksum)
        if response_entry is None:
            missing_checksums.append(checksum)
            continue

        entry["output"] = {"sentence": response_entry.sentence}

    extra_checksums = set(response_by_checksum.keys()) - {
        entry.get("checksum") for entry in input_entries
    }

    if missing_checksums:
        raise SystemExit(
            f"Missing response for checksum(s): {', '.join(sorted(missing_checksums))}"
        )

    if extra_checksums:
        raise SystemExit(
            f"Response contains unexpected checksum(s): {', '.join(sorted(extra_checksums))}"
        )

    request_json["output"] = {"subtitle": response_json.subtitle}
    return request_json

def main() -> None:
    defaults = load_default_paths()
    args = parse_args(
        default_prompt_path=defaults["prompt_path"],
        default_themes_dir=defaults["themes_dir"],
    )

    # 1. Read request JSON from stdin.
    request_json = read_stdin_json()

    # 2. Read system prompt file.
    raw_system_prompt = read_file_text(args.prompt_path)
    system_prompt = flesh_out_system_prompt(raw_system_prompt, request_json)

    # 3. Optionally load theme content.
    theme_content = load_theme_content(request_json, args.themes_dir)
    
    # 4. Build the user input string.
    model_input = build_model_input(request_json, theme_content)
    
    # 5. Call OpenAI.
    response_payload = call_openai(
        request=request_json,
        system_prompt=system_prompt,
        user_input=model_input,
    )
    
    # 7. Append response JSON
    output_obj = append_response_json(request_json, response_payload)

    # 6. Emit final wrapper JSON to stdout.
    # output_obj = {
    #     "request": request_json,
    #     "response": response_payload,
    # }

    json.dump(output_obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def run_from_json(
    request_json: str,
    prompt_path: Optional[str] = None,
    themes_dir: Optional[str] = None,
) -> str:
    defaults = load_default_paths()
    prompt_path = prompt_path or defaults["prompt_path"]
    if prompt_path is None:
        raise SystemExit("prompt_path is required.")
    if themes_dir is None:
        themes_dir = defaults["themes_dir"]

    request_obj = read_request_json(request_json)
    raw_system_prompt = read_file_text(prompt_path)
    system_prompt = flesh_out_system_prompt(raw_system_prompt, request_obj)
    theme_content = load_theme_content(request_obj, themes_dir)
    model_input = build_model_input(request_obj, theme_content)
    response_payload = call_openai(
        request=request_obj,
        system_prompt=system_prompt,
        user_input=model_input,
    )
    output_obj = append_response_json(request_obj, response_payload)
    return json.dumps(output_obj, ensure_ascii=False, indent=2)


def run_with_json(
    request_json: str,
    prompt_path: Optional[str] = None,
    themes_dir: Optional[str] = None,
) -> str:
    return run_from_json(
        request_json,
        prompt_path=prompt_path,
        themes_dir=themes_dir,
    )


def run_phase4_with_json(
    request_json: str,
    prompt_path: Optional[str] = None,
    themes_dir: Optional[str] = None,
) -> str:
    return run_from_json(
        request_json,
        prompt_path=prompt_path,
        themes_dir=themes_dir,
    )


if __name__ == "__main__":
    main()
