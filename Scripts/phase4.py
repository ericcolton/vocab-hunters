#!/usr/bin/env python3
import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

from openai import OpenAI


def parse_args() -> argparse.Namespace:
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
        required=True,
        help="Path to a text file containing the system prompt/instructions.",
    )
    parser.add_argument(
        "-t",
        "--theme-dir",
        required=False,
        help=(
            "Optional directory containing theme JSON files. "
            'If the input JSON has a "theme" field, this script will look for '
            '"<theme>.json" inside this directory and include its contents in '
            "the user input."
        ),
    )
    return parser.parse_args()


def read_stdin_json() -> Dict[str, Any]:
    raw = sys.stdin.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Failed to parse JSON from stdin: {exc}") from exc


def read_file_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as exc:
        raise SystemExit(f"Failed to read file '{path}': {exc}") from exc


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

    theme_path = os.path.join(theme_dir, f"{theme_name}.json")
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
        parts.append("\n\nTHEME JSON:\n")
        parts.append(theme_content)

    return "".join(parts)


def call_openai(
    request: Dict[str, Any],
    system_prompt: str,
    user_input: str,
) -> Any:
    """
    Call the OpenAI Responses API using:
      - model from request['model']
      - seed from request['seed'] (if present)
      - system_prompt as `instructions`
      - user_input as `input`
    """
    model = request.get("model")
    if not model:
        raise SystemExit("Input JSON must contain a 'model' field.")

    seed = request.get("seed")

    client = OpenAI()  # expects OPENAI_API_KEY in env

    # The Responses API takes `instructions` (system-level) and `input` (user-level) :contentReference[oaicite:3]{index=3}
    kwargs: Dict[str, Any] = {
        "model": model,
        "instructions": system_prompt,
        "input": user_input,
    }
    # if isinstance(seed, int):
    #     kwargs["seed"] = seed

    response = client.responses.create(**kwargs)

    # Get the aggregated text form of the model output (convenience property). :contentReference[oaicite:4]{index=4}
    response_text = getattr(response, "output_text", None)

    if response_text is None:
        # Fallback: serialize the full response object if output_text isn't present.
        # The OpenAI objects are Pydantic models, so we can use model_dump(). :contentReference[oaicite:5]{index=5}
        return response.model_dump()

    # Try to interpret the model's output as JSON. If that fails, wrap raw text.
    try:
        parsed = json.loads(response_text)
        return parsed
    except json.JSONDecodeError:
        return {"raw_text": response_text}


def main() -> None:
    args = parse_args()

    # 1. Read request JSON from stdin.
    request_json = read_stdin_json()

    # 2. Read system prompt file.
    system_prompt = read_file_text(args.prompt_path)

    # 3. Optionally load theme content.
    theme_content = load_theme_content(request_json, args.theme_dir)

    # 4. Build the user input string.
    model_input = build_model_input(request_json, theme_content)

    # 5. Call OpenAI.
    response_payload = call_openai(
        request=request_json,
        system_prompt=system_prompt,
        user_input=model_input,
    )

    # 6. Emit final wrapper JSON to stdout.
    output_obj = {
        "request": request_json,
        "response": response_payload,
    }

    json.dump(output_obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

