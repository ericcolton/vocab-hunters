#!/usr/bin/env python3
import argparse
import json
import os
import sys
import hashlib
from pathlib import Path

from phase3 import run_with_json as run_phase3_with_json
from phase4 import run_phase4_with_json

from Libraries.reference_data import lookup_source_dataset, lookup_theme

class Phase2Error(Exception):
    def __init__(self, message, exit_code=1):
        super().__init__(message)
        self.exit_code = exit_code


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
    If HOMEWORK_HERO_CONFIG_PATH is set, load JSON and return defaults and path.
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
    
    return default_responses_datastore, config_path


def parse_args(argv=None, default_responses_datastore=None):
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

    # Load lists and compute index mapping based on key_name
    datasets = load_list("source_datasets.json", "source_dataset")
    themes = load_list("themes.json", "theme")
    models = load_list("models.json", "model")

    def find_index_by_keyname(items, key_name, filename):
        for idx, item in enumerate(items):
            if item.get("key_name") == key_name:
                return idx
        raise SystemExit(f"Could not find key_name '{key_name}' in {filename}.")

    dataset_idx = find_index_by_keyname(datasets, source_dataset, "source_datasets.json")
    theme_idx = find_index_by_keyname(themes, theme, "themes.json")
    model_idx = find_index_by_keyname(models, model, "models.json")

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


def process_request(request, responses_datastore, config_path):
    # Extract required fields from the request
    try:
        source_dataset = request["source_dataset"]
        theme = request["theme"]
        reading_level = request["reading_level"]
        model = request["model"]
        section = request["section"]
        seed = request["seed"]
    except KeyError as e:
        raise Phase2Error(f"Missing required field in request JSON: {e}") from e

    reading_level_segment = build_reading_level_segment(reading_level)
    worksheet_id = build_worksheet_id(request, config_path)

    datastore_root = Path(responses_datastore)

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
        phase_3_input["worksheet_id"] = worksheet_id

        # try:
        #     from phase3 import run_with_json as run_phase3_with_json
        # except Exception as e:
        #     raise Phase2Error(f"Failed to import phase3 runner: {e}") from e

        try:
            phase_3_stdout_data = run_phase3_with_json(
                json.dumps(phase_3_input, ensure_ascii=False)
            )
        except SystemExit as e:
            raise Phase2Error(str(e)) from e
        try:
            phase_4_stdout_data = run_phase4_with_json(phase_3_stdout_data)
        except SystemExit as e:
            raise Phase2Error(str(e)) from e
        
        # Write phase_4_stdout_data to cache_path, creating subdirectories as needed
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("w", encoding="utf-8") as f:
            f.write(phase_4_stdout_data)

    # Load payload from cache file
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            output_payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise Phase2Error(f"Failed to read/parse cache file: {e}") from e

    # Add the presentation_metadata from the original request if present
    request_metadata = request.get("presentation_metadata")
    if request_metadata:

        # load dataset metadata for source, source_abbr
        dataset_entry = lookup_source_dataset(source_dataset)
        dataset_title = (dataset_entry or {}).get("title", "")
        dataset_abbr = (dataset_entry or {}).get("title_abbr", "")

        # load theme metadata for theme, theme_abbr
        theme_entry = lookup_theme(theme)
        theme_title = (theme_entry or {}).get("title", "")
        theme_abbr = (theme_entry or {}).get("title_abbr", "")

        presentation_variables = {
            "section": section,
            "reading_system": reading_level["system"],
            "reading_level": reading_level["level"],
            "model": model,
            "episode": seed,
            "worksheet_id": worksheet_id,
            "source": dataset_title,
            "source_abbr": dataset_abbr,
            "theme": theme_title,
            "theme_abbr": theme_abbr,
        }

        interpolated_metadata = dict(request_metadata)
        for key in ("header", "footer", "answer_key_footer"):
            if key in interpolated_metadata:
                template = interpolated_metadata[key]
                if template is None:
                    continue
                text = str(template)
                for var_key, value in presentation_variables.items():
                    placeholder = "{" + var_key + "}"
                    text = text.replace(placeholder, str(value))
                interpolated_metadata[key] = text

        output_payload["presentation_metadata"] = interpolated_metadata

    return output_payload


def run_from_json(request_json, responses_datastore=None, config_path=None):
    default_responses_datastore, default_config_path = load_env_defaults()
    responses_datastore = responses_datastore or default_responses_datastore
    config_path = config_path or default_config_path
    if not responses_datastore or not config_path:
        raise Phase2Error("responses_datastore and config_path are required.")

    try:
        request = json.loads(request_json)
    except json.JSONDecodeError as e:
        raise Phase2Error(f"Failed to parse request JSON: {e}") from e

    output_payload = process_request(
        request,
        responses_datastore=responses_datastore,
        config_path=config_path,
    )
    return json.dumps(output_payload, ensure_ascii=False, indent=2)


def run_with_json(request_json, responses_datastore=None, config_path=None):
    return run_from_json(
        request_json,
        responses_datastore=responses_datastore,
        config_path=config_path,
    )


def main(argv=None):
    default_responses_datastore, config_path = load_env_defaults()
    args = parse_args(argv, default_responses_datastore=default_responses_datastore)

    # Read JSON request from stdin
    try:
        request = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"Error: Failed to parse JSON from stdin: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        output_payload = process_request(
            request,
            responses_datastore=args.responses_datastore,
            config_path=config_path,
        )
    except Phase2Error as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(e.exit_code)

    json.dump(output_payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
