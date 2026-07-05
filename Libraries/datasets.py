import json
from pathlib import Path
from typing import Optional

from Libraries.reference_data import get_source_datasets_dir


class DatasetError(Exception):
    pass


def load_dataset(source_dataset: str, datasets_dir: Optional[Path] = None):
    source_dir = Path(datasets_dir) if datasets_dir is not None else get_source_datasets_dir()
    path = source_dir / f"{source_dataset}.json"
    if not path.is_file():
        raise DatasetError(f"Dataset file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise DatasetError(f"Failed to parse dataset JSON at {path}: {exc}")
