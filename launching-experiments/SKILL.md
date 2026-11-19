---
name: launching-experiments
description: Use when about to start, babysit, or wrap up any GPU training/evaluation run or multi-hour CPU job on the CPLab cluster in a project that keeps an explog logbook — including runs launched by custom supervisors, ssh/tmux, or Codex workers instead of gpurun, smoke tests, and requests like "趕快跑", "跑完告訴我", "開新一輪". Applies to Claude Code and Codex root sessions.
---

# launching-experiments

Every real run leaves three traces that other sessions and the user rely on: a **logbook round**
written before launch, **ntfy pushes** for every finish/failure/stage change, and **STATUS notes**.
Speed comes from running steps in parallel, never from skipping a trace.

## Before launch

1. **Smoke or debug?** No `--round`, no logbook. Anything whose numbers may be cited → a round.
2. **Context:** `explog brief` (+ `ideabank brief` when designing). Read the project `CLAUDE.md`/`AGENTS.md`.
3. **Round:** `explog new "<one-sentence hypothesis>"` → fill `proposal.md` *before* launch:
   hypothesis, change vs baseline, **成功判準**, full config, node:card. Never edit it after
   approval; corrections go in an appendix. A new round's proposal needs the user's approval;
   once code review passes, launch without asking again.
4. **Cards:** the user names them. Check free VRAM with `gpus`; launch when memory suffices,
   don't wait for an empty card. Never auto-pick cards. No card-hour caps; timeouts are hang guards only.
5. **STATUS:** `python3 ~/.claude/skills/project-status/scripts/status_report.py note --project . --next "..."`
   (Codex: `~/.codex/skills/...`). REQUIRED SUB-SKILL: project-status.

## Launch

| How it runs | Logbook link | ntfy |
|---|---|---|
| `gpurun <node:card> --round R0xx -- cmd` | automatic (`jobs.tsv`) | automatic: done + failure with log tail |
| supervisor / ssh+tmux / Codex worker / CPU script | **you** must record job ids, cards, start/end in the run dir; `explog fill` will say "還沒有關聯的 job" | **you** must call `notify` at every stage change, failure, and the end |

Prefer gpurun per cell. If a custom launcher is unavoidable, end every stage with
`notify -t "<round> <stage>" "exit $rc"` (add `-p high` on failure) and run it in **tmux on
the worker node**. Building remote commands: quote with `shlex.quote`; `$?` inside a
double-quoted ssh string expands locally and always reads 0.

## While it runs

- Watchers live in tmux on a worker node, not in a background shell of your session (it dies with you).
- Liveness = target process alive (`ps` full command) **and** no `ended`/`exit` file **and** log mtime fresh.
  A status file alone has missed dead runs for hours.
- Error / stop-loss / waiting for user or GPU → `note --blocked "..."` immediately; resolved → `note --clear-blocked --next "..."`.
- Post-training stages (eval, scoring) fan out across the user's card pool like training cells; a serial single-card stage idles the cluster.

## After

1. `explog fill R0xx` (gpurun runs) or a job table built from your receipts.
2. Integrity check → `logbook` agent backfills `results.md` (every number cites file + field; missing → 「log 中無此數據」).
3. Blind recompute by a different model from raw paths only → compare. Findings go to an audit file, never silent edits.
4. `explog lessons`; `explog status R0xx done` (pushes ntfy); `note --clear-blocked --next "..."`.
   Orchestrated rounds: agent-conductor Accept step.

## Red flags

| Thought | Reality |
|---|---|
| "User said hurry, write the proposal later" | Proposals written after the run lose their value. Write it fast, in parallel with implementation. |
| "My supervisor notifies" | Only gpurun notifies. Check that your launcher calls `notify`, or add the call. |
| "I'll check back later" | Without a tmux watcher and ntfy, nobody learns of a failure. |
| "Smoke counts as the run" | Smoke numbers are engineering-only; never cite them as results. |
| "Wait for an empty card / budget OK" | Launch on sufficient VRAM; no budget stops. |
