"""Orchestrator status manager with locked, atomic JSON updates."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator


def _resolve_root(root_override: str | None) -> Path:
    if root_override:
        return Path(root_override).resolve()
    env = os.environ.get("ORCHESTRATOR_ROOT", "")
    if env:
        return Path(env).resolve()
    return Path.cwd() / "outputs"


def status_dir(session8: str, *, root: str | None = None) -> Path:
    return _resolve_root(root) / "orchestrator" / session8


def status_path(session8: str, *, root: str | None = None) -> Path:
    return status_dir(session8, root=root) / "status.json"


def dashboard_path(session8: str, *, root: str | None = None) -> Path:
    return status_dir(session8, root=root) / "dashboard.html"


def decisions_path(session8: str, *, root: str | None = None) -> Path:
    return status_dir(session8, root=root) / "decisions.log"


def lock_path(session8: str, *, root: str | None = None) -> Path:
    return status_dir(session8, root=root) / "status.lock"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_status(session8: str) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "session": session8,
        "created_at": _now_iso(),
        "steps": {str(i): "pending" for i in range(1, 6)},
        "agents": [],
        "runs": [],
        "questions": [],
        "decisions": [],
        "cost": {},
    }


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _append_jsonl(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


@contextmanager
def status_lock(session8: str, *, root: str | None = None) -> Iterator[None]:
    """Serialize writers using a stable inode separate from status.json."""

    path = lock_path(session8, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load(session8: str, *, root: str | None = None) -> dict[str, Any]:
    with status_path(session8, root=root).open(encoding="utf-8") as handle:
        return json.load(handle)


def save(data: dict[str, Any], *, root: str | None = None) -> Path:
    path = status_path(data["session"], root=root)
    _atomic_write_json(path, data)
    return path


def mutate_status(
    session8: str,
    mutator: Callable[[dict[str, Any]], Any],
    *,
    root: str | None = None,
    initialize: bool = False,
    audit_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one complete read-modify-write transaction under the session lock."""

    with status_lock(session8, root=root):
        data = _empty_status(session8) if initialize else load(session8, root=root)
        mutator(data)
        save(data, root=root)
        if audit_entry is not None:
            _append_jsonl(decisions_path(session8, root=root), audit_entry)
        return data


def render(session8: str, *, root: str | None = None) -> Path:
    src = status_path(session8, root=root)
    dst = dashboard_path(session8, root=root)
    render_script = Path(__file__).resolve().parent / "render_dashboard.py"
    subprocess.run([sys.executable, str(render_script), str(src), str(dst)], check=True)
    return dst


def cmd_init(session8: str, *, root: str | None = None) -> dict[str, Any]:
    return mutate_status(session8, lambda _data: None, root=root, initialize=True)


def cmd_step_set(data: dict[str, Any], step: str, state: str) -> dict[str, Any]:
    if step not in data["steps"]:
        raise ValueError(f"step must be 1-5, got {step}")
    if state not in ("pending", "active", "done"):
        raise ValueError(f"state must be pending/active/done, got {state}")
    data["steps"][step] = state
    return data


def cmd_agent_add(
    data: dict[str, Any],
    *,
    agent_id: str,
    role: str,
    model: str,
    vendor: str,
    state: str,
    unblocks: str,
    provider_session_id: str | None = None,
    artifact: str | None = None,
    stderr: str | None = None,
    exit_code: int | None = None,
    finished: str | None = None,
    independence_waived: bool = False,
    waiver_reason: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": agent_id,
        "role": role,
        "model": model,
        "vendor": vendor,
        "state": state,
        "unblocks": unblocks,
        "started": _now_iso(),
        "independence_waived": bool(independence_waived),
    }
    optional = {
        "provider_session_id": provider_session_id,
        "artifact": artifact,
        "stderr": stderr,
        "exit_code": exit_code,
        "finished": finished,
        "waiver_reason": waiver_reason,
    }
    entry.update({key: value for key, value in optional.items() if value is not None})
    data["agents"].append(entry)
    return data


def cmd_agent_update(data: dict[str, Any], *, agent_id: str, **fields: Any) -> dict[str, Any]:
    for agent in data["agents"]:
        if agent["id"] == agent_id:
            for key, value in fields.items():
                if value is not None:
                    agent[key] = value
            return data
    raise ValueError(f"agent '{agent_id}' not found")


def cmd_run_set(data: dict[str, Any], *, run_id: str, **fields: Any) -> dict[str, Any]:
    for run in data["runs"]:
        if run["id"] == run_id:
            for key, value in fields.items():
                if value is not None:
                    run[key] = value
            return data
    entry: dict[str, Any] = {"id": run_id}
    entry.update({key: value for key, value in fields.items() if value is not None})
    data["runs"].append(entry)
    return data


def cmd_question_add(data: dict[str, Any], *, question_id: str, text: str) -> dict[str, Any]:
    data["questions"].append({"id": question_id, "text": text, "asked_at": _now_iso()})
    return data


def cmd_question_answer(data: dict[str, Any], *, question_id: str, text: str) -> dict[str, Any]:
    for question in data["questions"]:
        if question["id"] == question_id:
            question["answered_at"] = _now_iso()
            question["answer"] = text
            return data
    raise ValueError(f"question '{question_id}' not found")


def cmd_decision_add(
    data: dict[str, Any],
    *,
    by: str,
    text: str,
    independence_waived: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "timestamp": _now_iso(),
        "by": by,
        "text": text,
        "independence_waived": independence_waived,
    }
    if reason is not None:
        entry["reason"] = reason
    data["decisions"].insert(0, entry)
    data["decisions"] = data["decisions"][:10]
    return data


def cmd_cost_add(data: dict[str, Any], *, model: str, calls: int, tokens: int) -> dict[str, Any]:
    cost = data["cost"]
    if model not in cost:
        cost[model] = {"calls": 0, "tokens": 0}
    cost[model]["calls"] += calls
    cost[model]["tokens"] += tokens
    return data


def _resolve_session(args: argparse.Namespace) -> str:
    if args.session:
        return args.session
    env = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if env:
        return env[:8]
    print("error: --session required (Claude and Codex roots should pass a stable id)", file=sys.stderr)
    raise SystemExit(1)


def _agent_fields(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "role": getattr(args, "role", None),
        "model": getattr(args, "model", None),
        "vendor": getattr(args, "vendor", None),
        "state": getattr(args, "state", None),
        "unblocks": getattr(args, "unblocks", None),
        "provider_session_id": getattr(args, "provider_session_id", None),
        "artifact": getattr(args, "artifact", None),
        "stderr": getattr(args, "stderr", None),
        "exit_code": getattr(args, "exit_code", None),
        "finished": getattr(args, "finished", None),
        "independence_waived": getattr(args, "independence_waived", None),
        "waiver_reason": getattr(args, "waiver_reason", None),
    }


def _add_agent_optional_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider-session-id")
    parser.add_argument("--artifact")
    parser.add_argument("--stderr")
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--finished")
    parser.add_argument("--independence-waived", action="store_true", default=None)
    parser.add_argument("--waiver-reason")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="status.py", description="Orchestrator status manager")
    parser.add_argument("--session", help="Stable orchestrator session id")
    parser.add_argument("--root", help="Root directory (default: ./outputs or ORCHESTRATOR_ROOT)")
    parser.add_argument("--no-render", action="store_true", help="Skip dashboard re-render")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize status.json")

    step = sub.add_parser("step", help="Manage step state").add_subparsers(dest="step_cmd")
    step_set = step.add_parser("set", help="Set step state")
    step_set.add_argument("step_num", help="Step number 1-5")
    step_set.add_argument("step_state", choices=["pending", "active", "done"])

    agent = sub.add_parser("agent", help="Manage agents").add_subparsers(dest="agent_cmd")
    agent_add = agent.add_parser("add", help="Add agent")
    agent_add.add_argument("--id", required=True, dest="agent_id")
    agent_add.add_argument("--role", required=True)
    agent_add.add_argument("--model", required=True)
    agent_add.add_argument("--vendor", required=True, choices=["claude", "codex"])
    agent_add.add_argument("--state", required=True)
    agent_add.add_argument("--unblocks", required=True)
    _add_agent_optional_args(agent_add)
    agent_update = agent.add_parser("update", help="Update agent fields")
    agent_update.add_argument("--id", required=True, dest="agent_id")
    agent_update.add_argument("--role")
    agent_update.add_argument("--model")
    agent_update.add_argument("--vendor", choices=["claude", "codex"])
    agent_update.add_argument("--state")
    agent_update.add_argument("--unblocks")
    _add_agent_optional_args(agent_update)

    run = sub.add_parser("run", help="Manage runs").add_subparsers(dest="run_cmd")
    run_set = run.add_parser("set", help="Set run fields")
    run_set.add_argument("--id", required=True, dest="run_id")
    run_set.add_argument("--machine")
    run_set.add_argument("--tier", choices=["L0", "L1", "L2", "L3"])
    run_set.add_argument("--progress")
    run_set.add_argument("--incident")
    run_set.add_argument("--next-checkpoint")
    run_set.add_argument("--watchdog-state")
    run_set.add_argument("--watchdog-checked-at")
    run_set.add_argument("--max-recoverable-loss", type=float)

    question = sub.add_parser("question", help="Manage questions").add_subparsers(dest="q_cmd")
    q_add = question.add_parser("add", help="Add question")
    q_add.add_argument("--id", required=True, dest="q_id")
    q_add.add_argument("--text", required=True)
    q_answer = question.add_parser("answer", help="Answer question")
    q_answer.add_argument("--id", required=True, dest="q_id")
    q_answer.add_argument("--text", required=True)

    decision = sub.add_parser("decision", help="Manage decisions").add_subparsers(dest="dec_cmd")
    dec_add = decision.add_parser("add", help="Add decision")
    dec_add.add_argument("--by", required=True)
    dec_add.add_argument("--text", required=True)
    dec_add.add_argument("--independence-waived", action="store_true")
    dec_add.add_argument("--reason")

    cost = sub.add_parser("cost", help="Manage cost").add_subparsers(dest="cost_cmd")
    cost_add = cost.add_parser("add", help="Add cost")
    cost_add.add_argument("--model", required=True)
    cost_add.add_argument("--calls", required=True, type=int)
    cost_add.add_argument("--tokens", required=True, type=int)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        raise SystemExit(1)

    session8 = _resolve_session(args)
    root = args.root
    audit_entry: dict[str, Any] | None = None

    def mutate(data: dict[str, Any]) -> None:
        nonlocal audit_entry
        if args.command == "step" and args.step_cmd == "set":
            cmd_step_set(data, args.step_num, args.step_state)
        elif args.command == "agent" and args.agent_cmd == "add":
            cmd_agent_add(data, agent_id=args.agent_id, **_agent_fields(args))
        elif args.command == "agent" and args.agent_cmd == "update":
            cmd_agent_update(data, agent_id=args.agent_id, **_agent_fields(args))
        elif args.command == "run" and args.run_cmd == "set":
            cmd_run_set(
                data,
                run_id=args.run_id,
                machine=args.machine,
                tier=args.tier,
                progress=args.progress,
                incident=args.incident,
                next_checkpoint=args.next_checkpoint,
                watchdog_state=args.watchdog_state,
                watchdog_checked_at=args.watchdog_checked_at,
                max_recoverable_loss=args.max_recoverable_loss,
            )
        elif args.command == "question" and args.q_cmd == "add":
            cmd_question_add(data, question_id=args.q_id, text=args.text)
        elif args.command == "question" and args.q_cmd == "answer":
            cmd_question_answer(data, question_id=args.q_id, text=args.text)
        elif args.command == "decision" and args.dec_cmd == "add":
            cmd_decision_add(
                data,
                by=args.by,
                text=args.text,
                independence_waived=args.independence_waived,
                reason=args.reason,
            )
            audit_entry = dict(data["decisions"][0])
        elif args.command == "cost" and args.cost_cmd == "add":
            cmd_cost_add(data, model=args.model, calls=args.calls, tokens=args.tokens)
        else:
            parser.error("missing or invalid subcommand")

    if args.command == "init":
        mutate_status(session8, lambda _data: None, root=root, initialize=True)
    else:
        with status_lock(session8, root=root):
            data = load(session8, root=root)
            mutate(data)
            save(data, root=root)
            if audit_entry is not None:
                _append_jsonl(decisions_path(session8, root=root), audit_entry)

    if not args.no_render:
        render(session8, root=root)


if __name__ == "__main__":
    main()
