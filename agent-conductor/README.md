# agent-conductor — Claude + Codex orchestrator

A Claude Code or Codex skill for orchestrating a dual-vendor research-engineering loop:
plan → implement → review → run → accept. It provides cross-vendor review, bounded watchdog
authority, structured dispatch metadata, and a live HTML dashboard.

## Install

Choose the skill directory used by the host:

```bash
# Claude Code, user-wide
ln -s /path/to/shared-skills/agent-conductor ~/.claude/skills/agent-conductor

# Codex, user-wide
ln -s /path/to/shared-skills/agent-conductor ~/.codex/skills/agent-conductor
```

Use the normal skill invocation offered by the host. A Codex root launches Claude workers through
`scripts/dispatch_agent.py`; a Claude root uses the same wrapper for consistent artifacts.

Codex implementation routes to GPT-5.6 Sol by default. GPT-5.6 Terra is reserved for focused,
mechanical tasks with complete executable acceptance checks; uncertain or higher-risk work stays
with Sol. Luna is not part of the routing policy.

Requirements: POSIX, Python ≥ 3.10, Claude Code CLI, and Codex CLI ≥ 0.150. Runtime scripts use only
the Python standard library. tmux is optional and is only an observability backend.

## Layout

```
SKILL.md                      orchestration policy
scripts/dispatch_agent.py     probe / foreground run / optional detached launch + wait
scripts/watchdog_l0.py        detection-only L0 watchdog
scripts/status.py             locked status.json writer
scripts/render_dashboard.py   status.json → dashboard.html
templates/                    plan, brief, review, run state, runbook
tests/                        hermetic pytest suite
```

## Validate

```bash
python3 -m pytest tests -q
ruff check scripts tests
```
