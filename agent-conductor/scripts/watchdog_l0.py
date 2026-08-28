"""Detection-only L0 watchdog for one experiment run."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .status import cmd_run_set, mutate_status, render
except ImportError:
    from status import cmd_run_set, mutate_status, render

DEFAULT_ERROR_PATTERN = r"Traceback|\bERROR\b|\bOOM\b|out of memory|No space left"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_seconds(path: Path, now: float) -> float | None:
    try:
        return max(0.0, now - path.stat().st_mtime)
    except FileNotFoundError:
        return None


def _tail(path: Path, limit: int = 65536) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            return handle.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _gpu_has_pid(pid: int) -> tuple[bool, str | None]:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return False, "nvidia-smi unavailable"
    result = subprocess.run(
        [executable, "--query-compute-apps=pid", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        return False, f"nvidia-smi exit {result.returncode}"
    pids = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return str(pid) in pids, None


def evaluate(args: argparse.Namespace, *, now: float | None = None) -> dict[str, Any]:
    checked_at = time.time() if now is None else now
    anomalies: list[str] = []
    observations: dict[str, Any] = {}

    heartbeat = Path(args.heartbeat_file)
    heartbeat_age = _age_seconds(heartbeat, checked_at)
    observations["heartbeat_age_seconds"] = heartbeat_age
    observations["heartbeat_file"] = str(heartbeat)
    if heartbeat_age is None:
        anomalies.append(f"heartbeat missing: {heartbeat.name}")
    elif heartbeat_age > args.stale_after:
        anomalies.append(f"heartbeat stale: {heartbeat_age:.1f}s > {args.stale_after:.1f}s")

    if args.pid is not None:
        alive = _pid_alive(args.pid)
        observations["pid_alive"] = alive
        if not alive:
            anomalies.append(f"process not running: pid {args.pid}")

    if args.log_file:
        log_path = Path(args.log_file)
        log_tail = _tail(log_path)
        match = re.search(args.error_pattern, log_tail, flags=re.IGNORECASE)
        observations["log_file"] = str(log_path)
        if not log_path.exists():
            anomalies.append(f"log missing: {log_path.name}")
        elif match:
            anomalies.append(f"log anomaly: {match.group(0)}")

    if args.disk_path:
        disk_path = Path(args.disk_path)
        try:
            free_gb = shutil.disk_usage(disk_path).free / (1024 ** 3)
            observations["disk_free_gb"] = round(free_gb, 3)
            if free_gb < args.min_free_gb:
                anomalies.append(f"disk low: {free_gb:.2f} GiB < {args.min_free_gb:.2f} GiB")
        except FileNotFoundError:
            observations["disk_path"] = str(disk_path)
            anomalies.append(f"disk path missing: {disk_path.name}")

    if args.checkpoint:
        checkpoint = Path(args.checkpoint)
        checkpoint_age = _age_seconds(checkpoint, checked_at)
        observations["checkpoint_age_seconds"] = checkpoint_age
        observations["checkpoint"] = str(checkpoint)
        if checkpoint_age is None:
            anomalies.append(f"checkpoint missing: {checkpoint.name}")
        elif checkpoint_age > args.checkpoint_stale_after:
            anomalies.append(
                f"checkpoint stale: {checkpoint_age:.1f}s > {args.checkpoint_stale_after:.1f}s"
            )

    if args.require_gpu:
        if args.pid is None:
            anomalies.append("--require-gpu needs --pid")
        else:
            on_gpu, gpu_error = _gpu_has_pid(args.pid)
            observations["pid_on_gpu"] = on_gpu
            if gpu_error:
                anomalies.append(gpu_error)
            elif not on_gpu:
                anomalies.append(f"pid {args.pid} is not visible on a GPU")

    return {
        "healthy": not anomalies,
        "checked_at": _now_iso(),
        "anomalies": anomalies,
        "observations": observations,
    }


def _append_incident(root: Path, run_id: str, summary: str) -> Path:
    path = root / "runs" / run_id / "incidents.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{_now_iso()} | L0 | script | anomaly | wake root | {summary}\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
    return path


def command_check(args: argparse.Namespace) -> int:
    if args.max_recoverable_loss <= 0:
        print("error: --max-recoverable-loss must be positive", file=sys.stderr)
        return 2
    if args.stale_after <= 0 or args.checkpoint_stale_after <= 0:
        print("error: stale thresholds must be positive", file=sys.stderr)
        return 2

    result = evaluate(args)
    summary = "; ".join(result["anomalies"]) if result["anomalies"] else "healthy"

    def update(data: dict[str, Any]) -> None:
        cmd_run_set(
            data,
            run_id=args.run_id,
            tier="L0",
            incident=summary if result["anomalies"] else None,
            watchdog_state="anomaly" if result["anomalies"] else "healthy",
            watchdog_checked_at=result["checked_at"],
            max_recoverable_loss=args.max_recoverable_loss,
        )

    mutate_status(args.session, update, root=args.root)
    render(args.session, root=args.root)
    if result["anomalies"]:
        result["incidents_log"] = str(_append_incident(Path(args.root).resolve(), args.run_id, summary))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["healthy"] else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detection-only L0 experiment watchdog")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="Run one watchdog observation pass")
    check.add_argument("--root", required=True)
    check.add_argument("--session", required=True)
    check.add_argument("--run-id", required=True)
    check.add_argument("--heartbeat-file", required=True)
    check.add_argument("--max-recoverable-loss", required=True, type=float)
    check.add_argument("--stale-after", type=float, default=180)
    check.add_argument("--pid", type=int)
    check.add_argument("--log-file")
    check.add_argument("--error-pattern", default=DEFAULT_ERROR_PATTERN)
    check.add_argument("--disk-path")
    check.add_argument("--min-free-gb", type=float, default=1.0)
    check.add_argument("--checkpoint")
    check.add_argument("--checkpoint-stale-after", type=float, default=180)
    check.add_argument("--require-gpu", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return command_check(args)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
