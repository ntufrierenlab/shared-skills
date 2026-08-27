# maestro — Claude + Codex orchestrator

A Claude Code skill that turns the session into the **orchestrator** of a Claude + Codex
research-engineering loop: plan → implement → review → run → accept, with fixed model roles,
cross-vendor review, a three-tier watchdog for multi-day experiments, bounded review rounds, and
independent re-computation of result tables. Includes a zero-dependency live HTML dashboard.

## Install

```
ln -s ~/Courses/shared-skills/maestro ~/.claude/skills/maestro   # user-wide
# or
ln -s ~/Courses/shared-skills/maestro <project>/.claude/skills/maestro  # per project
```

Then in Claude Code: `/maestro`.

Requirements: Claude Code CLI, Codex CLI ≥ 0.150 (`npm i -g @openai/codex@latest`), Python ≥ 3.10.

## Layout

```
SKILL.md                     the skill (read this first)
scripts/status.py            status.json writer + CLI
scripts/render_dashboard.py  status.json → self-contained dashboard.html
templates/                   brief, review brief, questions, run_state, runbook
tests/                       pytest, hermetic
```

## Run the tests

```
python3 -m pytest tests -q
```
