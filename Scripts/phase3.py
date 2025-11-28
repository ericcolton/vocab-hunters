#!/usr/bin/env python3
import argparse
import os
import hashlib
import json
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build phase 3 request JSON from a source dataset."
    )
    # If HOMEWORK_HERO_CONFIG_PATH is set and points to a JSON file,
    # parse it and use its `source_datasets` key as the default value.
    default_source_datasets = None
    config_path = os.environ.get("HOMEWORK_HERO_CONFIG_PATH")
    if config_path:
        cfg_path = Path(config_path)
        if not cfg_path.is_file():
            raise SystemExit(f"HOMEWORK_HERO_CONFIG_PATH points to non-existent file: {cfg_path}")
        try:
            with cfg_path.open("r", encoding="utf-8") as cf:
                cfg = json.load(cf)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Failed to parse JSON config at {cfg_path}: {exc}")
        default_source_datasets = cfg.get("source_datasets")

    parser.add_argument(
        "-s",
        "--source-datasets",
        required=(default_source_datasets is None),
        default=default_source_datasets,
        help=(
            "Directory containing source dataset JSON files. "
            "Default may be provided by HOMEWORK_HERO_CONFIG_PATH JSON key 'source_datasets'."
        ),
    )
    return parser.parse_args()


def load_request(stdin_data: str):
    try:
        return json.loads(stdin_data)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Failed to parse JSON from stdin: {exc}")


def load_dataset(source_dir: str, source_dataset: str):
    path = Path(source_dir) / f"{source_dataset}.json"
    if not path.is_file():
        raise SystemExit(f"Dataset file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Failed to parse dataset JSON at {path}: {exc}")


def find_section(dataset: dict, section_number: int) -> dict:
    sections = dataset.get("sections", [])
    for section in sections:
        if section.get("section") == section_number:
            return section
    raise SystemExit(f"Section {section_number} not found in dataset.")


def build_reading_level_token(reading_level: dict) -> str:
    # Expecting {"system": "...", "level": "..."}
    if not isinstance(reading_level, dict):
        raise SystemExit("reading_level must be an object with 'system' and 'level'.")
    system = reading_level.get("system")
    level = reading_level.get("level")
    if system is None or level is None:
        raise SystemExit("reading_level must contain 'system' and 'level' keys.")
    return f"{system}-{level}"


def sha256_prefix_16(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def build_output(request: dict, section_entries: list) -> dict:
    # Top-level fields coming straight from the input
    source_dataset = request["source_dataset"]
    reading_level = request["reading_level"]
    theme = request["theme"]
    model = request["model"]
    seed = request["seed"]

    metadata = request.get("metadata", {})
    # Prefer metadata.section, fall back to top-level 'section' if needed.
    if "section" in metadata:
        section_number = metadata["section"]
    else:
        section_number = request.get("section")
    if section_number is None:
        raise SystemExit("Section number not found in request (metadata.section or section).")

    header = metadata.get("header")
    footer = metadata.get("footer")
    if header is None or footer is None:
        raise SystemExit("metadata.header and metadata.footer must be present in the request.")

    # Build doc_key = source_dataset | reading_level_token | model | theme | seed
    reading_level_token = build_reading_level_token(reading_level)
    doc_key = "|".join(
        [
            source_dataset,
            reading_level_token,
            model,
            theme,
            str(seed),
        ]
    )
    doc_checksum = sha256_prefix_16(doc_key)

    # Build data section
    data_items = []
    for entry in section_entries:
        word = entry["word"]
        part_of_speech = entry["part_of_speech"]
        definition = entry["definition"]
        def_num = entry.get("def_num")

        # key = doc_key | word | part_of_speech | definition
        key = "|".join(
            [
                doc_key,
                word,
                part_of_speech,
                definition,
            ]
        )
        checksum = sha256_prefix_16(key)

        data_items.append(
            {
                "word": word,
                "part_of_speech": part_of_speech,
                "definition": definition,
                "def_num": def_num,
                "key": key,
                "checksum": checksum,
            }
        )

    output = {
        "type": request.get("type", "build_request"),
        "source_dataset": source_dataset,
        "reading_level": reading_level,
        "theme": theme,
        "model": model,
        "seed": seed,
        "doc_key": doc_key,
        "doc_checksum": doc_checksum,
        "metadata": {
            "section": section_number,
            "header": header,
            "footer": footer,
        },
        "data": data_items,
    }
    return output


def main():
    args = parse_args()

    stdin_data = sys.stdin.read()
    if not stdin_data.strip():
        raise SystemExit("No JSON input provided on stdin.")

    request = load_request(stdin_data)

    # # Make sure required top-level fields are present
    # for key in ("source_dir"):
    #     if key not in request:
    #         raise SystemExit(f"Missing required key in request: {key}")

    source_dataset = request["source_dataset"]
    dataset = load_dataset(args.source_datasets, source_dataset)

    # Determine section number from request (metadata.section or section)
    section_number = request.get("section")
    if section_number is None:
        raise SystemExit("Section number not found in request (section).")

    section_obj = find_section(dataset, section_number)
    entries = section_obj.get("entries", [])

    output = build_output(request, entries)
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

