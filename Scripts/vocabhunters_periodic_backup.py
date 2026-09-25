#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Periodic backup of the Render-hosted vocab-hunters SQLite database and content tree.

Runs LOCALLY (not on Render). SSHes into the Render host, takes a consistent
snapshot of the live SQLite database via the `sqlite3` CLI's `.backup` command
(safe against a concurrent writer, unlike copying the file directly), then scp's
that snapshot plus the content/ directory back to a dated local backup directory.
The remote temp snapshot is removed afterward.

The local backup mirrors the remote directory structure exactly: the sqlite
file and content/ dir are siblings under a directory named after the remote
db directory (e.g. "vocabhunters.db"), matching
/var/data/vocab_hunters/vocabhunters.db/{vocabhunters.sqlite3,content}.

PREREQUISITE: the SSH key for the Render host is passphrase-protected. This
script never prompts for or handles that passphrase -- it relies entirely on
ssh-agent having the key already unlocked. One-time setup:

    ssh-add --apple-use-keychain ~/.ssh/<your-render-key>

and in ~/.ssh/config:

    Host ssh.virginia.render.com
        UseKeychain yes
        AddKeysToAgent yes
        IdentityFile ~/.ssh/<your-render-key>

With that in place, macOS Keychain unlocks the key into ssh-agent on login and
every `ssh`/`scp` call this script makes picks it up transparently.
"""

import argparse
import shlex
import subprocess
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any, List, Optional

DEFAULT_HOST = "srv-d5omcac9c44c73faefm0@ssh.virginia.render.com"
DEFAULT_REMOTE_SQLITE_PATH = "/var/data/vocab_hunters/vocabhunters.db/vocabhunters.sqlite3"
DEFAULT_REMOTE_CONTENT_DIR = "/var/data/vocab_hunters/vocabhunters.db/content"
DEFAULT_BACKUP_ROOT = "~/OneDrive/Development/homework-hero/backups"


class BackupError(Exception):
    """Raised when a remote command or file transfer fails."""


def resolve_backup_dir(root: Path, date_str: str) -> Path:
    """Return root/date_str, or root/date_str.1, .2, ... -- the first path
    that does not already exist."""
    candidate = root / date_str
    suffix = 0
    while candidate.exists():
        suffix += 1
        candidate = root / f"{date_str}.{suffix}"
    return candidate


def make_remote_tmp_path(date_str: str) -> str:
    return f"/tmp/vocabhunters_backup_{date_str}_{uuid.uuid4().hex[:8]}.sqlite3"


def build_ssh_command(host: str, remote_command: str) -> List[str]:
    return ["ssh", host, remote_command]


def build_scp_download_command(host: str, remote_path: str, local_path: Path, recursive: bool = False) -> List[str]:
    cmd = ["scp"]
    if recursive:
        cmd.append("-r")
    cmd.append(f"{host}:{remote_path}")
    cmd.append(str(local_path))
    return cmd


def _run(cmd: List[str], runner: Any, description: str) -> None:
    result = runner(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise BackupError(f"{description} failed (exit {result.returncode}): {result.stderr.strip()}")


def run_remote_sqlite_backup(host: str, remote_sqlite_path: str, remote_tmp_path: str, runner: Any = subprocess.run) -> None:
    backup_dot_command = f".backup '{remote_tmp_path}'"
    remote_command = f"sqlite3 {shlex.quote(remote_sqlite_path)} {shlex.quote(backup_dot_command)}"
    cmd = build_ssh_command(host, remote_command)
    _run(cmd, runner, "remote sqlite3 .backup")


def download_sqlite_backup(host: str, remote_tmp_path: str, local_sqlite_path: Path, runner: Any = subprocess.run) -> None:
    cmd = build_scp_download_command(host, remote_tmp_path, local_sqlite_path)
    _run(cmd, runner, "scp of sqlite backup")


def download_content_dir(host: str, remote_content_dir: str, local_db_dir: Path, runner: Any = subprocess.run) -> None:
    cmd = build_scp_download_command(host, remote_content_dir, local_db_dir, recursive=True)
    _run(cmd, runner, "scp of content dir")


def cleanup_remote_tmp(host: str, remote_tmp_path: str, runner: Any = subprocess.run) -> None:
    remote_command = f"rm -f {shlex.quote(remote_tmp_path)}"
    cmd = build_ssh_command(host, remote_command)
    _run(cmd, runner, "remote temp file cleanup")


def perform_backup(
    host: str,
    remote_sqlite_path: str,
    remote_content_dir: str,
    backup_root: Path,
    date_str: str,
    runner: Any = subprocess.run,
) -> Path:
    dated_dir = resolve_backup_dir(backup_root, date_str)
    db_dir_name = Path(remote_sqlite_path).parent.name
    sqlite_filename = Path(remote_sqlite_path).name
    local_db_dir = dated_dir / db_dir_name
    local_db_dir.mkdir(parents=True)

    remote_tmp_path = make_remote_tmp_path(date_str)
    backup_taken = False
    try:
        run_remote_sqlite_backup(host, remote_sqlite_path, remote_tmp_path, runner=runner)
        backup_taken = True
        download_sqlite_backup(host, remote_tmp_path, local_db_dir / sqlite_filename, runner=runner)
        download_content_dir(host, remote_content_dir, local_db_dir, runner=runner)
    finally:
        if backup_taken:
            cleanup_remote_tmp(host, remote_tmp_path, runner=runner)

    return dated_dir


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=DEFAULT_HOST, help="SSH host (user@host) for the Render service")
    parser.add_argument("--remote-sqlite-path", default=DEFAULT_REMOTE_SQLITE_PATH, help="Path to the live sqlite3 db on the remote host")
    parser.add_argument("--remote-content-dir", default=DEFAULT_REMOTE_CONTENT_DIR, help="Path to the content/ dir on the remote host")
    parser.add_argument("--backup-root", default=DEFAULT_BACKUP_ROOT, help="Local directory under which dated backups are created")
    parser.add_argument("--date", default=None, help="Override the YYYYMMDD backup date (default: today)")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    date_str = args.date or date.today().strftime("%Y%m%d")
    backup_root = Path(args.backup_root).expanduser()

    try:
        dated_dir = perform_backup(
            host=args.host,
            remote_sqlite_path=args.remote_sqlite_path,
            remote_content_dir=args.remote_content_dir,
            backup_root=backup_root,
            date_str=date_str,
        )
    except BackupError as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1

    print(f"Backup complete: {dated_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
