#!/usr/bin/env python3
"""Authentication blueprint: SQLite-backed identity, signed-cookie sessions.

Identity data (users, lockout state) lives in {VOCAB_HUNTERS_DB_PATH}/auth.sqlite3.
Content (themes, worksheets, datasets) stays on the filesystem; see
Libraries/user_data.py.

CSRF strategy: HTML form POSTs carry a per-session token (validated with
hmac.compare_digest). JSON fetch POSTs (/generate, /fetch_episode, /my/* APIs)
deliberately skip tokens: SESSION_COOKIE_SAMESITE=Lax stops cross-site POSTs
from carrying the session cookie, and a cross-origin page cannot send
Content-Type: application/json without a CORS preflight that Flask never
approves.
"""

import hmac
import logging
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

from flask import (
    Blueprint,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from Libraries.reference_data import get_database_path

auth_bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_MAX_LENGTH = 254
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 200
MAX_FAILED_LOGINS = 10
LOCKOUT_MINUTES = 15
LOGIN_ERROR = "Invalid email or password."

# Werkzeug's default (scrypt) is unavailable on Python builds whose OpenSSL
# lacks scrypt (the project targets 3.9). PBKDF2-SHA256 at 600k iterations is
# the OWASP-recommended fallback; hashes are self-describing, so this can be
# upgraded later without invalidating existing passwords.
PASSWORD_HASH_METHOD = "pbkdf2:sha256:600000"


def hash_password(password: str) -> str:
    return generate_password_hash(password, method=PASSWORD_HASH_METHOD)


# Verified against on every login with an unknown email so response timing
# does not reveal whether an account exists.
_DUMMY_HASH = hash_password("hh-dummy-not-a-password")

_MIGRATIONS = [
    # migration 1
    """
    CREATE TABLE users (
        id                 INTEGER PRIMARY KEY,
        email              TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash      TEXT NOT NULL,
        session_generation INTEGER NOT NULL DEFAULT 1,
        failed_login_count INTEGER NOT NULL DEFAULT 0,
        locked_until       TEXT,
        created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    );
    """,
]


class AuthError(Exception):
    pass


def get_logger() -> logging.Logger:
    try:
        return current_app.logger
    except RuntimeError:
        return logging.getLogger(__name__)


def get_auth_db_path() -> Path:
    return get_database_path() / "auth.sqlite3"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_auth_db() -> None:
    """Create/upgrade the auth schema. Safe to call from every gunicorn worker:
    BEGIN IMMEDIATE serializes writers, and user_version is rechecked inside
    the transaction."""
    path = get_auth_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for target, script in enumerate(_MIGRATIONS, start=1):
            if version < target:
                conn.executescript(script)
                conn.execute(f"PRAGMA user_version = {target}")
                get_logger().debug("Applied auth migration %d", target)
        conn.commit()
    finally:
        conn.close()


def get_auth_db() -> sqlite3.Connection:
    if "auth_db" not in g:
        g.auth_db = _connect(get_auth_db_path())
    return g.auth_db


@auth_bp.teardown_app_request
def _close_auth_db(exc: Optional[BaseException]) -> None:
    conn = g.pop("auth_db", None)
    if conn is not None:
        conn.close()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def current_user() -> Optional[sqlite3.Row]:
    """Return the logged-in user's row, or None. Clears stale sessions."""
    if "current_user" in g:
        return g.current_user
    user: Optional[sqlite3.Row] = None
    user_id = session.get("user_id")
    if user_id is not None:
        row = get_auth_db().execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None or row["session_generation"] != session.get("sgen"):
            session.clear()
        else:
            user = row
    g.current_user = user
    return user


def _wants_json() -> bool:
    if request.is_json:
        return True
    accept = request.accept_mimetypes
    return accept["application/json"] > accept["text/html"]


def _safe_next_url(target: Optional[str]) -> Optional[str]:
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if current_user() is None:
            if _wants_json():
                return jsonify({"error": "Login required."}), 401
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


def get_csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def validate_csrf() -> bool:
    expected = session.get("csrf")
    provided = request.form.get("csrf_token", "")
    return bool(expected) and hmac.compare_digest(expected, provided)


@auth_bp.app_context_processor
def inject_auth() -> dict:
    return {"current_user": current_user(), "csrf_token": get_csrf_token}


def _log_in_user(row: sqlite3.Row) -> None:
    session.clear()
    session["user_id"] = row["id"]
    session["sgen"] = row["session_generation"]
    session.permanent = True


def _validate_registration(email: str, password: str) -> Optional[str]:
    if not email or len(email) > EMAIL_MAX_LENGTH or not EMAIL_RE.match(email):
        return "Please enter a valid email address."
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Password must be at most {PASSWORD_MAX_LENGTH} characters."
    return None


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html", error=None, email="")

    if not validate_csrf():
        return render_template("register.html", error="Invalid form token. Please try again.", email=""), 400

    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    error = _validate_registration(email, password)
    if error:
        return render_template("register.html", error=error, email=email), 400

    db = get_auth_db()
    try:
        cursor = db.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (email, hash_password(password)),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return render_template(
            "register.html", error="That email is already registered.", email=email
        ), 400

    row = db.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
    _log_in_user(row)
    get_logger().debug("Registered user id=%d", row["id"])
    return redirect(url_for("worksheets"))


def _is_locked(row: sqlite3.Row) -> bool:
    locked_until = row["locked_until"]
    if not locked_until:
        return False
    try:
        until = datetime.strptime(locked_until, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return _utcnow() < until


def _record_login_failure(db: sqlite3.Connection, row: sqlite3.Row) -> None:
    new_count = row["failed_login_count"] + 1
    if new_count >= MAX_FAILED_LOGINS:
        db.execute(
            "UPDATE users SET failed_login_count = 0, locked_until = ? WHERE id = ?",
            (_iso(_utcnow() + timedelta(minutes=LOCKOUT_MINUTES)), row["id"]),
        )
        get_logger().debug("Locked user id=%d for %d minutes", row["id"], LOCKOUT_MINUTES)
    else:
        db.execute(
            "UPDATE users SET failed_login_count = ? WHERE id = ?",
            (new_count, row["id"]),
        )
    db.commit()


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html", error=None, email="", next=request.args.get("next", ""))

    if not validate_csrf():
        return render_template("login.html", error="Invalid form token. Please try again.", email="", next=""), 400

    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    next_url = _safe_next_url(request.form.get("next"))

    db = get_auth_db()
    row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    if row is None:
        # Unknown email: burn a hash check so timing matches the real path.
        check_password_hash(_DUMMY_HASH, password)
        return render_template("login.html", error=LOGIN_ERROR, email=email, next=next_url or ""), 401

    if _is_locked(row):
        check_password_hash(_DUMMY_HASH, password)
        return render_template("login.html", error=LOGIN_ERROR, email=email, next=next_url or ""), 401

    if not check_password_hash(row["password_hash"], password):
        _record_login_failure(db, row)
        return render_template("login.html", error=LOGIN_ERROR, email=email, next=next_url or ""), 401

    db.execute(
        "UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE id = ?",
        (row["id"],),
    )
    db.commit()
    _log_in_user(row)
    get_logger().debug("Logged in user id=%d", row["id"])
    return redirect(next_url or url_for("worksheets"))


@auth_bp.route("/logout", methods=["POST"])
def logout():
    if not validate_csrf():
        return jsonify({"error": "Invalid form token."}), 400
    session.clear()
    return redirect(url_for("landing"))


@auth_bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    user = current_user()
    if request.method == "GET":
        return render_template("account.html", user=user, error=None, message=None)

    if not validate_csrf():
        return render_template("account.html", user=user, error="Invalid form token. Please try again.", message=None), 400

    old_password = request.form.get("old_password") or ""
    new_password = request.form.get("new_password") or ""

    if not check_password_hash(user["password_hash"], old_password):
        return render_template("account.html", user=user, error="Current password is incorrect.", message=None), 400
    if len(new_password) < PASSWORD_MIN_LENGTH or len(new_password) > PASSWORD_MAX_LENGTH:
        return render_template(
            "account.html",
            user=user,
            error=f"New password must be {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters.",
            message=None,
        ), 400

    db = get_auth_db()
    new_generation = user["session_generation"] + 1
    db.execute(
        "UPDATE users SET password_hash = ?, session_generation = ? WHERE id = ?",
        (hash_password(new_password), new_generation, user["id"]),
    )
    db.commit()
    # Keep this session alive; all others are now stale.
    session["sgen"] = new_generation
    g.pop("current_user", None)
    get_logger().debug("Password changed for user id=%d", user["id"])
    return render_template("account.html", user=current_user(), error=None, message="Password updated.")
