
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# --- CONFIGURATION / PLUGINS ---
# In a real app, these could be loaded from a database or JSON file.
app_config = {
    "data_sources": [
        {"id": "ww3000_bk3", "name": "Wordly Wise 3000 Book 3"},
        {"id": "ww3000_bk4", "name": "Wordly Wise 3000 Book 4 (Coming Soon)"}, # Example of adding another
    ],
    "themes": [
        {
            "id": "kpop",
            "name": "KPop Demon Hunters",
            "css_class": "", # Default theme has no extra class
            "ui_title": "KPop Vocab Hunters",
            "ui_subtitle": "Hunt Vocabulary. Defeat Demons."
        },
        {
            "id": "wof",
            "name": "Wings of Fire",
            "css_class": "theme-wof",
            "ui_title": "Dragon Vocab Scrolls",
            "ui_subtitle": "Fly High. Burn Bright. Learn Words."
        }
    ],
    "models": [
        {"id": "gpt-5-mini", "name": "gpt-5-mini"},
        {"id": "gpt-4o", "name": "gpt-4o (High Cost)"},
    ],
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

if __name__ == '__main__':
    app.run(debug=True)
