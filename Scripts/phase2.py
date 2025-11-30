#!/usr/bin/env python3
import argparse
import json
import subprocess
import os
import sys
import hashlib
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
    If HOMEWORK_HERO_CONFIG_PATH is set, load JSON and return config dict and path.
    """
    config_path = os.environ.get("HOMEWORK_HERO_CONFIG_PATH")
    if not config_path:
        return None, None, None

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
    
    return default_responses_datastore, default_scripts_dir, config_path


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

def build_worksheet_id(request, config_path):
    """
    Encode source_dataset, theme, reading_level, model, section, and seed into an opaque
    but reversible integer worksheet_id.
    
    - Lookups reference data files from config["reference_data"] directory
    - Encodes IDs using bit packing and reversible obfuscation
    - Exits with non-zero code if any lookup fails
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"Failed to load config from '{config_path}': {e}") from e

    reference_data_dir = config.get("reference_data")
    if not reference_data_dir:
        raise SystemExit(
            f"Config at '{config_path}' missing 'reference_data' key."
        )

    reference_data_dir = Path(reference_data_dir)

    # Extract required fields from request
    try:
        source_dataset = request["source_dataset"]
        theme = request["theme"]
        reading_level = request["reading_level"]
        model = request["model"]
        section = request["section"]
        seed = request["seed"]
    except KeyError as e:
        raise SystemExit(f"Request missing required field for worksheeet_id: {e}") from e

    # Helper: load reference file and return list of items
    def load_list(filename, field_name):
        ref_path = reference_data_dir / filename
        try:
            with open(ref_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                raise SystemExit(f"Reference file {filename} ({field_name}) is not a list or object.")
            return data
        except (OSError, json.JSONDecodeError) as e:
            raise SystemExit(f"Failed to load reference data from {filename} ({field_name}): {e}") from e

    # Load lists and compute index mapping based on short_name
    datasets = load_list("source_datasets.json", "source_dataset")
    themes = load_list("themes.json", "theme")
    models = load_list("models.json", "model")

    def find_index_by_shortname(items, short_name, filename):
        for idx, item in enumerate(items):
            if item.get("short_name") == short_name:
                return idx
        raise SystemExit(f"Could not find short_name '{short_name}' in {filename}.")

    dataset_idx = find_index_by_shortname(datasets, source_dataset, "source_datasets.json")
    theme_idx = find_index_by_shortname(themes, theme, "themes.json")
    model_idx = find_index_by_shortname(models, model, "models.json")

    # Find reading_level_id
    if isinstance(reading_level, dict):
        reading_system = reading_level.get("system", None)
        reading_level_val = reading_level.get("level", None)
        if reading_system is None or reading_level_val is None:
            raise SystemExit(
                "reading_level must contain 'system' and 'level' keys."
            )
        if reading_system == "fp":
            # 'A' -> 0, 'B' -> 1, etc.
            reading_level_id = ord(str(reading_level_val).upper()) - ord('A')
        elif reading_system == "grade":
            reading_level_id = int(reading_level_val) + 30
        else:
            raise SystemExit(
                f"Unknown reading_level system: '{reading_system}'."
            )
    else:
        # Accept integers or numeric strings as fallback
        try:
            reading_level_id = int(reading_level)
        except Exception:
            raise SystemExit("reading_level must be a dict or integer-like value.") from None

    # Normalize and validate integer fields (section and seed may be strings)
    try:
        section_int = int(section)
    except Exception:
        raise SystemExit("section must be an integer or integer-like string.") from None

    try:
        seed_int = int(seed)
    except Exception:
        raise SystemExit("seed must be an integer or integer-like string.") from None

    # Bit-size allocations (must match the spec)
    DATASET_BITS = 7
    THEME_BITS = 7
    MODEL_BITS = 5
    READING_BITS = 6
    SECTION_BITS = 7
    SEED_BITS = 8

    # Validate index ranges
    if not (0 <= dataset_idx < (1 << DATASET_BITS)):
        raise SystemExit(f"dataset_idx {dataset_idx} out of range for {DATASET_BITS} bits")
    if not (0 <= theme_idx < (1 << THEME_BITS)):
        raise SystemExit(f"theme_idx {theme_idx} out of range for {THEME_BITS} bits")
    if not (0 <= model_idx < (1 << MODEL_BITS)):
        raise SystemExit(f"model_idx {model_idx} out of range for {MODEL_BITS} bits")
    if not (0 <= reading_level_id < (1 << READING_BITS)):
        raise SystemExit(f"reading_level_id {reading_level_id} out of range for {READING_BITS} bits")
    if not (0 <= section_int < (1 << SECTION_BITS)):
        raise SystemExit(f"section {section_int} out of range for {SECTION_BITS} bits")
    if not (0 <= seed_int < (1 << SEED_BITS)):
        raise SystemExit(f"seed {seed_int} out of range for {SEED_BITS} bits")

    # Bit positions (seed lowest)
    seed_shift = 0
    section_shift = seed_shift + SEED_BITS
    reading_shift = section_shift + SECTION_BITS
    model_shift = reading_shift + READING_BITS
    theme_shift = model_shift + MODEL_BITS
    dataset_shift = theme_shift + THEME_BITS

    packed = (
        ((dataset_idx & ((1 << DATASET_BITS) - 1)) << dataset_shift)
        | ((theme_idx & ((1 << THEME_BITS) - 1)) << theme_shift)
        | ((model_idx & ((1 << MODEL_BITS) - 1)) << model_shift)
        | ((reading_level_id & ((1 << READING_BITS) - 1)) << reading_shift)
        | ((section_int & ((1 << SECTION_BITS) - 1)) << section_shift)
        | ((seed_int & ((1 << SEED_BITS) - 1)) << seed_shift)
    )

    # Reversible obfuscation: XOR with a fixed 64-bit key
    OBFUSCATION_KEY = 0xA5A5A5A5A5
    obfuscated = (packed ^ OBFUSCATION_KEY)

    # Return as a lowercase hexadecimal string without the '0x' prefix (e.g. '1a2b3c').
    # This is reversible: int(hex_string, 16) -> XOR with same key -> unpack bits
    hex_digits = format(int(obfuscated), 'x')

    # Zero-pad hex digits to at least 10 characters (left-pad with zeros)
    hex_padded = hex_digits.zfill(10)

    # Insert seperators
    hex_str = hex_padded[:2] + '-' + hex_padded[2:6] + '-' + hex_padded[6:]
    return hex_str


def main(argv=None):
    default_responses_datastore, default_scripts_dir, config_path = load_env_defaults()
    args = parse_args(argv, default_responses_datastore=default_responses_datastore, default_scripts_dir=default_scripts_dir)

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

    datastore_root = Path(args.responses_datastore)

    # Build expected cache file path:
    # {responses_datastore}/{source_dataset}/{theme}/{reading_level}/{model}/{section}/{seed}.json
    cache_path = (
        datastore_root
        / str(source_dataset)
        / reading_level_segment
        / str(section)
        / str(theme)
        / str(model)
        / f"{seed}.json"
    )

    if not cache_path.is_file():

        phase_3_input_json = json.dumps(request, ensure_ascii=False)
        # Remove presentation_metadata from phase3 input
        phase_3_input = json.loads(phase_3_input_json)
        phase_3_input.pop("presentation_metadata", None)

        # Build and add worksheet_id
        worksheet_id = build_worksheet_id(request, config_path)
        phase_3_input["worksheet_id"] = worksheet_id

        phase3_path = Path(args.scripts) / "phase3.py"
        process = subprocess.run(
            ["python3", str(phase3_path)],
            input=json.dumps(phase_3_input, ensure_ascii=False),
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
            output_payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Error: Failed to read/parse cache file: {e}", file=sys.stderr)
        sys.exit(1)

    # Emit combined JSON to stdout
    # Add the presentation_metadata from the original request if present
    request_metadata = request.get("presentation_metadata")
    if request_metadata:
        output_payload["presentation_metadata"] = request_metadata

    json.dump(output_payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
