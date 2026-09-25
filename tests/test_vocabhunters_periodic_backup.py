#!/usr/bin/env python3
"""Coverage for Scripts/vocabhunters_periodic_backup.py's pure path/command
logic and its subprocess orchestration, via an injected runner in place of a
live SSH connection."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "Scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from vocabhunters_periodic_backup import (  # noqa: E402
    BackupError,
    build_scp_download_command,
    build_ssh_command,
    cleanup_remote_tmp,
    download_content_dir,
    download_sqlite_backup,
    perform_backup,
    resolve_backup_dir,
    run_remote_sqlite_backup,
)


def _ok(returncode=0, stderr=""):
    return SimpleNamespace(returncode=returncode, stderr=stderr)


class FakeRunner:
    """Records every call and returns success unless told to fail on a given call index."""

    def __init__(self, fail_at=None, fail_stderr="boom"):
        self.calls = []
        self.fail_at = fail_at
        self.fail_stderr = fail_stderr

    def __call__(self, cmd, **kwargs):
        index = len(self.calls)
        self.calls.append(cmd)
        if self.fail_at is not None and index == self.fail_at:
            return _ok(returncode=1, stderr=self.fail_stderr)
        return _ok()


def test_resolve_backup_dir_no_collision(tmp_path):
    assert resolve_backup_dir(tmp_path, "20260924") == tmp_path / "20260924"


def test_resolve_backup_dir_appends_incrementing_suffix(tmp_path):
    (tmp_path / "20260924").mkdir()
    (tmp_path / "20260924.1").mkdir()
    assert resolve_backup_dir(tmp_path, "20260924") == tmp_path / "20260924.2"


def test_build_ssh_command():
    assert build_ssh_command("user@host", "echo hi") == ["ssh", "user@host", "echo hi"]


def test_build_scp_download_command_file():
    cmd = build_scp_download_command("user@host", "/tmp/x.sqlite3", Path("/local/x.sqlite3"))
    assert cmd == ["scp", "user@host:/tmp/x.sqlite3", "/local/x.sqlite3"]


def test_build_scp_download_command_recursive():
    cmd = build_scp_download_command("user@host", "/remote/content", Path("/local/db"), recursive=True)
    assert cmd == ["scp", "-r", "user@host:/remote/content", "/local/db"]


def test_run_remote_sqlite_backup_quotes_paths_with_spaces():
    runner = FakeRunner()
    run_remote_sqlite_backup("user@host", "/var/data/a b.sqlite3", "/tmp/tmp file.sqlite3", runner=runner)
    cmd = runner.calls[0]
    assert cmd[0] == "ssh"
    assert cmd[1] == "user@host"
    assert "'/var/data/a b.sqlite3'" in cmd[2]
    assert "/tmp/tmp file.sqlite3" in cmd[2]


def test_run_remote_sqlite_backup_raises_on_nonzero_exit():
    runner = FakeRunner(fail_at=0, fail_stderr="no such table")
    with pytest.raises(BackupError, match="no such table"):
        run_remote_sqlite_backup("user@host", "/db.sqlite3", "/tmp/x.sqlite3", runner=runner)


def test_download_sqlite_backup_and_content_dir_build_expected_commands():
    runner = FakeRunner()
    download_sqlite_backup("user@host", "/tmp/x.sqlite3", Path("/local/db/x.sqlite3"), runner=runner)
    download_content_dir("user@host", "/remote/content", Path("/local/db"), runner=runner)
    assert runner.calls[0] == ["scp", "user@host:/tmp/x.sqlite3", "/local/db/x.sqlite3"]
    assert runner.calls[1] == ["scp", "-r", "user@host:/remote/content", "/local/db"]


def test_cleanup_remote_tmp_builds_rm_command():
    runner = FakeRunner()
    cleanup_remote_tmp("user@host", "/tmp/x.sqlite3", runner=runner)
    cmd = runner.calls[0]
    assert cmd[0] == "ssh"
    assert "rm -f" in cmd[2]
    assert "/tmp/x.sqlite3" in cmd[2]


def test_perform_backup_mirrors_remote_structure_and_calls_in_order(tmp_path):
    runner = FakeRunner()
    dated_dir = perform_backup(
        host="user@host",
        remote_sqlite_path="/var/data/vocab_hunters/vocabhunters.db/vocabhunters.sqlite3",
        remote_content_dir="/var/data/vocab_hunters/vocabhunters.db/content",
        backup_root=tmp_path,
        date_str="20260924",
        runner=runner,
    )

    assert dated_dir == tmp_path / "20260924"
    local_db_dir = dated_dir / "vocabhunters.db"
    assert local_db_dir.is_dir()

    # order: sqlite3 .backup (ssh), scp sqlite file, scp content dir, cleanup (ssh)
    assert len(runner.calls) == 4
    assert runner.calls[0][0] == "ssh" and ".backup" in runner.calls[0][2]
    assert runner.calls[1][0] == "scp"
    assert str(local_db_dir / "vocabhunters.sqlite3") in runner.calls[1]
    assert runner.calls[2] == ["scp", "-r", "user@host:/var/data/vocab_hunters/vocabhunters.db/content", str(local_db_dir)]
    assert runner.calls[3][0] == "ssh" and "rm -f" in runner.calls[3][2]


def test_perform_backup_still_cleans_up_remote_tmp_when_scp_fails(tmp_path):
    # fail_at=1 is the second call: the sqlite-file scp download.
    runner = FakeRunner(fail_at=1, fail_stderr="connection reset")
    with pytest.raises(BackupError, match="connection reset"):
        perform_backup(
            host="user@host",
            remote_sqlite_path="/var/data/vocab_hunters/vocabhunters.db/vocabhunters.sqlite3",
            remote_content_dir="/var/data/vocab_hunters/vocabhunters.db/content",
            backup_root=tmp_path,
            date_str="20260924",
            runner=runner,
        )
    # ssh backup succeeded (call 0), scp failed (call 1), cleanup still attempted (call 2)
    assert len(runner.calls) == 3
    assert runner.calls[2][0] == "ssh" and "rm -f" in runner.calls[2][2]


def test_perform_backup_skips_cleanup_when_remote_backup_itself_fails(tmp_path):
    runner = FakeRunner(fail_at=0, fail_stderr="sqlite3: command not found")
    with pytest.raises(BackupError, match="command not found"):
        perform_backup(
            host="user@host",
            remote_sqlite_path="/var/data/vocab_hunters/vocabhunters.db/vocabhunters.sqlite3",
            remote_content_dir="/var/data/vocab_hunters/vocabhunters.db/content",
            backup_root=tmp_path,
            date_str="20260924",
            runner=runner,
        )
    assert len(runner.calls) == 1
