"""Hermetic tests for detached dispatch and the detection-only watchdog."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from scripts.dispatch_agent import _provider_command
from scripts.status import cmd_init, load

SKILL_ROOT = Path(__file__).resolve().parent.parent
DISPATCH = SKILL_ROOT / "scripts" / "dispatch_agent.py"
WATCHDOG = SKILL_ROOT / "scripts" / "watchdog_l0.py"
SESSION = "feedface"


def _launch_fake(
    tmp_path: Path,
    program: str,
    *,
    role: str = "reviewer",
    timeout: float = 0,
    env_allow: tuple[str, ...] = (),
) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str], Path, Path]:
    fake = tmp_path / "fake_provider.py"
    fake.write_text(program, encoding="utf-8")
    brief = tmp_path / "brief.md"
    brief.write_text("prompt with spaces\nand a second line\n", encoding="utf-8")
    artifact = tmp_path / "artifact.md"
    dispatch_dir = tmp_path / "dispatch"
    command = [
        sys.executable,
        str(DISPATCH),
        "launch",
        "--provider",
        "fake",
        "--provider-executable",
        str(fake),
        "--role",
        role,
        "--model",
        "fake-model",
        "--cwd",
        str(tmp_path),
        "--brief",
        str(brief),
        "--artifact",
        str(artifact),
        "--dispatch-dir",
        str(dispatch_dir),
        "--timeout",
        str(timeout),
    ]
    for name in env_allow:
        command.extend(["--env-allow", name])
    launched = subprocess.run(command, capture_output=True, text=True, check=False)
    waited = subprocess.run(
        [
            sys.executable,
            str(DISPATCH),
            "wait",
            "--dispatch-dir",
            str(dispatch_dir),
            "--wait-timeout",
            "10",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return launched, waited, artifact, dispatch_dir


def test_probe_writes_capability_json(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PATH"] = ""
    result = subprocess.run(
        [sys.executable, str(DISPATCH), "probe", "--workdir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / "cli_probe.json").read_text(encoding="utf-8"))
    assert set(payload["providers"]) == {"claude", "codex"}
    for provider in payload["providers"].values():
        assert {"available", "version", "verified_flags"} <= provider.keys()


def test_unknown_codex_role_defaults_to_read_only() -> None:
    request = {
        "provider": "codex",
        "provider_executable": "/bin/codex",
        "role": "future-role",
        "model": "test",
        "cwd": "/tmp",
        "artifact": "/tmp/result.md",
        "artifact_mode": "final-message",
        "resume_session_id": None,
    }
    probe = {
        "executable": "/bin/codex",
        "verified_flags": [
            "model", "sandbox", "json", "cwd", "stdin_prompt", "output_last_message"
        ],
    }
    command, _structured = _provider_command(request, probe)
    sandbox_index = command.index("-s")
    assert command[sandbox_index + 1] == "read-only"


def test_claude_read_role_ignores_settings_and_denies_writes() -> None:
    request = {
        "provider": "claude",
        "provider_executable": "/bin/claude",
        "role": "reviewer",
        "model": "test",
        "effort": "high",
        "cwd": "/tmp",
        "artifact": "/tmp/result.md",
        "artifact_mode": "final-message",
        "resume_session_id": None,
        "allow_tools": ["Bash(pytest:*)"],
    }
    probe = {
        "executable": "/bin/claude",
        "verified_flags": [
            "model",
            "effort",
            "permission_mode",
            "stdin_prompt",
            "allowed_tools",
            "disallowed_tools",
            "restricted",
            "tools",
            "output_format",
        ],
    }
    command, _structured = _provider_command(request, probe)
    assert "--restricted" in command
    assert command[command.index("--tools") + 1] == "Read,Glob,Grep,Bash"
    denied = command[command.index("--disallowedTools") + 1 :]
    assert {"Edit", "Write", "NotebookEdit", "MultiEdit"} <= set(denied)
    assert "Bash" not in denied


def test_final_message_uses_stdin_and_filters_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VISIBLE_FOR_TEST", "kept")
    monkeypatch.setenv("SECRET_FOR_TEST", "drop-me")
    program = """
import json
import os
import sys

prompt = sys.stdin.read()
result = {
    "prompt": prompt,
    "visible": os.environ.get("VISIBLE_FOR_TEST"),
    "secret": os.environ.get("SECRET_FOR_TEST"),
}
print(json.dumps({"result": json.dumps(result), "session_id": "fake-session"}))
"""
    launched, waited, artifact, dispatch_dir = _launch_fake(
        tmp_path, program, env_allow=("VISIBLE_FOR_TEST",)
    )
    assert launched.returncode == 0, launched.stderr
    assert waited.returncode == 0, waited.stderr
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["prompt"] == "prompt with spaces\nand a second line\n"
    assert payload["visible"] == "kept"
    assert payload["secret"] is None
    result = json.loads((dispatch_dir / "result.json").read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["provider_session_id"] == "fake-session"


def test_implementer_must_write_its_artifact(tmp_path: Path) -> None:
    program = """
import json
import sys

sys.stdin.read()
print(json.dumps({"result": "final message only", "session_id": "fake-session"}))
"""
    launched, waited, artifact, dispatch_dir = _launch_fake(
        tmp_path, program, role="implementer"
    )
    assert launched.returncode == 0
    assert waited.returncode == 1
    assert not artifact.exists()
    result = json.loads((dispatch_dir / "result.json").read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert "missing or empty" in result["error"]


def test_timeout_kills_entire_provider_process_group(tmp_path: Path) -> None:
    child_marker = tmp_path / "child-survived"
    program = f"""
import subprocess
import sys
import time

sys.stdin.read()
subprocess.Popen([
    sys.executable,
    "-c",
    "import pathlib,time; time.sleep(2); pathlib.Path({str(child_marker)!r}).touch()",
])
time.sleep(30)
"""
    launched, waited, _artifact, dispatch_dir = _launch_fake(tmp_path, program, timeout=0.5)
    assert launched.returncode == 0
    assert waited.returncode == 1
    result = json.loads((dispatch_dir / "result.json").read_text(encoding="utf-8"))
    assert result["timed_out"] is True
    time.sleep(2.2)
    assert not child_marker.exists()


def test_watchdog_missing_loss_budget_writes_nothing(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    heartbeat.touch()
    root = tmp_path / "outputs"
    result = subprocess.run(
        [
            sys.executable,
            str(WATCHDOG),
            "check",
            "--root",
            str(root),
            "--session",
            SESSION,
            "--run-id",
            "run-1",
            "--heartbeat-file",
            str(heartbeat),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert not root.exists()


def test_watchdog_healthy_and_stale_paths(tmp_path: Path) -> None:
    root = tmp_path / "outputs"
    cmd_init(SESSION, root=str(root))
    heartbeat = tmp_path / "heartbeat"
    heartbeat.touch()
    base = [
        sys.executable,
        str(WATCHDOG),
        "check",
        "--root",
        str(root),
        "--session",
        SESSION,
        "--run-id",
        "run-1",
        "--heartbeat-file",
        str(heartbeat),
        "--max-recoverable-loss",
        "3600",
        "--stale-after",
        "1",
    ]
    healthy = subprocess.run(base, capture_output=True, text=True, check=False)
    assert healthy.returncode == 0, healthy.stderr
    assert load(SESSION, root=str(root))["runs"][0]["watchdog_state"] == "healthy"

    old = time.time() - 10
    os.utime(heartbeat, (old, old))
    stale = subprocess.run(base, capture_output=True, text=True, check=False)
    assert stale.returncode == 1
    run = load(SESSION, root=str(root))["runs"][0]
    assert run["watchdog_state"] == "anomaly"
    incidents = root / "runs" / "run-1" / "incidents.log"
    assert "heartbeat stale" in incidents.read_text(encoding="utf-8")


def test_watchdog_redacts_missing_absolute_path_from_status_and_dashboard(
    tmp_path: Path,
) -> None:
    root = tmp_path / "outputs"
    cmd_init(SESSION, root=str(root))
    heartbeat = tmp_path / "private" / "heartbeat.secret"
    result = subprocess.run(
        [
            sys.executable,
            str(WATCHDOG),
            "check",
            "--root",
            str(root),
            "--session",
            SESSION,
            "--run-id",
            "run-missing",
            "--heartbeat-file",
            str(heartbeat),
            "--max-recoverable-loss",
            "60",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    run = load(SESSION, root=str(root))["runs"][0]
    assert run["incident"] == "heartbeat missing: heartbeat.secret"
    assert str(tmp_path) not in run["incident"]
    dashboard = root / "orchestrator" / SESSION / "dashboard.html"
    assert dashboard.exists()
    assert str(tmp_path) not in dashboard.read_text(encoding="utf-8")


def test_watchdog_log_detection_never_runs_remediation(tmp_path: Path) -> None:
    root = tmp_path / "outputs"
    cmd_init(SESSION, root=str(root))
    heartbeat = tmp_path / "heartbeat"
    heartbeat.touch()
    log = tmp_path / "run.log"
    log.write_text("step 1\nTraceback: broken\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(WATCHDOG),
            "check",
            "--root",
            str(root),
            "--session",
            SESSION,
            "--run-id",
            "run-2",
            "--heartbeat-file",
            str(heartbeat),
            "--max-recoverable-loss",
            "60",
            "--log-file",
            str(log),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["anomalies"] == ["log anomaly: Traceback"]
