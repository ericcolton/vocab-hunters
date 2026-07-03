
import json
import logging
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from functools import lru_cache
from pathlib import Path

from flask import Flask, render_template, request, jsonify, Response, redirect, url_for

app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)
app.logger.setLevel(logging.DEBUG)

_secret_key = os.environ.get("SECRET_KEY")
if not _secret_key:
    if os.environ.get("HOMEWORK_HERO_DEV") == "1":
        _secret_key = "dev-only-insecure-secret"
    else:
        raise RuntimeError(
            "SECRET_KEY environment variable is required "
            "(set HOMEWORK_HERO_DEV=1 for local development)."
        )
app.config.update(
    SECRET_KEY=_secret_key,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "1") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)

scripts_dir = Path(__file__).resolve().parent / "Scripts"
if str(scripts_dir) not in sys.path:
    sys.path.append(str(scripts_dir))

from phase2 import run_with_json, Phase2Error, decode_worksheet_id, build_worksheet_id
from phase3 import run_with_json as run_phase3_with_json
from phase4 import run_phase4_with_json
from phase5 import run_with_json as run_phase5_with_json
from Libraries.reference_data import (
    get_reference_data_path,
    get_responses_datastore_path,
    get_user_themes_dir,
    load_source_datasets,
    load_themes,
    lookup_source_dataset,
    validate_key_component,
)
from auth import auth_bp, current_user, init_auth_db, login_required, validate_csrf
from Libraries.user_data import (
    UserDataError,
    build_theme_content,
    get_user_source_datasets_dir,
    get_user_theme,
    is_user_key,
    list_user_datasets,
    list_user_episodes,
    list_user_themes,
    list_user_worksheet_groups,
    make_user_key,
    save_user_dataset,
    save_user_theme,
    strip_user_key,
)
from Libraries.user_pipeline import (
    UserPipelineError,
    generate_user_worksheet,
    interpolate_presentation_metadata,
)
from Libraries.datasets import DatasetError

app.register_blueprint(auth_bp)
init_auth_db()

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

def _sanitize_theme_name(text: str) -> str:
    sanitized = re.sub(r'\s+', '_', text.strip())
    sanitized = re.sub(r'[/\\\x00]', '', sanitized)
    return sanitized[:255] or 'custom'


def record_user_theme_episode(custom_text: str):
    file_stem = _sanitize_theme_name(custom_text)
    user_themes_dir = get_user_themes_dir()
    user_themes_dir.mkdir(parents=True, exist_ok=True)
    theme_path = user_themes_dir / f"{file_stem}.txt"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with theme_path.open("a", encoding="utf-8") as f:
        f.write(timestamp + "\n")
    episode_count = sum(1 for line in theme_path.read_text(encoding="utf-8").splitlines() if line.strip())
    theme_content = "The sentences should take place in a world where " + file_stem.replace("_", " ")
    return file_stem, episode_count, theme_content


def send_ntfy_notification(theme_name: str, episode: int):
    import os as _os
    topic = _os.environ.get("NTFY_TOPIC")
    if not topic:
        app.logger.warning("NTFY_TOPIC not set, skipping notification")
        return
    message = f"Theme: {theme_name.replace('_', ' ')}\nEpisode: {episode}"
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        method="POST",
    )
    req.add_header("Title", "Homework Hero: New custom episode")
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as exc:
        app.logger.warning("Failed to send ntfy notification: %s", exc)

def _extract_presentation_metadata(raw_payload):
    """Merge top-level header/footer fields into presentation_metadata."""
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
    return presentation_metadata

def build_worksheet_id_from_params(source_dataset, theme, model, reading_level, section, seed):
    request_dict = {
        "source_dataset": source_dataset,
        "theme": theme,
        "model": model,
        "reading_level": {"system": "fp", "level": reading_level},
        "section": section,
        "seed": seed,
    }
    try:
        return build_worksheet_id(request_dict)
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

def load_sections_for_dataset(source_dataset, datasets_dir=None):
    from Libraries.datasets import load_dataset
    data = load_dataset(source_dataset, datasets_dir=datasets_dir)
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

def build_view_config():
    """Global reference config plus the logged-in user's content (if any).

    get_app_config() stays lru_cached and global-only; user data must never
    land in that cache."""
    config = dict(get_app_config())
    user = current_user()
    if user is not None:
        config["user_themes"] = list_user_themes(user["id"])
        config["user_datasets"] = list_user_datasets(user["id"])
    return config

@app.route('/')
def landing():
    return render_template('landing.html', config=build_view_config())

@app.route('/worksheets')
def worksheets():
    return render_template('generator.html', config=build_view_config(), worksheet_params=None)

@app.route('/worksheet')
def worksheet():
    worksheet_id = request.args.get('id')
    if not worksheet_id:
        return redirect(url_for('worksheets'))

    try:
        decoded = decode_worksheet_id(worksheet_id)
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

    # Enrich episodes with worksheet IDs and view URLs
    for ep in episodes_list:
        ep["worksheet_id"] = build_worksheet_id_from_params(
            source_dataset=params["source_dataset"],
            theme=params["theme"],
            model=params["model"],
            reading_level=params["reading_level"],
            section=params["section"],
            seed=ep["episode"],
        )
        ep["url"] = f"/worksheet?id={ep['worksheet_id']}" if ep["worksheet_id"] else None

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
        "mode": "global",
        "worksheet_id": worksheet_id,
        "pdf_filename": pdf_filename,
        "pdf_url": f"/worksheet_pdf?id={worksheet_id}",
        "params": params,
        "episodes": episodes_list,
        "episode_exists": current_idx is not None,
        "prev_worksheet_id": prev_worksheet_id,
        "next_worksheet_id": next_worksheet_id,
        "prev_url": f"/worksheet?id={prev_worksheet_id}" if prev_worksheet_id else None,
        "next_url": f"/worksheet?id={next_worksheet_id}" if next_worksheet_id else None,
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

    try:
        decoded = decode_worksheet_id(worksheet_id)
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

    try:
        for field, value in (
            ("source_dataset", source_dataset),
            ("theme", theme),
            ("reading_level", reading_level),
            ("model", model),
            ("section", section),
        ):
            validate_key_component(value, field)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    # --- Custom theme branch: bypass Phase 2 entirely ---
    app_config = get_app_config()
    theme_entry = None
    for t in app_config["themes"]:
        if t["id"] == theme:
            theme_entry = t
            break
    is_custom_request = bool(theme_entry and theme_entry.get("key_name") == "user_specified")

    user = current_user()
    if user is None and (is_user_key(theme) or is_user_key(source_dataset)):
        return jsonify({"error": "Login required."}), 401

    # --- Logged-in user content: per-user pipeline with private caching ---
    if user is not None and (is_custom_request or is_user_key(theme) or is_user_key(source_dataset)):
        theme_key = theme
        if is_custom_request:
            custom_text = raw_payload.get("custom_theme_text", "").strip()
            if not custom_text:
                return jsonify({"error": "Please describe your custom world."}), 400
            try:
                theme_key = make_user_key(save_user_theme(user["id"], custom_text))
            except UserDataError as exc:
                return jsonify({"error": str(exc)}), 400

        presentation_metadata = _extract_presentation_metadata(raw_payload)

        try:
            response_json, seed_used = generate_user_worksheet(
                user_id=user["id"],
                source_dataset=source_dataset,
                theme=theme_key,
                reading_level=reading_level,
                model=model,
                section=section,
                presentation_metadata=presentation_metadata or None,
            )
        except UserPipelineError as exc:
            return jsonify({"error": str(exc)}), 404 if exc.not_found else 400

        try:
            pdf_bytes = run_phase5_with_json(response_json)
        except ValueError as exc:
            return jsonify({"error": f"Failed to build PDF: {exc}"}), 500

        my_worksheet_url = "/my/worksheet?" + urlencode(
            {
                "source_dataset": source_dataset,
                "theme": theme_key,
                "reading_level": reading_level,
                "section": section,
                "model": model,
                "episode": seed_used,
            }
        )
        filename = build_pdf_filename(
            source_dataset=source_dataset,
            theme=theme_key,
            section=section,
            episode=seed_used,
        )
        resp = Response(pdf_bytes, mimetype="application/pdf")
        resp.headers["Content-Disposition"] = f'inline; filename="{filename}"'
        resp.headers["X-My-Worksheet-Url"] = my_worksheet_url
        return resp

    # --- Anonymous custom theme: unchanged legacy behavior (uncached) ---
    if is_custom_request:
        custom_text = raw_payload.get("custom_theme_text", "").strip()
        if not custom_text:
            return jsonify({"error": "Please describe your custom world."}), 400

        theme_file_stem, episode_count, theme_content = record_user_theme_episode(custom_text)

        # Build payload for Phase 3 with the custom theme file stem
        custom_payload = {
            "source_dataset": source_dataset,
            "theme": theme_file_stem,
            "reading_level": {"system": "fp", "level": reading_level},
            "model": model,
            "section": section,
            "seed": episode_count,
            "worksheet_id": None,
        }

        try:
            phase3_output = run_phase3_with_json(json.dumps(custom_payload, ensure_ascii=False))
        except (SystemExit, DatasetError) as exc:
            return jsonify({"error": str(exc)}), 400

        try:
            phase4_output = run_phase4_with_json(phase3_output, theme_content=theme_content)
        except SystemExit as exc:
            return jsonify({"error": str(exc)}), 500

        phase4_data = json.loads(phase4_output)

        # Add presentation_metadata with interpolated variables
        presentation_metadata = _extract_presentation_metadata(raw_payload)

        if presentation_metadata:
            dataset_entry = lookup_source_dataset(source_dataset)
            dataset_title = (dataset_entry or {}).get("title", "")
            dataset_abbr = (dataset_entry or {}).get("title_abbr", "")

            presentation_variables = {
                "section": section,
                "reading_system": "fp",
                "reading_level": reading_level,
                "model": model,
                "episode": episode_count,
                "worksheet_id": "",
                "source": dataset_title,
                "source_abbr": dataset_abbr,
                "theme": "Create Your Own Theme",
                "theme_abbr": "Custom",
            }

            phase4_data["presentation_metadata"] = interpolate_presentation_metadata(
                presentation_metadata, presentation_variables
            )

        # Set worksheet_id to None so Phase 5 falls back to base URL for QR
        phase4_data["worksheet_id"] = None

        try:
            pdf_bytes = run_phase5_with_json(json.dumps(phase4_data, ensure_ascii=False))
        except ValueError as exc:
            return jsonify({"error": f"Failed to build PDF: {exc}"}), 500

        send_ntfy_notification(theme_file_stem, episode_count)

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

    presentation_metadata = _extract_presentation_metadata(raw_payload)

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
        validate_key_component(source_dataset, "source_dataset")
        if is_user_key(source_dataset):
            user = current_user()
            if user is None:
                return jsonify({"error": "Not found."}), 404
            sections = load_sections_for_dataset(
                strip_user_key(source_dataset),
                datasets_dir=get_user_source_datasets_dir(user["id"]),
            )
        else:
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
        for field, value in (
            ("source_dataset", source_dataset),
            ("theme", theme),
            ("reading_level", reading_level),
            ("model", model),
            ("section", section),
        ):
            validate_key_component(value, field)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
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
    try:
        for field in ("source_dataset", "theme", "reading_level", "model", "section", "episode"):
            if field not in payload:
                raise ValueError(f"Missing required field: {field}.")
            validate_key_component(payload[field], field)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
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

MY_WORKSHEET_PARAM_FIELDS = ("source_dataset", "theme", "reading_level", "section", "model")

def _validated_my_params(args):
    """Extract and validate the /my/* identifying params from request args.
    Raises ValueError on missing/unsafe values."""
    params = {}
    for field in MY_WORKSHEET_PARAM_FIELDS:
        value = args.get(field)
        if not value:
            raise ValueError(f"Missing required field: {field}.")
        params[field] = validate_key_component(value, field)
    return params

def _my_worksheet_urls(params, episode):
    query = dict(params)
    query["episode"] = episode
    encoded = urlencode(query)
    return "/my/worksheet?" + encoded, "/my/worksheet_pdf?" + encoded

def _resolve_my_theme_entry(user_id, theme):
    """A synthetic theme entry (css_class/ui_title) for user-owned themes,
    falling back to the global entry or the default theme."""
    app_config = get_app_config()
    if is_user_key(theme):
        try:
            record = get_user_theme(user_id, strip_user_key(theme))
        except UserDataError:
            record = None
        title = (record or {}).get("title") or theme
        return {
            "css_class": "",
            "ui_title": title,
            "ui_subtitle": "Your custom theme",
            "title_abbr": "Custom",
        }
    for t in app_config["themes"]:
        if t["id"] == theme:
            return t
    return app_config["themes"][0] if app_config["themes"] else None

def _my_episode_entries(user, params):
    reading_level_segment = build_reading_level_segment(params["reading_level"])
    episodes_list = list_user_episodes(
        user["id"],
        params["source_dataset"],
        reading_level_segment,
        params["section"],
        params["theme"],
        params["model"],
    )
    for ep in episodes_list:
        ep["url"], ep["pdf_url"] = _my_worksheet_urls(params, ep["episode"])
        ep["worksheet_id"] = None
    return episodes_list

@app.route('/my/worksheets')
@login_required
def my_worksheets():
    user = current_user()
    themes = list_user_themes(user["id"])
    theme_titles = {t["id"]: t["title"] for t in themes}
    for t in get_app_config()["themes"]:
        theme_titles.setdefault(t["id"], t["title"])
    dataset_titles = {d["id"]: d["title"] for d in get_app_config()["data_sources"]}

    groups = []
    for group in list_user_worksheet_groups(user["id"]):
        params = {
            "source_dataset": group["source_dataset"],
            "theme": group["theme"],
            "reading_level": group["reading_level"],
            "section": group["section"],
            "model": group["model"],
        }
        episodes = [
            {"episode": ep, "url": _my_worksheet_urls(params, ep)[0]}
            for ep in group["episodes"]
        ]
        groups.append(
            {
                "theme_title": theme_titles.get(group["theme"], group["theme"]),
                "dataset_title": dataset_titles.get(
                    group["source_dataset"],
                    group["source_dataset"].replace("u--", "", 1).replace("_", " "),
                ),
                "reading_level": group["reading_level"],
                "section": group["section"],
                "model": group["model"],
                "episodes": episodes,
            }
        )
    return render_template(
        'my_worksheets.html',
        config=build_view_config(),
        themes=themes,
        groups=groups,
        datasets=list_user_datasets(user["id"]),
        dataset_error=request.args.get('dataset_error'),
        dataset_saved=request.args.get('dataset_saved'),
    )

@app.route('/my/worksheet')
@login_required
def my_worksheet():
    user = current_user()
    try:
        params = _validated_my_params(request.args)
        episode = int(request.args.get("episode", ""))
    except ValueError:
        return redirect(url_for('my_worksheets'))

    episodes_list = _my_episode_entries(user, params)
    current_idx = None
    for i, ep in enumerate(episodes_list):
        if ep["episode"] == episode:
            current_idx = i
            break
    if current_idx is None:
        return redirect(url_for('my_worksheets'))

    prev_url = episodes_list[current_idx - 1]["url"] if current_idx > 0 else None
    next_url = None
    next_is_generate = False
    next_generate_episode = None
    if current_idx < len(episodes_list) - 1:
        next_url = episodes_list[current_idx + 1]["url"]
    else:
        next_is_generate = True
        next_generate_episode = episodes_list[-1]["episode"] + 1

    view_url, pdf_url = _my_worksheet_urls(params, episode)
    theme_entry = _resolve_my_theme_entry(user["id"], params["theme"])
    pdf_filename = build_pdf_filename(
        source_dataset=params["source_dataset"],
        theme=params["theme"],
        section=params["section"],
        episode=episode,
    )

    viewer = {
        "mode": "user",
        "worksheet_id": None,
        "pdf_filename": pdf_filename,
        "pdf_url": pdf_url,
        "params": {
            "source_dataset": params["source_dataset"],
            "theme": params["theme"],
            "model": params["model"],
            "reading_level": params["reading_level"],
            "section": params["section"],
            "seed": episode,
        },
        "episodes": episodes_list,
        "episode_exists": True,
        "prev_worksheet_id": None,
        "next_worksheet_id": None,
        "prev_url": prev_url,
        "next_url": next_url,
        "next_is_generate": next_is_generate,
        "next_generate_episode": next_generate_episode,
        "theme_entry": theme_entry,
    }
    return render_template('viewer.html', viewer=viewer, config=build_view_config())

@app.route('/my/worksheet_pdf')
@login_required
def my_worksheet_pdf():
    user = current_user()
    try:
        params = _validated_my_params(request.args)
        episode = int(request.args.get("episode", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    presentation_metadata = {
        "header": "{theme} - Section {section}",
        "footer": "Page {current_page} of {total_pages}",
        "answer_key_footer": "Fountas & Pinnell Level {reading_level}",
    }
    try:
        response_json, _ = generate_user_worksheet(
            user_id=user["id"],
            source_dataset=params["source_dataset"],
            theme=params["theme"],
            reading_level=params["reading_level"],
            model=params["model"],
            section=params["section"],
            seed=episode,
            presentation_metadata=presentation_metadata,
            allow_generate=False,
        )
    except UserPipelineError as exc:
        return jsonify({"error": str(exc)}), 404 if exc.not_found else 400

    try:
        pdf_bytes = run_phase5_with_json(response_json)
    except ValueError as exc:
        return jsonify({"error": f"Failed to build PDF: {exc}"}), 500

    filename = build_pdf_filename(
        source_dataset=params["source_dataset"],
        theme=params["theme"],
        section=params["section"],
        episode=episode,
    )
    resp = Response(pdf_bytes, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    return resp

@app.route('/my/episodes')
@login_required
def my_episodes():
    user = current_user()
    try:
        params = _validated_my_params(request.args)
    except ValueError:
        return jsonify({"episodes": []})
    return jsonify({"episodes": _my_episode_entries(user, params)})

@app.route('/my/themes')
@login_required
def my_themes():
    user = current_user()
    return jsonify({"themes": list_user_themes(user["id"])})

@app.route('/my/datasets', methods=['GET', 'POST'])
@login_required
def my_datasets():
    user = current_user()
    if request.method == 'GET':
        return jsonify({"datasets": list_user_datasets(user["id"])})

    uploaded = request.files.get("dataset_file")
    if uploaded is not None:
        # Classic form upload from /my/worksheets: CSRF-checked, redirects back.
        if not validate_csrf():
            return jsonify({"error": "Invalid form token."}), 400
        title = (request.form.get("title") or "").strip() or Path(uploaded.filename or "").stem
        overwrite = request.form.get("overwrite") == "1"
        try:
            data = json.load(uploaded.stream)
        except (ValueError, UnicodeDecodeError):
            return redirect(url_for('my_worksheets', dataset_error="Could not parse the file as JSON."))
        try:
            save_user_dataset(user["id"], title, data, overwrite=overwrite)
        except UserDataError as exc:
            return redirect(url_for('my_worksheets', dataset_error=str(exc)))
        return redirect(url_for('my_worksheets', dataset_saved="1"))

    # JSON API upload
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "Expected a JSON body or a dataset_file upload."}), 400
    title = (payload.get("title") or "").strip()
    dataset = payload.get("dataset") if "dataset" in payload else payload
    overwrite = bool(payload.get("overwrite"))
    try:
        stem = save_user_dataset(user["id"], title, dataset, overwrite=overwrite)
    except UserDataError as exc:
        status = 409 if getattr(exc, "already_exists", False) else 400
        return jsonify({"error": str(exc)}), status
    return jsonify({"id": make_user_key(stem), "stem": stem}), 201

@app.route('/about')
def about():
    return render_template('about.html', config=get_app_config())

if __name__ == '__main__':
    app.run(debug=True)
