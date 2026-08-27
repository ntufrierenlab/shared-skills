"""Orchestrator status file manager -- atomic JSON writes + CLI.

Usage:
    python3 scripts/status.py --session deadbeef init
    python3 -m scripts.status --session deadbeef step set 2 active
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_status(session8: str) -> dict[str, Any]:
    return {
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
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load(session8: str, *, root: str | None = None) -> dict[str, Any]:
    p = status_path(session8, root=root)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(data: dict[str, Any], *, root: str | None = None) -> Path:
    session8 = data["session"]
    p = status_path(session8, root=root)
    _atomic_write_json(p, data)
    return p


def render(session8: str, *, root: str | None = None) -> Path:
    src = status_path(session8, root=root)
    dst = dashboard_path(session8, root=root)
    render_script = Path(__file__).resolve().parent / "render_dashboard.py"
    subprocess.run(
        [sys.executable, str(render_script), str(src), str(dst)],
        check=True,
    )
    return dst


# -- Mutation helpers --------------------------------------------------------


def cmd_init(session8: str, *, root: str | None = None) -> dict[str, Any]:
    data = _empty_status(session8)
    save(data, root=root)
    return data


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
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": agent_id,
        "role": role,
        "model": model,
        "vendor": vendor,
        "state": state,
        "unblocks": unblocks,
        "started": _now_iso(),
    }
    data["agents"].append(entry)
    return data


def cmd_agent_update(data: dict[str, Any], *, agent_id: str, **fields: Any) -> dict[str, Any]:
    for agent in data["agents"]:
        if agent["id"] == agent_id:
            for k, v in fields.items():
                if v is not None:
                    agent[k] = v
            return data
    raise ValueError(f"agent '{agent_id}' not found")


def cmd_run_set(data: dict[str, Any], *, run_id: str, **fields: Any) -> dict[str, Any]:
    for run in data["runs"]:
        if run["id"] == run_id:
            for k, v in fields.items():
                if v is not None:
                    run[k] = v
            return data
    entry: dict[str, Any] = {"id": run_id}
    for k, v in fields.items():
        if v is not None:
            entry[k] = v
    data["runs"].append(entry)
    return data


def cmd_question_add(data: dict[str, Any], *, question_id: str, text: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": question_id,
        "text": text,
        "asked_at": _now_iso(),
    }
    data["questions"].append(entry)
    return data


def cmd_question_answer(data: dict[str, Any], *, question_id: str, text: str) -> dict[str, Any]:
    for q in data["questions"]:
        if q["id"] == question_id:
            q["answered_at"] = _now_iso()
            q["answer"] = text
            return data
    raise ValueError(f"question '{question_id}' not found")


def cmd_decision_add(data: dict[str, Any], *, by: str, text: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "timestamp": _now_iso(),
        "by": by,
        "text": text,
    }
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


# -- CLI ---------------------------------------------------------------------


def _resolve_session(args: argparse.Namespace) -> str:
    session = getattr(args, "session", None)
    if session:
        return session
    env = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if env:
        return env[:8]
    print("error: --session required (or set CLAUDE_CODE_SESSION_ID)", file=sys.stderr)
    sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="status.py",
        description="Orchestrator status manager",
    )
    parser.add_argument("--session", help="8-hex session id")
    parser.add_argument("--root", help="Root directory (default: ./outputs or ORCHESTRATOR_ROOT)")
    parser.add_argument("--no-render", action="store_true", help="Skip dashboard re-render")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize status.json")

    step_p = sub.add_parser("step", help="Manage step state")
    step_sub = step_p.add_subparsers(dest="step_cmd")
    step_set_p = step_sub.add_parser("set", help="Set step state")
    step_set_p.add_argument("step_num", help="Step number 1-5")
    step_set_p.add_argument("step_state", choices=["pending", "active", "done"])

    agent_p = sub.add_parser("agent", help="Manage agents")
    agent_sub = agent_p.add_subparsers(dest="agent_cmd")
    agent_add_p = agent_sub.add_parser("add", help="Add agent")
    agent_add_p.add_argument("--id", required=True, dest="agent_id")
    agent_add_p.add_argument("--role", required=True)
    agent_add_p.add_argument("--model", required=True)
    agent_add_p.add_argument("--vendor", required=True, choices=["claude", "codex"])
    agent_add_p.add_argument("--state", required=True)
    agent_add_p.add_argument("--unblocks", required=True)
    agent_upd_p = agent_sub.add_parser("update", help="Update agent fields")
    agent_upd_p.add_argument("--id", required=True, dest="agent_id")
    agent_upd_p.add_argument("--role")
    agent_upd_p.add_argument("--model")
    agent_upd_p.add_argument("--vendor", choices=["claude", "codex"])
    agent_upd_p.add_argument("--state")
    agent_upd_p.add_argument("--unblocks")

    run_p = sub.add_parser("run", help="Manage runs")
    run_sub = run_p.add_subparsers(dest="run_cmd")
    run_set_p = run_sub.add_parser("set", help="Set run fields")
    run_set_p.add_argument("--id", required=True, dest="run_id")
    run_set_p.add_argument("--machine")
    run_set_p.add_argument("--tier", choices=["L0", "L1", "L2", "L3"])
    run_set_p.add_argument("--progress")
    run_set_p.add_argument("--incident")
    run_set_p.add_argument("--next-checkpoint")

    q_p = sub.add_parser("question", help="Manage questions")
    q_sub = q_p.add_subparsers(dest="q_cmd")
    q_add_p = q_sub.add_parser("add", help="Add question")
    q_add_p.add_argument("--id", required=True, dest="q_id")
    q_add_p.add_argument("--text", required=True)
    q_ans_p = q_sub.add_parser("answer", help="Answer question")
    q_ans_p.add_argument("--id", required=True, dest="q_id")
    q_ans_p.add_argument("--text", required=True)

    dec_p = sub.add_parser("decision", help="Manage decisions")
    dec_sub = dec_p.add_subparsers(dest="dec_cmd")
    dec_add_p = dec_sub.add_parser("add", help="Add decision")
    dec_add_p.add_argument("--by", required=True)
    dec_add_p.add_argument("--text", required=True)

    cost_p = sub.add_parser("cost", help="Manage cost")
    cost_sub = cost_p.add_subparsers(dest="cost_cmd")
    cost_add_p = cost_sub.add_parser("add", help="Add cost")
    cost_add_p.add_argument("--model", required=True)
    cost_add_p.add_argument("--calls", required=True, type=int)
    cost_add_p.add_argument("--tokens", required=True, type=int)

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    session8 = _resolve_session(args)
    root = getattr(args, "root", None)

    if args.command == "init":
        data = cmd_init(session8, root=root)
    else:
        data = load(session8, root=root)

        if args.command == "step" and args.step_cmd == "set":
            cmd_step_set(data, args.step_num, args.step_state)
        elif args.command == "agent" and args.agent_cmd == "add":
            cmd_agent_add(
                data,
                agent_id=args.agent_id,
                role=args.role,
                model=args.model,
                vendor=args.vendor,
                state=args.state,
                unblocks=args.unblocks,
            )
        elif args.command == "agent" and args.agent_cmd == "update":
            cmd_agent_update(
                data,
                agent_id=args.agent_id,
                role=args.role,
                model=args.model,
                vendor=args.vendor,
                state=args.state,
                unblocks=args.unblocks,
            )
        elif args.command == "run" and args.run_cmd == "set":
            cmd_run_set(
                data,
                run_id=args.run_id,
                machine=getattr(args, "machine", None),
                tier=getattr(args, "tier", None),
                progress=getattr(args, "progress", None),
                incident=getattr(args, "incident", None),
                next_checkpoint=getattr(args, "next_checkpoint", None),
            )
        elif args.command == "question" and args.q_cmd == "add":
            cmd_question_add(data, question_id=args.q_id, text=args.text)
        elif args.command == "question" and args.q_cmd == "answer":
            cmd_question_answer(data, question_id=args.q_id, text=args.text)
        elif args.command == "decision" and args.dec_cmd == "add":
            cmd_decision_add(data, by=args.by, text=args.text)
        elif args.command == "cost" and args.cost_cmd == "add":
            cmd_cost_add(data, model=args.model, calls=args.calls, tokens=args.tokens)
        else:
            parser.print_help()
            sys.exit(1)

        save(data, root=root)

    if not args.no_render:
        render(session8, root=root)


if __name__ == "__main__":
    main()
