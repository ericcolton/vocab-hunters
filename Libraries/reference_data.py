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


def get_reference_data_path():
    config, config_path = get_global_config()
    reference_data_path = config.get("reference_data")
    if not reference_data_path:
        raise RuntimeError(
            f"Config at HOMEWORK_HERO_CONFIG_PATH='{config_path}' missing 'reference_data'."
        )
    return Path(reference_data_path)


def get_source_datasets_dir():
    config, config_path = get_global_config()
    source_datasets_dir = config.get("source_datasets")
    if not source_datasets_dir:
        raise RuntimeError(
            f"Config at HOMEWORK_HERO_CONFIG_PATH='{config_path}' missing 'source_datasets'."
        )
    return Path(source_datasets_dir)


def get_responses_datastore_path():
    config, config_path = get_global_config()
    responses_datastore = config.get("responses_datastore")
    if not responses_datastore:
        raise RuntimeError(
            f"Config at HOMEWORK_HERO_CONFIG_PATH='{config_path}' missing 'responses_datastore'."
        )
    return Path(responses_datastore)


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
