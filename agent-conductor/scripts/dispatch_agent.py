"""Probe, launch, and wait for detached Claude/Codex worker processes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_ENV = {"PATH", "HOME", "USER", "SHELL", "LANG", "TERM", "TMPDIR"}
COMMON_ENV = {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE"}
CLAUDE_READ_TOOLS = ["Read", "Glob", "Grep"]
CLAUDE_WRITE_TOOLS = ["Read", "Glob", "Grep", "Edit", "Write"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _capture(argv: list[str], *, timeout: float = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def _probe_provider(provider: str) -> dict[str, Any]:
    executable = shutil.which(provider)
    if executable is None:
        return {"available": False, "version": None, "executable": None, "verified_flags": []}

    try:
        version_result = _capture([executable, "--version"])
        version = (version_result.stdout or version_result.stderr).strip() or None
        help_argv = [executable, "--help"] if provider == "claude" else [executable, "exec", "--help"]
        help_result = _capture(help_argv)
        help_text = (help_result.stdout or "") + (help_result.stderr or "")
        resume_result = _capture(
            [executable, "--help"]
            if provider == "claude"
            else [executable, "exec", "resume", "--help"]
        )
        resume_text = (resume_result.stdout or "") + (resume_result.stderr or "")
    except subprocess.TimeoutExpired as exc:
        return {
            "available": True,
            "version": None,
            "executable": executable,
            "verified_flags": [],
            "probe_error": f"timeout: {exc}",
        }
    candidates = {
        "model": "--model" in help_text or "-m," in help_text,
        "effort": "--effort" in help_text,
        "output_format": "--output-format" in help_text,
        "permission_mode": "--permission-mode" in help_text,
        "allowed_tools": "--allowedTools" in help_text,
        "disallowed_tools": "--disallowedTools" in help_text,
        "restricted": "--restricted" in help_text,
        "tools": "--tools" in help_text,
        "sandbox": "--sandbox" in help_text or "-s," in help_text,
        "json": "--json" in help_text,
        "output_last_message": "--output-last-message" in help_text or "-o," in help_text,
        "cwd": "--cd" in help_text or "-C," in help_text,
        "resume": (
            "--resume" in help_text
            if provider == "claude"
            else all(flag in resume_text for flag in ("--model", "--sandbox", "--cd", "--json"))
        ),
        "stdin_prompt": "stdin" in help_text.lower(),
    }
    return {
        "available": True,
        "version": version,
        "executable": executable,
        "verified_flags": sorted(key for key, value in candidates.items() if value),
        "help_exit_code": help_result.returncode,
        "resume_help_exit_code": resume_result.returncode,
    }


def command_probe(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": 1,
        "generated_at": _now_iso(),
        "providers": {
            "claude": _probe_provider("claude"),
            "codex": _probe_provider("codex"),
        },
    }
    destination = workdir / "cli_probe.json"
    _atomic_json(destination, result)
    print(json.dumps({"probe": str(destination), **result}, ensure_ascii=False))
    return 0


def _safe_env(provider: str, extra: list[str]) -> dict[str, str]:
    names = BASE_ENV | COMMON_ENV
    prefixes = ["LC_"]
    prefixes.extend(["ANTHROPIC_", "CLAUDE_"] if provider == "claude" else ["OPENAI_", "CODEX_"])
    env = {
        key: value
        for key, value in os.environ.items()
        if key in names or any(key.startswith(prefix) for prefix in prefixes)
    }
    for spec in extra:
        if spec.endswith("*"):
            prefix = spec[:-1]
            env.update({key: value for key, value in os.environ.items() if key.startswith(prefix)})
        elif spec in os.environ:
            env[spec] = os.environ[spec]
        else:
            raise ValueError(f"required environment variable is missing: {spec}")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _absolute_existing(path_text: str, *, directory: bool = False) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        raise ValueError(f"path must be absolute: {path}")
    if directory and not path.is_dir():
        raise ValueError(f"directory does not exist: {path}")
    if not directory and not path.is_file():
        raise ValueError(f"file does not exist: {path}")
    return path


def _artifact_mode(role: str, override: str | None) -> str:
    if override:
        return override
    return "worker-file" if role == "implementer" else "final-message"


def command_launch(args: argparse.Namespace) -> int:
    cwd = _absolute_existing(args.cwd, directory=True)
    brief = _absolute_existing(args.brief)
    artifact = Path(args.artifact)
    dispatch_dir = Path(args.dispatch_dir)
    if not artifact.is_absolute() or not dispatch_dir.is_absolute():
        raise ValueError("--artifact and --dispatch-dir must be absolute")
    if args.timeout < 0:
        raise ValueError("--timeout must be zero or positive")
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    if (dispatch_dir / "meta.json").exists() or (dispatch_dir / "result.json").exists():
        raise ValueError(f"dispatch directory has already been used: {dispatch_dir}")
    artifact.parent.mkdir(parents=True, exist_ok=True)

    prompt = brief.read_text(encoding="utf-8")
    request = {
        "schema_version": 1,
        "provider": args.provider,
        "provider_executable": args.provider_executable,
        "role": args.role,
        "model": args.model,
        "effort": args.effort,
        "cwd": str(cwd),
        "brief": str(brief),
        "artifact": str(artifact),
        "artifact_mode": _artifact_mode(args.role, args.artifact_mode),
        "timeout": args.timeout,
        "resume_session_id": args.resume_session_id,
        "allow_tools": args.allow_tool,
        "env_allow": args.env_allow,
        "input_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    }
    _atomic_json(dispatch_dir / "request.json", request)

    env = _safe_env(args.provider if args.provider != "fake" else "codex", args.env_allow)
    supervisor = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "_run", "--dispatch-dir", str(dispatch_dir)],
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    meta = {
        "schema_version": 1,
        "provider": args.provider,
        "role": args.role,
        "model": args.model,
        "started": _now_iso(),
        "supervisor_pid": supervisor.pid,
        "supervisor_pgid": supervisor.pid,
        "artifact": str(artifact),
        "stderr": str(dispatch_dir / "stderr.txt"),
        "input_hash": request["input_hash"],
    }
    _atomic_json(dispatch_dir / "meta.json", meta)
    print(json.dumps(meta, ensure_ascii=False))
    return 0


def _require_flags(probe: dict[str, Any], required: set[str]) -> None:
    available = set(probe.get("verified_flags", []))
    missing = sorted(required - available)
    if missing:
        raise RuntimeError(f"provider CLI lacks required verified capabilities: {', '.join(missing)}")


def _provider_command(request: dict[str, Any], probe: dict[str, Any]) -> tuple[list[str], bool]:
    provider = request["provider"]
    executable = request.get("provider_executable") or probe.get("executable")
    if not executable:
        raise RuntimeError(f"{provider} executable is unavailable")

    if provider == "fake":
        path = Path(executable)
        return ([sys.executable, str(path)] if path.suffix == ".py" else [str(path)]), True

    resume = request.get("resume_session_id")
    if provider == "claude":
        required = {
            "model",
            "permission_mode",
            "stdin_prompt",
            "allowed_tools",
            "disallowed_tools",
            "restricted",
            "tools",
        }
        _require_flags(probe, required)
        command = [executable, "--model", request["model"]]
        if "effort" in probe["verified_flags"] and request.get("effort"):
            command += ["--effort", request["effort"]]
        if resume:
            _require_flags(probe, {"resume"})
            command += ["--resume", resume]
        command += ["--restricted", "--permission-mode", "dontAsk"]
        json_output = "output_format" in probe["verified_flags"]
        if json_output:
            command += ["--output-format", "json"]
        allowed = list(
            CLAUDE_WRITE_TOOLS if request["role"] == "implementer" else CLAUDE_READ_TOOLS
        )
        allowed += request.get("allow_tools", [])
        bash_allowed = any(tool == "Bash" or tool.startswith("Bash(") for tool in allowed)
        available = list(
            CLAUDE_WRITE_TOOLS if request["role"] == "implementer" else CLAUDE_READ_TOOLS
        )
        if bash_allowed:
            available.append("Bash")
        command += ["--tools", ",".join(dict.fromkeys(available))]
        command += ["--allowedTools", *dict.fromkeys(allowed)]
        denied = ["NotebookEdit", "MultiEdit"]
        if request["role"] != "implementer":
            denied += ["Edit", "Write"]
        if not bash_allowed:
            denied.append("Bash")
        command += ["--disallowedTools", *denied]
        command += ["-p"]
        return command, json_output

    required = {"model", "sandbox", "json", "cwd", "stdin_prompt"}
    _require_flags(probe, required)
    sandbox = "workspace-write" if request["role"] == "implementer" else "read-only"
    if resume:
        _require_flags(probe, {"resume"})
        command = [executable, "exec", "resume", "-m", request["model"], "-C", request["cwd"]]
        command += ["-s", sandbox, "--json", resume]
    else:
        command = [
            executable,
            "exec",
            "-m",
            request["model"],
            "-C",
            request["cwd"],
            "-s",
            sandbox,
            "--json",
        ]
    if request["artifact_mode"] == "final-message" and "output_last_message" in probe["verified_flags"]:
        command += ["-o", request["artifact"]]
    command += ["-"]
    return command, True


def _parse_final(provider: str, stdout: str) -> tuple[str, str | None]:
    if provider in {"claude", "fake"}:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return stdout, None
        return str(payload.get("result", stdout)), payload.get("session_id")

    final = ""
    session_id: str | None = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            session_id = event.get("thread_id") or event.get("threadId") or event.get("id")
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            final = str(item.get("text", final))
    return final, session_id


def _supervise(dispatch_dir: Path) -> int:
    request = _read_json(dispatch_dir / "request.json")
    started_monotonic = time.monotonic()
    stdout = ""
    stderr = ""
    exit_code = 1
    timed_out = False
    provider_session_id: str | None = None
    error: str | None = None
    try:
        prompt = Path(request["brief"]).read_text(encoding="utf-8")
        probe = (
            {"available": True, "version": "fake", "executable": request["provider_executable"],
             "verified_flags": []}
            if request["provider"] == "fake"
            else _probe_provider(request["provider"])
        )
        _atomic_json(dispatch_dir / "probe.json", probe)
        command, _structured = _provider_command(request, probe)
        meta_path = dispatch_dir / "meta.json"
        deadline = time.monotonic() + 5
        while not meta_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        meta = _read_json(meta_path)
        meta.update({"provider_argv": command, "cli_version": probe.get("version")})
        _atomic_json(meta_path, meta)

        env = _safe_env(
            request["provider"] if request["provider"] != "fake" else "codex",
            request.get("env_allow", []),
        )
        process = subprocess.Popen(
            command,
            cwd=request["cwd"],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        meta.update({"provider_pid": process.pid, "provider_pgid": process.pid})
        _atomic_json(meta_path, meta)
        timeout = request.get("timeout") or None
        try:
            stdout, stderr = process.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        exit_code = process.returncode
        _atomic_text(dispatch_dir / "stdout.txt", stdout)
        _atomic_text(dispatch_dir / "stderr.txt", stderr)

        final, provider_session_id = _parse_final(request["provider"], stdout)
        artifact = Path(request["artifact"])
        if request["artifact_mode"] == "final-message":
            if request["provider"] != "codex" or not artifact.exists():
                _atomic_text(artifact, final)
        artifact_ok = artifact.is_file() and artifact.stat().st_size > 0
        ok = exit_code == 0 and artifact_ok and not timed_out
        if not artifact_ok:
            error = "expected artifact is missing or empty"
        elif timed_out:
            error = "provider exceeded the configured hang guard"
        elif exit_code != 0:
            error = f"provider exited with code {exit_code}"
    except BaseException as exc:
        ok = False
        error = f"{type(exc).__name__}: {exc}"
        stderr = (stderr + "\n" + error).lstrip()
        _atomic_text(dispatch_dir / "stdout.txt", stdout)
        _atomic_text(dispatch_dir / "stderr.txt", stderr)

    result = {
        "schema_version": 1,
        "ok": ok,
        "provider": request["provider"],
        "model": request["model"],
        "role": request["role"],
        "exit_code": exit_code,
        "timed_out": timed_out,
        "provider_session_id": provider_session_id,
        "artifact": request["artifact"],
        "stdout": str(dispatch_dir / "stdout.txt"),
        "stderr": str(dispatch_dir / "stderr.txt"),
        "input_hash": request["input_hash"],
        "duration_seconds": round(time.monotonic() - started_monotonic, 3),
        "finished": _now_iso(),
        "error": error,
    }
    _atomic_json(dispatch_dir / "result.json", result)
    return 0 if ok else 1


def command_wait(args: argparse.Namespace) -> int:
    dispatch_dir = Path(args.dispatch_dir)
    if not dispatch_dir.is_absolute():
        raise ValueError("--dispatch-dir must be absolute")
    result_path = dispatch_dir / "result.json"
    started = time.monotonic()
    while not result_path.exists():
        if args.wait_timeout and time.monotonic() - started >= args.wait_timeout:
            print("wait timed out; worker was not cancelled", file=sys.stderr)
            return 2
        time.sleep(args.poll_interval)
    result = _read_json(result_path)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detached Claude/Codex worker dispatcher")
    sub = parser.add_subparsers(dest="command", required=True)
    probe = sub.add_parser("probe", help="Probe installed provider CLI capabilities")
    probe.add_argument("--workdir", required=True)

    launch = sub.add_parser("launch", help="Launch a detached worker supervisor")
    launch.add_argument("--provider", required=True, choices=["claude", "codex", "fake"])
    launch.add_argument("--provider-executable", help=argparse.SUPPRESS)
    launch.add_argument("--role", required=True)
    launch.add_argument("--model", required=True)
    launch.add_argument("--effort", default="high")
    launch.add_argument("--cwd", required=True)
    launch.add_argument("--brief", required=True)
    launch.add_argument("--artifact", required=True)
    launch.add_argument("--dispatch-dir", required=True)
    launch.add_argument("--artifact-mode", choices=["final-message", "worker-file"])
    launch.add_argument("--timeout", type=float, default=0)
    launch.add_argument("--resume-session-id")
    launch.add_argument("--allow-tool", action="append", default=[])
    launch.add_argument("--env-allow", action="append", default=[])

    wait = sub.add_parser("wait", help="Wait for result.json from a launched worker")
    wait.add_argument("--dispatch-dir", required=True)
    wait.add_argument("--wait-timeout", type=float, default=0)
    wait.add_argument("--poll-interval", type=float, default=0.2)

    internal = sub.add_parser("_run", help=argparse.SUPPRESS)
    internal.add_argument("--dispatch-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "probe":
            return command_probe(args)
        if args.command == "launch":
            return command_launch(args)
        if args.command == "wait":
            return command_wait(args)
        return _supervise(Path(args.dispatch_dir))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
