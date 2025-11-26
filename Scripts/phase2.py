#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path


def build_reading_level_segment(reading_level):
    """
    Convert the reading_level object to a path segment.

    Expected format:
        {"system": "fp", "level": "P"} -> "fp_P"

    Falls back to str(reading_level) if it's not a dict or fields are missing.
    """
    if isinstance(reading_level, dict):
        system = reading_level.get("system")
        level = reading_level.get("level")
        if system is not None and level is not None:
            return f"{system}_{level}"
    # Fallback: just stringify whatever we got
    return str(reading_level)


def load_default_datastore():
    """
    If HOMEWORK_HERO_CONFIG_PATH is set, load JSON and return
    config["source_datasets"] as the default datastore path.
    """
    config_path = os.environ.get("HOMEWORK_HERO_CONFIG_PATH")
    if not config_path:
        return None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"Failed to load HOMEWORK_HERO_CONFIG_PATH='{config_path}': {e}") from e

    default = config.get("source_datasets")
    if default is None:
        raise SystemExit(
            f"Config at HOMEWORK_HERO_CONFIG_PATH='{config_path}' missing 'source_datasets'."
        )
    return default


def parse_args(argv=None, default_datastore=None):
    parser = argparse.ArgumentParser(
        description="Look up cached payloads for a vocab request."
    )
    parser.add_argument(
        "-d",
        "--datastore",
        "--source-datasets",
        default=default_datastore,
        required=default_datastore is None,
        help=(
            "Root directory of the datastore. "
            "Defaults to config['source_datasets'] when HOMEWORK_HERO_CONFIG_PATH is set."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None):
    default_datastore = load_default_datastore()
    args = parse_args(argv, default_datastore=default_datastore)

    # Read JSON request from stdin
    try:
        request = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"Error: Failed to parse JSON from stdin: {e}", file=sys.stderr)
        sys.exit(1)

    # Extract required fields from the request
    try:
        source_dataset = request["source_dataset"]
        theme = request["theme"]
        reading_level = request["reading_level"]
        model = request["model"]
        section = request["section"]
        seed = request["seed"]
    except KeyError as e:
        print(f"Error: Missing required field in request JSON: {e}", file=sys.stderr)
        sys.exit(1)

    reading_level_segment = build_reading_level_segment(reading_level)

    datastore_root = Path(args.datastore)

    # Build expected cache file path:
    # {datastore}/{source_dataset}/{theme}/{reading_level}/{model}/{section}/{seed}.json
    cache_path = (
        datastore_root
        / str(source_dataset)
        / str(theme)
        / reading_level_segment
        / str(model)
        / str(section)
        / f"{seed}.json"
    )

    if not cache_path.is_file():
        print(
            f"Error: Cache file not found at: {cache_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load payload from cache file
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Error: Failed to read/parse cache file: {e}", file=sys.stderr)
        sys.exit(1)

    # Emit combined JSON to stdout
    output = {
        "request": request,
        "payload": payload,
    }

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
