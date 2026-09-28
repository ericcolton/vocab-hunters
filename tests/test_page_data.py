"""Server-to-JS data handoff: the generator/viewer pages embed their data as a
<script type="application/json"> block read by static/js/*.js."""

import json
import re


def _page_data(html: str, element_id: str) -> dict:
    match = re.search(
        rf'<script type="application/json" id="{element_id}">(.*?)</script>', html, re.S
    )
    assert match, f"#{element_id} block missing"
    return json.loads(match.group(1))


def test_build_theme_config_merges_global_and_user_themes(app_module):
    config = {
        "themes": [
            {
                "id": 3,
                "key_name": "no_theme",
                "ui_title": "Tom & Jerry",
                "ui_subtitle": "Sub",
                "css_class": "theme-plain",
            }
        ],
        "user_themes": [{"id": "u--castle", "key_name": "u--castle", "title": "Castle"}],
    }
    assert app_module.build_theme_config(config) == {
        "3": {
            "key_name": "no_theme",
            "title": "Tom & Jerry",
            "subtitle": "Sub",
            "css_class": "theme-plain",
        },
        "u--castle": {
            "key_name": "u--castle",
            "title": "Castle",
            "subtitle": "Your custom theme",
            "css_class": "theme-custom",
        },
    }


def test_build_theme_config_without_user_themes(app_module):
    assert app_module.build_theme_config({"themes": []}) == {}


def test_generator_page_embeds_theme_config(client):
    html = client.get("/worksheets").get_data(as_text=True)
    data = _page_data(html, "generator-data")
    assert data["worksheetParams"] is None
    titles = {entry["key_name"]: entry["title"] for entry in data["themeConfig"].values()}
    assert titles["no_theme"] == "Everyday Vocab Practice"
    assert "js/generator.js" in html
    assert "onchange=" not in html


def test_viewer_page_embeds_viewer_data(app_module, client):
    worksheet_id = app_module.build_worksheet_id_from_params(
        source_dataset="testds",
        theme="space",
        model="test-model",
        reading_level="C",
        section=1,
        seed=1,
    )
    assert worksheet_id
    html = client.get(f"/worksheet?id={worksheet_id}").get_data(as_text=True)
    data = _page_data(html, "viewer-data")
    assert data["viewer"]["params"]["section"] == 1
    assert data["config"]["data_sources"]
    assert "js/viewer.js" in html
    assert "onclick=" not in html
