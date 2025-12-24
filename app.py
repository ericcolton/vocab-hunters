
import json
import os
from pathlib import Path

from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

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
        name = item.get("name") or key_name.replace("_", " ").title()
        data_sources.append({"id": key_name, "name": name})
    return data_sources

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
        display_name = item.get("name") or key_name.replace("_", " ").title()
        themes.append(
            {
                "id": key_name,
                "name": display_name,
                "css_class": item.get("css_class", ""),
                "ui_title": item.get("ui_title") or display_name,
                "ui_subtitle": item.get("ui_subtitle", ""),
            }
        )
    return themes

def load_models():
    reference_data_path = get_reference_data_path()
    models_path = reference_data_path / "models.json"
    with open(models_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("Reference_Data/models.json must be a list or object.")
    models = []
    default_set = False
    for item in data:
        key_name = item.get("key_name")
        if not key_name:
            continue
        display_name = item.get("name") or key_name
        is_default = bool(item.get("is_default")) and not default_set
        if is_default:
            default_set = True
        models.append(
            {"id": key_name, "name": display_name, "is_default": is_default}
        )
    if models and not default_set:
        models[0]["is_default"] = True
    return models

def load_sections_for_dataset(source_dataset):
    source_datasets_dir = get_source_datasets_dir()
    dataset_path = source_datasets_dir / f"{source_dataset}.json"
    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    sections = data.get("sections", [])
    if not isinstance(sections, list):
        return []
    section_numbers = []
    for section in sections:
        if isinstance(section, dict) and "section" in section:
            section_numbers.append(section["section"])
    if section_numbers:
        return sorted({int(s) for s in section_numbers if str(s).isdigit()})
    return list(range(1, len(sections) + 1))

# --- CONFIGURATION / PLUGINS ---
data_sources = load_source_datasets()
default_sections = (
    load_sections_for_dataset(data_sources[0]["id"]) if data_sources else []
)
app_config = {
    "data_sources": data_sources,
    "themes": load_themes(),
    "models": load_models(),
    "sections": default_sections,
    "levels": list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") # Generates ['A', 'B', ... 'Z']
}

@app.route('/')
def index():
    # Pass the config to the template to generate dropdowns dynamically
    return render_template('index.html', config=app_config)

@app.route('/generate', methods=['POST'])
def generate():
    # Placeholder for your generation logic
    data = request.form
    print(f"Generating with: {data}")
    return jsonify({"status": "success", "message": "Worksheet generation started..."})

@app.route('/sections/<source_dataset>')
def sections(source_dataset):
    try:
        sections = load_sections_for_dataset(source_dataset)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"sections": sections})

@app.route('/about')
def about():
    return render_template('about.html', config=app_config)

if __name__ == '__main__':
    app.run(debug=True)
