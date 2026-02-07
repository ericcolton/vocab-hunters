
import json
import logging
import sys
from functools import lru_cache
from pathlib import Path

from flask import Flask, render_template, request, jsonify, Response, redirect, url_for

app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)
app.logger.setLevel(logging.DEBUG)

scripts_dir = Path(__file__).resolve().parent / "Scripts"
if str(scripts_dir) not in sys.path:
    sys.path.append(str(scripts_dir))

from phase2 import run_with_json, Phase2Error, decode_worksheet_id, build_worksheet_id, load_env_defaults
from phase5 import run_with_json as run_phase5_with_json
from Libraries.reference_data import (
    get_reference_data_path,
    get_source_datasets_dir,
    get_responses_datastore_path,
    load_source_datasets,
    load_themes,
)

def build_reading_level_segment(reading_level):
    # assume F&P
    return f"fp_{reading_level}"

def build_worksheet_id_from_params(source_dataset, theme, model, reading_level, section, seed):
    _, config_path = load_env_defaults()
    if not config_path:
        return None
    request_dict = {
        "source_dataset": source_dataset,
        "theme": theme,
        "model": model,
        "reading_level": {"system": "fp", "level": reading_level},
        "section": section,
        "seed": seed,
    }
    try:
        return build_worksheet_id(request_dict, config_path)
    except (SystemExit, Exception):
        return None

def list_cached_episodes(source_dataset, theme, reading_level, model, section):
    datastore_root = get_responses_datastore_path()
    reading_level_segment = build_reading_level_segment(reading_level)
    cache_dir = (
        datastore_root
        / str(source_dataset)
        / reading_level_segment
        / str(section)
        / str(theme)
        / str(model)
    )

    if not cache_dir.is_dir():
        return []
    episodes = []
    for path in cache_dir.iterdir():
        if path.is_file() and path.suffix == ".json" and path.stem.isdigit():
            subtitle = ""
            try:
                with path.open("r", encoding="utf-8") as f:
                    payload = json.load(f)
                subtitle = (
                    (payload.get("output") or {}).get("subtitle")
                    or (payload.get("presentation_metadata") or {}).get("subtitle")
                    or ""
                )
            except (OSError, json.JSONDecodeError):
                subtitle = ""
            episodes.append({"episode": int(path.stem), "subtitle": subtitle})
    return sorted(episodes, key=lambda item: item["episode"])

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
        display_title = item.get("title") or key_name
        is_default = bool(item.get("is_default")) and not default_set
        if is_default:
            default_set = True
        models.append(
            {"id": key_name, "title": display_title, "is_default": is_default}
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

@lru_cache(maxsize=1)
def get_app_config():
    data_sources = load_source_datasets()
    default_sections = (
        load_sections_for_dataset(data_sources[0]["id"]) if data_sources else []
    )
    return {
        "data_sources": data_sources,
        "themes": load_themes(),
        "models": load_models(),
        "sections": default_sections,
        "levels": list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),  # Generates ['A', 'B', ... 'Z']
    }

@app.route('/')
def landing():
    return render_template('landing.html', config=get_app_config())

@app.route('/worksheets')
def worksheets():
    return render_template('generator.html', config=get_app_config(), worksheet_params=None)

@app.route('/worksheet')
def worksheet():
    worksheet_id = request.args.get('id')
    if not worksheet_id:
        return redirect(url_for('worksheets'))

    _, config_path = load_env_defaults()
    if not config_path:
        return redirect(url_for('worksheets'))

    try:
        decoded = decode_worksheet_id(worksheet_id, config_path)
    except Phase2Error:
        return redirect(url_for('worksheets'))

    reading_level = decoded["reading_level"]
    rl_letter = reading_level.get("level")
    params = {
        "source_dataset": decoded["source_dataset"],
        "theme": decoded["theme"],
        "model": decoded["model"],
        "reading_level": rl_letter,
        "section": decoded["section"],
        "seed": decoded["seed"],
    }

    # List all cached episodes for this config
    episodes_list = list_cached_episodes(
        source_dataset=params["source_dataset"],
        theme=params["theme"],
        reading_level=params["reading_level"],
        model=params["model"],
        section=params["section"],
    )

    # Enrich episodes with worksheet IDs
    for ep in episodes_list:
        ep["worksheet_id"] = build_worksheet_id_from_params(
            source_dataset=params["source_dataset"],
            theme=params["theme"],
            model=params["model"],
            reading_level=params["reading_level"],
            section=params["section"],
            seed=ep["episode"],
        )

    # Find current position and compute prev/next
    current_seed = params["seed"]
    current_idx = None
    for i, ep in enumerate(episodes_list):
        if ep["episode"] == current_seed:
            current_idx = i
            break

    prev_worksheet_id = None
    next_worksheet_id = None
    next_is_generate = False
    next_generate_episode = None

    SEED_BITS = 8
    max_seed = (1 << SEED_BITS) - 1

    if current_idx is not None:
        if current_idx > 0:
            prev_worksheet_id = episodes_list[current_idx - 1]["worksheet_id"]
        if current_idx < len(episodes_list) - 1:
            next_worksheet_id = episodes_list[current_idx + 1]["worksheet_id"]
        else:
            # At the last cached episode — next triggers generate
            last_episode = episodes_list[-1]["episode"]
            if last_episode < max_seed:
                next_is_generate = True
                next_generate_episode = last_episode + 1

    # Resolve theme entry for CSS class
    app_config = get_app_config()
    theme_entry = None
    for t in app_config["themes"]:
        if t["id"] == params["theme"]:
            theme_entry = t
            break
    if not theme_entry and app_config["themes"]:
        theme_entry = app_config["themes"][0]

    viewer = {
        "worksheet_id": worksheet_id,
        "params": params,
        "episodes": episodes_list,
        "prev_worksheet_id": prev_worksheet_id,
        "next_worksheet_id": next_worksheet_id,
        "next_is_generate": next_is_generate,
        "next_generate_episode": next_generate_episode,
        "theme_entry": theme_entry,
    }

    return render_template('viewer.html', viewer=viewer, config=app_config)

@app.route('/worksheet_pdf')
def worksheet_pdf():
    worksheet_id = request.args.get('id')
    if not worksheet_id:
        return jsonify({"error": "Missing worksheet id"}), 400

    _, config_path = load_env_defaults()
    if not config_path:
        return jsonify({"error": "Server configuration error"}), 500

    try:
        decoded = decode_worksheet_id(worksheet_id, config_path)
    except Phase2Error as exc:
        return jsonify({"error": str(exc)}), 400

    reading_level = decoded["reading_level"]
    payload = {
        "source_dataset": decoded["source_dataset"],
        "theme": decoded["theme"],
        "model": decoded["model"],
        "reading_level": reading_level,
        "section": decoded["section"],
        "seed": decoded["seed"],
        "episode": decoded["seed"],
        "presentation_metadata": {
            "header": "{theme} - Section {section}",
            "footer": "Page {current_page} of {total_pages}",
            "answer_key_footer": "Fountas & Pinnell Level {reading_level}",
        },
    }

    try:
        response_json = run_with_json(json.dumps(payload, ensure_ascii=False))
    except Phase2Error as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        pdf_bytes = run_phase5_with_json(response_json)
    except ValueError as exc:
        return jsonify({"error": f"Failed to build PDF: {exc}"}), 500

    return Response(pdf_bytes, mimetype="application/pdf")

@app.route('/generate', methods=['POST'])
def generate():
    raw_payload = request.get_json(silent=True)
    if raw_payload is None:
        raw_payload = request.form.to_dict()

    source_dataset = raw_payload.get("source_dataset") or raw_payload.get("datasource")
    theme = raw_payload.get("theme")
    reading_level = raw_payload.get("reading_level") or raw_payload.get("level")
    if isinstance(reading_level, dict):
        reading_level = reading_level.get("level")
    model = raw_payload.get("model")
    section = raw_payload.get("section")

    if not all([source_dataset, theme, reading_level, model, section]):
        return jsonify({"error": "Missing required fields."}), 400

    try:
        episodes_list = list_cached_episodes(
            source_dataset=source_dataset,
            theme=theme,
            reading_level=reading_level,
            model=model,
            section=section,
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    next_episode = episodes_list[-1]["episode"] + 1 if episodes_list else 1

    presentation_metadata = dict(raw_payload.get("presentation_metadata") or {})
    header_value = raw_payload.get("header")
    if header_value is None:
        header_value = raw_payload.get("header_text")
    footer_value = raw_payload.get("footer")
    if footer_value is None:
        footer_value = raw_payload.get("footer_text")
    answer_key_footer_value = raw_payload.get("answer_key_footer")

    if header_value is not None:
        presentation_metadata["header"] = header_value
    if footer_value is not None:
        presentation_metadata["footer"] = footer_value
    if answer_key_footer_value is not None:
        presentation_metadata["answer_key_footer"] = answer_key_footer_value

    payload = {
        "source_dataset": source_dataset,
        "theme": theme,
        "reading_level": {"system": "fp", "level": reading_level},
        "model": model,
        "section": section,
        "episode": next_episode,
        "seed": next_episode,
    }
    if presentation_metadata:
        payload["presentation_metadata"] = presentation_metadata

    try:
        response_json = run_with_json(json.dumps(payload, ensure_ascii=False))
        response_payload = json.loads(response_json)
    except Phase2Error as exc:
        return jsonify({"error": str(exc)}), 400
    except json.JSONDecodeError as exc:
        return jsonify({"error": f"Failed to parse phase2 response: {exc}"}), 500

    try:
        pdf_bytes = run_phase5_with_json(response_json)
    except ValueError as exc:
        return jsonify({"error": f"Failed to build PDF: {exc}"}), 500

    new_worksheet_id = build_worksheet_id_from_params(
        source_dataset=source_dataset,
        theme=theme,
        model=model,
        reading_level=reading_level,
        section=section,
        seed=next_episode,
    )
    resp = Response(pdf_bytes, mimetype="application/pdf")
    if new_worksheet_id:
        resp.headers["X-Worksheet-Id"] = new_worksheet_id
    return resp

@app.route('/sections/<source_dataset>')
def sections(source_dataset):
    try:
        sections = load_sections_for_dataset(source_dataset)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"sections": sections})

@app.route('/episodes')
def episodes():
    source_dataset = request.args.get("source_dataset")
    theme = request.args.get("theme")
    reading_level = request.args.get("reading_level")
    model = request.args.get("model")
    section = request.args.get("section")
    if not all([source_dataset, theme, reading_level, model, section]):
        return jsonify({"episodes": []})
    try:
        episodes_list = list_cached_episodes(
            source_dataset=source_dataset,
            theme=theme,
            reading_level=reading_level,
            model=model,
            section=section,
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    for ep in episodes_list:
        ep["worksheet_id"] = build_worksheet_id_from_params(
            source_dataset=source_dataset,
            theme=theme,
            model=model,
            reading_level=reading_level,
            section=section,
            seed=ep["episode"],
        )
    return jsonify({"episodes": episodes_list})

@app.route('/fetch_episode', methods=['POST'])
def fetch_episode():
    payload = request.get_json(silent=True) or {}
    presentation_metadata = payload.get("presentation_metadata") or {}
    for key in ("header", "footer", "answer_key_footer"):
        if key in payload:
            presentation_metadata[key] = payload.pop(key)
    if presentation_metadata:
        payload["presentation_metadata"] = presentation_metadata
    payload["seed"] = payload["episode"]
    payload["reading_level"] = {"system": "fp", "level": payload["reading_level"]}

    try:
        response_json = run_with_json(json.dumps(payload, ensure_ascii=False))
        response_payload = json.loads(response_json)
    except Phase2Error as exc:
        return jsonify({"error": str(exc)}), 400
    except json.JSONDecodeError as exc:
        return jsonify({"error": f"Failed to parse phase2 response: {exc}"}), 500

    try:
        pdf_bytes = run_phase5_with_json(response_json)
    except ValueError as exc:
        return jsonify({"error": f"Failed to build PDF: {exc}"}), 500

    return Response(pdf_bytes, mimetype="application/pdf")

@app.route('/about')
def about():
    return render_template('about.html', config=get_app_config())

if __name__ == '__main__':
    app.run(debug=True)
