import json
import os
from pathlib import Path


def get_global_config():
    config_path = os.environ.get("HOMEWORK_HERO_CONFIG_PATH")
    if not config_path:
        raise RuntimeError("HOMEWORK_HERO_CONFIG_PATH is not set.")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Failed to load HOMEWORK_HERO_CONFIG_PATH='{config_path}': {e}") from e
    return config, config_path


def get_database_path() -> Path:
    config, config_path = get_global_config()
    db = config.get("homework_hero_database")
    if not db:
        raise ValueError(
            f"Config at HOMEWORK_HERO_CONFIG_PATH='{config_path}' missing 'homework_hero_database'. "
            "Old config format (with 'source_datasets', 'prompt_path', etc.) is no longer supported."
        )
    return Path(db)


def ensure_database_dirs():
    db = get_database_path()
    for subdir in ("source_datasets", "themes", "user_themes", "responses_datastore", "reference_data"):
        (db / subdir).mkdir(parents=True, exist_ok=True)


def get_prompt_path() -> Path:
    return get_database_path() / "prompt.txt"


def get_reference_data_path() -> Path:
    return get_database_path() / "reference_data"


def get_source_datasets_dir() -> Path:
    return get_database_path() / "source_datasets"


def get_responses_datastore_path() -> Path:
    return get_database_path() / "responses_datastore"


def get_themes_dir() -> Path:
    return get_database_path() / "themes"


def get_user_themes_dir() -> Path:
    return get_database_path() / "user_themes"


def load_source_datasets():
    reference_data_path = get_reference_data_path()
    source_datasets_path = reference_data_path / "source_datasets.json"
    with open(source_datasets_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("Reference_Data/source_datasets.json must be a list or object.")
    data_sources = []
    for item in data:
        key_name = item.get("key_name")
        if not key_name:
            continue
        title = item.get("title") or key_name.replace("_", " ").title()
        data_sources.append(
            {
                "id": key_name,
                "key_name": key_name,
                "title": title,
                "title_abbr": item.get("title_abbr") or "",
            }
        )
    return data_sources


def lookup_source_dataset(key_name):
    datasets = load_source_datasets()
    lookup = {
        item.get("key_name"): item for item in datasets if item.get("key_name")
    }
    return lookup.get(key_name)


def load_themes():
    reference_data_path = get_reference_data_path()
    themes_path = reference_data_path / "themes.json"
    with open(themes_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("Reference_Data/themes.json must be a list or object.")
    themes = []
    for item in data:
        key_name = item.get("key_name")
        if not key_name:
            continue
        display_title = item.get("title") or key_name.replace("_", " ").title()
        themes.append(
            {
                "id": key_name,
                "key_name": key_name,
                "title": display_title,
                "title_abbr": item.get("title_abbr") or "",
                "css_class": item.get("css_class", ""),
                "ui_title": item.get("ui_title") or display_title,
                "ui_subtitle": item.get("ui_subtitle", ""),
            }
        )
    return themes


def lookup_theme(key_name):
    themes = load_themes()
    lookup = {item.get("key_name"): item for item in themes if item.get("key_name")}
    return lookup.get(key_name)
