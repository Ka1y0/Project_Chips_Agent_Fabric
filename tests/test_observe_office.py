import os
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "observe_office.py"


def test_existing_trial_directory_is_untouched(tmp_path):
    marker = tmp_path / "operator-state"
    marker.write_text("preserve")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--data-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0 and "new, unused data directory" in result.stderr
    assert list(tmp_path.iterdir()) == [marker] and marker.read_text() == "preserve"


def test_launcher_observes_isolated_noop_and_shuts_down(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = str(sock.getsockname()[1])
    directory = tmp_path / "trial"
    other = tmp_path / "operator"
    env = dict(
        os.environ,
        PROJECT_SUPERVISOR_DATA_DIR=str(other),
        PROJECT_SUPERVISOR_HOST="0.0.0.0",
        PROJECT_SUPERVISOR_READ_ONLY_API="false",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--data-dir", str(directory), "--port", port, "--check"],
        capture_output=True,
        text=True,
        timeout=50,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert f"Read-only Office: http://127.0.0.1:{port}/office/" in result.stdout
    assert not other.exists()
    with sqlite3.connect(directory / "supervisor.db") as connection:
        assert connection.execute("SELECT state FROM autonomous_hosts").fetchall() == [("stopped",)]
        assert connection.execute(
            "SELECT DISTINCT result,local_model_calls,local_tokens FROM heartbeat_ticks"
        ).fetchall() == [("NOOP", 0, 0)]
        assert connection.execute("SELECT COUNT(*) FROM autonomous_goals").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM worker_runs").fetchone() == (0,)
