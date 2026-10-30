---
name: project-status
description: Use when a project needs a project-level progress report (STATUS.md) that an external collector reads on a schedule, when a root session finishes a stage, hits an error, or is about to launch a long run, or when asked to "update the status", "整理進度", "寫進度報表". Applies to Claude Code and Codex root sessions on cplab/explog projects.
---

# project-status

One `STATUS.md` per project, re-rendered hourly, read by a collector that only looks at diffs.
**Facts are copied by a script; judgment is written by the root session; nobody types numbers.**

## Why a script and not a model

Baseline test (fresh session asked to write STATUS.md, no skill): 56k tokens for one report,
wrote "last 3 jobs DONE" when one was `DONE:1` (exit 1), copied SRCC figures secondhand from a
proposal's motivation section, and invented the next-step list from the generic workflow.
Hourly that is 1.3M tokens/day of unverifiable prose. The renderer costs zero tokens and
cannot paraphrase.

## The contract

| Field | Written by | How |
|---|---|---|
| round list, status, hypothesis, created | script | `logbook/*/proposal.md` frontmatter |
| current round's jobs, node:card, status, start/end | script | `logbook/<round>/jobs.tsv` → `jobs/<id>/{status,host,gpu,started,ended}` |
| latest conclusions | script | first bold line of each `results.md` `## 結論` |
| key metrics | script | `<project>/status_sources.json` (file + dotted key, or glob + count) |
| 目標 / 卡住的事 / 下一步 | **root session** | `status_report.py note` → `STATUS.notes.md` |

`STATUS.md` is generated output. Never edit it by hand; the next render overwrites it.
A missing source prints `無資料`. It is never filled in from memory.

## Root session duties (Claude Code or Codex)

Run `note` at exactly these events. The command is the deliverable; a sentence in chat is not.

```bash
S=~/.claude/skills/project-status/scripts/status_report.py   # Codex: ~/.codex/skills/project-status/...
python3 $S note --project <repo> --next "..."                  # new round opened / long run about to launch
python3 $S note --project <repo> --blocked "..."               # error, stop-loss, waiting on user or GPU
python3 $S note --project <repo> --clear-blocked --next "..."  # blocker resolved / round accepted
python3 $S render --project <repo>                             # optional: refresh now instead of waiting for cron
```

`--blocked` and `--next` replace the whole section, so restate the current list, not the delta.
Write what the collector needs to act on: what is waited for, who must decide, what fires next.

Numbers in a note are allowed only when copied from a file you just read, with that file named
in the same line. If a metric matters often, put it in `status_sources.json` instead.

## Setup (once per project)

```bash
python3 $S note --project <repo> --goal "<target metric, SOTA value, current best with source>"
cp ~/.claude/skills/project-status/templates/status_sources.example.json <repo>/status_sources.json  # edit paths
cat ~/.claude/skills/project-status/templates/instructions-snippet.md >> <repo>/CLAUDE.md
cat ~/.claude/skills/project-status/templates/instructions-snippet.md >> <repo>/AGENTS.md
```

Hourly render on one node (all projects live on NFS, one cron is enough):

```
0 * * * * python3 /home_nfs/<user>/shared-skills/project-status/scripts/status_report.py render-all --root /home_nfs/<user>/Project >/dev/null 2>&1
```

## Red flags

- Typing an SRCC/D/CI value into a note from recollection → put it in `status_sources.json`.
- Editing `STATUS.md` directly → it is overwritten; edit `STATUS.notes.md` via `note`.
- Summarizing job state in prose ("jobs finished") → the table already shows `DONE:0` vs `DONE:1`.
- Letting a worker (Sol/Terra/subagent) run `note` → only the root session writes; NFS has no locking.
- Writing 下一步 from the generic workflow ("then review, then accept") → take it from this round's proposal/runbook; name the gate that actually fires next (GREEN test slipped here: invented a sign-off step that had already passed).
- Reporting STATUS.md figures as a basis for a proposal → they are unaudited copies; `results.md` after blind recompute is the only admissible source.
