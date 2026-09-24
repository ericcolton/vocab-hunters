#!/usr/bin/env python3
import sqlite3

CSRF = "test-csrf-token"


def _set_csrf(client):
    with client.session_transaction() as sess:
        sess["csrf"] = CSRF


def _register(client, email, password="password123"):
    _set_csrf(client)
    return client.post(
        "/register",
        data={"email": email, "password": password, "csrf_token": CSRF},
    )


def _login(client, email, password):
    _set_csrf(client)
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": CSRF},
    )


def _logout(client):
    _set_csrf(client)
    return client.post("/logout", data={"csrf_token": CSRF})


def test_register_login_logout_flow(client):
    resp = _register(client, "flow@example.com")
    assert resp.status_code == 302
    assert "/worksheets" in resp.headers["Location"]

    resp = client.get("/account")
    assert resp.status_code == 200
    assert b"flow@example.com" in resp.data

    resp = _logout(client)
    assert resp.status_code == 302

    resp = client.get("/account")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]

    resp = _login(client, "flow@example.com", "password123")
    assert resp.status_code == 302
    assert client.get("/account").status_code == 200


def test_register_duplicate_email(client):
    assert _register(client, "dupe@example.com").status_code == 302
    _logout(client)
    resp = _register(client, "dupe@example.com")
    assert resp.status_code == 400
    assert b"already registered" in resp.data


def test_register_duplicate_email_case_insensitive(client):
    assert _register(client, "case@example.com").status_code == 302
    _logout(client)
    resp = _register(client, "CASE@Example.COM")
    assert resp.status_code == 400
    assert b"already registered" in resp.data


def test_register_rejects_bad_input(client):
    assert _register(client, "not-an-email").status_code == 400
    assert _register(client, "ok@example.com", password="short").status_code == 400


def test_login_failures_use_one_generic_message(client):
    _register(client, "real@example.com")
    _logout(client)

    wrong_password = _login(client, "real@example.com", "wrong-password")
    unknown_email = _login(client, "nobody@example.com", "wrong-password")
    assert wrong_password.status_code == 401
    assert unknown_email.status_code == 401
    assert b"Invalid email or password." in wrong_password.data
    assert b"Invalid email or password." in unknown_email.data


def test_lockout_after_repeated_failures(client, app_module):
    _register(client, "locked@example.com")
    _logout(client)

    for _ in range(10):
        assert _login(client, "locked@example.com", "wrong-password").status_code == 401

    # Locked now: even the correct password is refused, same generic message.
    resp = _login(client, "locked@example.com", "password123")
    assert resp.status_code == 401
    assert b"Invalid email or password." in resp.data


def test_login_required_json_vs_html(client):
    resp = client.get("/account", headers={"Accept": "application/json"})
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "Login required."

    resp = client.get("/account", headers={"Accept": "text/html"})
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_csrf_required_on_forms(client):
    resp = client.post(
        "/login", data={"email": "x@example.com", "password": "password123"}
    )
    assert resp.status_code == 400

    _register(client, "csrf@example.com")
    resp = client.post("/logout", data={})
    assert resp.status_code == 400
    # Session survived the rejected logout.
    assert client.get("/account").status_code == 200


def test_session_invalidated_when_generation_bumps(client, app_module):
    _register(client, "stale@example.com")
    assert client.get("/account").status_code == 200

    from Libraries.reference_data import get_sqlite_db_path

    with app_module.app.app_context():
        db_path = get_sqlite_db_path()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE users SET session_generation = session_generation + 1 WHERE email = ?",
        ("stale@example.com",),
    )
    conn.commit()
    conn.close()

    resp = client.get("/account")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_open_redirect_guard_on_next(client):
    _register(client, "redirect@example.com")
    _logout(client)
    _set_csrf(client)
    resp = client.post(
        "/login",
        data={
            "email": "redirect@example.com",
            "password": "password123",
            "next": "//evil.example.com/phish",
            "csrf_token": CSRF,
        },
    )
    assert resp.status_code == 302
    assert "evil.example.com" not in resp.headers["Location"]


def test_path_component_validation(client):
    resp = client.get(
        "/episodes",
        query_string={
            "source_dataset": "../../etc",
            "theme": "space",
            "reading_level": "C",
            "model": "test-model",
            "section": "1",
        },
    )
    assert resp.status_code == 400

    resp = client.post(
        "/generate",
        json={
            "source_dataset": "../../etc",
            "theme": "space",
            "reading_level": "C",
            "model": "test-model",
            "section": 1,
        },
    )
    assert resp.status_code == 400


def test_registration_rate_limit(client, app_module, monkeypatch):
    import auth

    monkeypatch.setattr(auth, "MAX_REGISTRATIONS_PER_IP_PER_HOUR", 3)

    for i in range(3):
        resp = _register(client, f"ratelimit{i}@example.com")
        assert resp.status_code == 302, f"attempt {i} should succeed"

    resp = _register(client, "ratelimit_over@example.com")
    assert resp.status_code == 429
    assert b"Too many registration attempts" in resp.data


def test_anonymous_pages_still_render(client):
    for path in ("/", "/worksheets", "/about"):
        resp = client.get(path)
        assert resp.status_code == 200, path
    assert b"Log in" in client.get("/about").data
