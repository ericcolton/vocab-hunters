
import json
import os
from pathlib import Path

from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

def get_reference_data_path():
    config_path = os.environ.get("HOMEWORK_HERO_CONFIG_PATH")
    if not config_path:
        raise RuntimeError("HOMEWORK_HERO_CONFIG_PATH is not set.")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Failed to load HOMEWORK_HERO_CONFIG_PATH='{config_path}': {e}") from e
    
    reference_data_path = config.get("reference_data")
    if not reference_data_path:
        raise RuntimeError(
            f"Config at HOMEWORK_HERO_CONFIG_PATH='{config_path}' missing 'reference_data'."
        )
    return Path(reference_data_path)

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
        short_name = item.get("short_name")
        if not short_name:
            continue
        name = item.get("name") or short_name.replace("_", " ").title()
        data_sources.append({"id": short_name, "name": name})
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
        short_name = item.get("short_name")
        if not short_name:
            continue
        display_name = item.get("name") or short_name.replace("_", " ").title()
        themes.append(
            {
                "id": short_name,
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
        short_name = item.get("short_name")
        if not short_name:
            continue
        display_name = item.get("name") or short_name
        is_default = bool(item.get("is_default")) and not default_set
        if is_default:
            default_set = True
        models.append(
            {"id": short_name, "name": display_name, "is_default": is_default}
        )
    if models and not default_set:
        models[0]["is_default"] = True
    return models

# --- CONFIGURATION / PLUGINS ---
app_config = {
    "data_sources": load_source_datasets(),
    "themes": load_themes(),
    "models": load_models(),
    "sections": list(range(1, 16)), # Generates [1, 2, ... 15]
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

@app.route('/about')
def about():
    return render_template('about.html', config=app_config)

if __name__ == '__main__':
    app.run(debug=True)
