
import hashlib
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
from phase3 import run_with_json as run_phase3_with_json
from phase4 import run_phase4_with_json
from phase5 import run_with_json as run_phase5_with_json
from Libraries.reference_data import (
    get_reference_data_path,
    get_source_datasets_dir,
    get_responses_datastore_path,
    load_source_datasets,
    load_themes,
    lookup_source_dataset,
)

def build_reading_level_segment(reading_level):
    # assume F&P
    return f"fp_{reading_level}"

def build_pdf_filename(source_dataset, theme, section, episode):
    """Build a descriptive PDF filename from worksheet parameters.

    Uses title_abbr from reference data when available, falling back to key_name.
    Format: {source_abbr}-{theme_abbr}-S{section}-E{episode}.pdf
    """
    app_config = get_app_config()

    source_abbr = source_dataset
    for ds in app_config["data_sources"]:
        if ds["id"] == source_dataset:
            source_abbr = ds.get("title_abbr") or ds.get("key_name") or source_dataset
            break

    theme_abbr = theme
    for t in app_config["themes"]:
        if t["id"] == theme:
            theme_abbr = t.get("title_abbr") or t.get("key_name") or theme
            break

    # Sanitize: replace spaces with underscores, remove problematic chars
    def sanitize(s):
        return s.replace(" ", "_").replace("/", "-")

    return f"{sanitize(source_abbr)}-{sanitize(theme_abbr)}-S{section}-E{episode}.pdf"

def get_themes_dir():
    from Libraries.reference_data import get_global_config
    config, _ = get_global_config()
    return config.get("themes_dir")

def save_custom_theme_file(custom_text):
    prefix = "The sentences should take place in a world where "
    wrapped = prefix + custom_text
    content_hash = hashlib.sha256(wrapped.encode("utf-8")).hexdigest()
    file_stem = f"user_specified_{content_hash}"
    themes_dir = get_themes_dir()
    if not themes_dir:
        raise ValueError("themes_dir not configured")
    theme_path = Path(themes_dir) / f"{file_stem}.txt"
    if not theme_path.exists():
        theme_path.parent.mkdir(parents=True, exist_ok=True)
        theme_path.write_text(wrapped, encoding="utf-8")
    return file_stem

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

    pdf_filename = build_pdf_filename(
        source_dataset=params["source_dataset"],
        theme=params["theme"],
        section=params["section"],
        episode=params["seed"],
    )

    viewer = {
        "worksheet_id": worksheet_id,
        "pdf_filename": pdf_filename,
        "params": params,
        "episodes": episodes_list,
        "episode_exists": current_idx is not None,
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

    filename = build_pdf_filename(
        source_dataset=decoded["source_dataset"],
        theme=decoded["theme"],
        section=decoded["section"],
        episode=decoded["seed"],
    )
    resp = Response(pdf_bytes, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    return resp

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

    # --- Custom theme branch: bypass Phase 2 entirely ---
    app_config = get_app_config()
    theme_entry = None
    for t in app_config["themes"]:
        if t["id"] == theme:
            theme_entry = t
            break

    if theme_entry and theme_entry.get("key_name") == "user_specified":
        custom_text = raw_payload.get("custom_theme_text", "").strip()
        if not custom_text:
            return jsonify({"error": "Please describe your custom world."}), 400

        try:
            theme_file_stem = save_custom_theme_file(custom_text)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 500

        # Build payload for Phase 3 with the custom theme file stem
        custom_payload = {
            "source_dataset": source_dataset,
            "theme": theme_file_stem,
            "reading_level": {"system": "fp", "level": reading_level},
            "model": model,
            "section": section,
            "seed": 1,
            "worksheet_id": None,
        }

        try:
            phase3_output = run_phase3_with_json(json.dumps(custom_payload, ensure_ascii=False))
        except SystemExit as exc:
            return jsonify({"error": str(exc)}), 400

        try:
            phase4_output = run_phase4_with_json(phase3_output)
        except SystemExit as exc:
            return jsonify({"error": str(exc)}), 500

        phase4_data = json.loads(phase4_output)

        # Add presentation_metadata with interpolated variables
        presentation_metadata = dict(raw_payload.get("presentation_metadata") or {})
        header_value = raw_payload.get("header") or raw_payload.get("header_text")
        footer_value = raw_payload.get("footer") or raw_payload.get("footer_text")
        answer_key_footer_value = raw_payload.get("answer_key_footer")

        if header_value is not None:
            presentation_metadata["header"] = header_value
        if footer_value is not None:
            presentation_metadata["footer"] = footer_value
        if answer_key_footer_value is not None:
            presentation_metadata["answer_key_footer"] = answer_key_footer_value

        if presentation_metadata:
            dataset_entry = lookup_source_dataset(source_dataset)
            dataset_title = (dataset_entry or {}).get("title", "")
            dataset_abbr = (dataset_entry or {}).get("title_abbr", "")

            presentation_variables = {
                "section": section,
                "reading_system": "fp",
                "reading_level": reading_level,
                "model": model,
                "episode": 1,
                "worksheet_id": "",
                "source": dataset_title,
                "source_abbr": dataset_abbr,
                "theme": "Create Your Own Theme",
                "theme_abbr": "Custom",
            }

            interpolated_metadata = dict(presentation_metadata)
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

            phase4_data["presentation_metadata"] = interpolated_metadata

        # Set worksheet_id to None so Phase 5 falls back to base URL for QR
        phase4_data["worksheet_id"] = None

        try:
            pdf_bytes = run_phase5_with_json(json.dumps(phase4_data, ensure_ascii=False))
        except ValueError as exc:
            return jsonify({"error": f"Failed to build PDF: {exc}"}), 500

        resp = Response(pdf_bytes, mimetype="application/pdf")
        resp.headers["Content-Disposition"] = 'inline; filename="custom-worksheet.pdf"'
        return resp

    # --- Standard theme flow (Phase 2) ---
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
    filename = build_pdf_filename(
        source_dataset=source_dataset,
        theme=theme,
        section=section,
        episode=next_episode,
    )
    resp = Response(pdf_bytes, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'inline; filename="{filename}"'
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

    filename = build_pdf_filename(
        source_dataset=payload["source_dataset"],
        theme=payload["theme"],
        section=payload["section"],
        episode=payload["episode"],
    )
    resp = Response(pdf_bytes, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    return resp

@app.route('/about')
def about():
    return render_template('about.html', config=get_app_config())

if __name__ == '__main__':
    app.run(debug=True)
