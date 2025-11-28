#!/usr/bin/env python3
import argparse
import json
import subprocess
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


def load_env_defaults():
    """
    If HOMEWORK_HERO_CONFIG_PATH is set, load JSON and return
    config["source_datasets"] as the default datastore path.
    """
    config_path = os.environ.get("HOMEWORK_HERO_CONFIG_PATH")
    if not config_path:
        return None, None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"Failed to load HOMEWORK_HERO_CONFIG_PATH='{config_path}': {e}") from e

    default_responses_datastore = config.get("responses_datastore")
    if default_responses_datastore is None:
        raise SystemExit(
            f"Config at HOMEWORK_HERO_CONFIG_PATH='{config_path}' missing 'responses_datastore'."
        )
    
    default_scripts_dir = config.get("scripts")
    if default_scripts_dir is None:
        raise SystemExit(
            f"Config at HOMEWORK_HERO_CONFIG_PATH='{config_path}' missing 'scripts'."
        )
    
    return default_responses_datastore, default_scripts_dir


def parse_args(argv=None, default_responses_datastore=None, default_scripts_dir=None):
    parser = argparse.ArgumentParser(
        description="Look up cached payloads for a vocab request."
    )
    parser.add_argument(
        "-d",
        "--responses-datastore",
        default=default_responses_datastore,
        required=default_responses_datastore is None,
        help=(
            "Root directory of the responses datastore. "
            "Defaults to config['responses_datastore'] when HOMEWORK_HERO_CONFIG_PATH is set."
        ),
    )
    parser.add_argument(
        "-s",
        "--scripts",
        default=default_scripts_dir,
        required=default_scripts_dir is None,
        help=(
            "Directory containing the phase scripts. "
            "Defaults to config['scripts'] when HOMEWORK_HERO_CONFIG_PATH is set."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None):
    default_responses_datastore, default_scripts_dir = load_env_defaults()
    args = parse_args(argv, default_responses_datastore=default_responses_datastore, default_scripts_dir=default_scripts_dir)

    # Read JSON request from stdin and save to variable for later access
    stdin_data = sys.stdin.read()
    try:
        request = json.loads(stdin_data)
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

    datastore_root = Path(args.responses_datastore)

    # Build expected cache file path:
    # {responses_datastore}/{source_dataset}/{theme}/{reading_level}/{model}/{section}/{seed}.json
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
        # print(
        #     f"Error: Cache file not found at: {cache_path}",
        #     file=sys.stderr,
        # )
        # sys.exit(1)
        # If cache file is missing, execute phase3.py with stdin_data
        phase3_path = Path(args.scripts) / "phase3.py"
        process = subprocess.run(
            ["python3", str(phase3_path)],
            input=stdin_data,
            text=True,
            capture_output=True
        )
        phase_3_return_code = process.returncode
        phase_3_stdout_data = process.stdout
        phase_3_stderr = process.stderr
        if phase_3_return_code != 0:
            print(f"Error: phase3.py failed with return code {phase_3_return_code}", file=sys.stderr)
            print(phase_3_stderr, file=sys.stderr)
            sys.exit(phase_3_return_code)
        # Execute phase4.py with stdout_data from phase3
        phase4_path = Path(args.scripts) / "phase4.py"
        process = subprocess.run(
            ["python3", str(phase4_path)],
            input=phase_3_stdout_data,
            text=True,
            capture_output=True
        )
        phase_4_return_code = process.returncode
        phase_4_stdout_data = process.stdout
        phase_4_stderr = process.stderr
        if phase_4_return_code != 0:
            print(f"Error: phase4.py failed with return code {phase_4_return_code}", file=sys.stderr)
            print(phase_4_stderr, file=sys.stderr)
            sys.exit(phase_4_return_code)
        
        # Write phase_4_stdout_data to cache_path, creating subdirectories as needed
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("w", encoding="utf-8") as f:
            f.write(phase_4_stdout_data)

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
