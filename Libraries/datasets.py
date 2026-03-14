import json
from pathlib import Path

from Libraries.reference_data import get_source_datasets_dir


def load_dataset(source_dataset: str):
    source_dir = get_source_datasets_dir()
    path = source_dir / f"{source_dataset}.json"
    if not path.is_file():
        raise SystemExit(f"Dataset file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Failed to parse dataset JSON at {path}: {exc}")
