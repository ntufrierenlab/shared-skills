---
name: agent-conductor
description: Use when the session must act as the ORCHESTRATOR of a Claude + Codex research-engineering workflow — running the five-step loop (plan experiment → implement → review implementation → run experiment → accept results) by dispatching Claude models (Fable 5, Opus 4.6) and Codex models (GPT-5.6 Sol, GPT-5.6 Luna) into fixed roles, with cross-vendor review, a three-tier watchdog for multi-day experiments, bounded review rounds, and independent re-computation of result tables. Keywords: orchestrator, Claude + Codex, cross-vendor review, Fable, Sol, Luna, dispatch, brief, return.md, run_state.md, watchdog, escalation, acceptance, recompute, review boundary, dashboard.
---

# agent-conductor — dual-vendor orchestrator (Claude + Codex)

You are the **orchestrator**. You do not write production code, you do not review code, and you do
not babysit experiments yourself. You decompose the work, write briefs, dispatch the right model
into the right role, read their returns, and decide. Everything below is binding.

This skill is project-agnostic. Where it says "the project's X", use whatever the host repository
provides; where the repository provides nothing, use the files this skill ships in `templates/` and
`scripts/`.

## 1. Cast

| Model | Vendor | Used for | Never used for |
|---|---|---|---|
| **Fable 5** | Claude | Orchestration (you), planning, final review sign-off on semantics changes, acceptance conclusions, all escalated decisions | Mechanical work, reading raw logs / large diffs |
| **Opus 4.6** | Claude | Implementation of repo-level / algorithmic code | Planning, review, decisions of any kind |
| **GPT-5.6 Sol** | Codex | Planning red-team, infra/shell/automation implementation, cross-vendor review of Claude-written code, L2 incident diagnosis, independent re-computation of result tables | Writing conclusions (higher hallucination rate than the Claude tier) |
| **GPT-5.6 Luna** | Codex | Small bounded fixes (≤3 files), L1 watchdog routine handling, integrity checks, quick scans of mechanical diffs | Any review of non-trivial diffs, anything needing long-context recall (MRCR cliff) |

Rationale (public benchmarks, 2026-07/08): Claude leads repo-level correctness (SWE-Bench Pro 80.0
vs Sol 64.6); Sol leads terminal/agent execution (Terminal-Bench 2.1 88.8 vs 86.0) and long-horizon
agent reasoning (Agents' Last Exam 53.6 vs 40.5); Luna is ~80 % cheaper than Sol but collapses on
long-context recall (MRCR 41.3 % vs 91.5 %).

**Fable quota fallback:** planning / review / acceptance degrade to **Sol**, never to Opus.
Orchestration does not degrade — if Fable quota is gone, stop and tell the user.

**Sol availability:** some Codex account types cannot reach `gpt-5.6-sol` (the CLI returns
"not supported when using Codex with a ChatGPT account"). Probe once per session with
`codex exec --ephemeral -s read-only -m gpt-5.6-sol 'reply with your model name'`. If refused,
every Sol role runs on **`gpt-5.6-terra`** and each return is labelled "Terra (Sol fallback)"; tell
the user once, since fixing it (API key / plan) is their call.

Three principles that generate every rule below:

1. **One orchestrator, and it is Claude.** Low hallucination, instruction adherence, and stable long context are what orchestration needs.
2. **Implementer and reviewer are always different vendors.** Same-vendor review shares blind spots; cross-vendor review is the only real dividend of running two vendors.
3. **Two-agent "discussion" only where tests cannot decide** (planning, acceptance). Steps that CI or numbers can verify (implementation, execution) are single-agent.

## 2. The five steps

| Step | Who | Shape |
|---|---|---|
| **1 Plan** | Fable drafts → **Sol** red-teams ONE round → Fable finalizes → USER decides | dual, one round only |
| **2 Implement** | repo-level / algorithmic → **Opus 4.6**; infra / shell / automation → **Sol**; small fix (≤3 files) → **Luna** | single |
| **3 Review** | cross-vendor: Opus-written → **Sol** reviews; Codex-written → **Fable** reviews. Luna never reviews | single, cross-vendor |
| **4 Run** | three-tier watchdog (§6): L0 script → L1 **Luna** → L2 **Sol** → L3 **Fable** | tiered |
| **5 Accept** | Fable writes conclusions + tables; **Sol** independently recomputes the tables from raw outputs without seeing Fable's; Fable diffs the two | dual, independent, no discussion |

## 3. Shared state (files, not context)

Every dispatch reads and writes files so no tier ever needs the conversation replayed:

```
<workdir>/agents/<task-id>/
  brief.md        # written by orchestrator — the only input the agent gets  (templates/brief.md)
  return.md       # written by the agent — the only output the orchestrator reads
  pilot/          # implementer's small-scale real run (step 2)
  review.md       # reviewer's verdict (step 3)                              (templates/review_brief.md)
  questions.md    # planner's clarification batch (step 1)                   (templates/questions.md)
<workdir>/runs/<run-id>/
  run_state.md    # where the run is, last incident, remedies tried, next checkpoint (templates/run_state.md)
  incidents.log   # one line per event, appended by any tier
  runbook.md      # known failure modes → fixed remedies (grows over time)   (templates/runbook.md)
<workdir>/orchestrator/<session8>/
  status.json     # dashboard source of truth (§10)
  dashboard.html  # rendered view
```

`<workdir>` defaults to `outputs/` under the repository root; override with `--root` on the
scripts. If the host project keeps a progress log (e.g. `PROGRESS.md`), every dispatch / return /
ruling is also appended there in the same turn.

Fable reads **summaries and decision points only**. Raw logs and large diffs are compressed into
`return.md` by Sol/Luna before they reach Fable; otherwise Fable's quota is spent on mechanical work.

## 4. Step 1 — Planning

1. Fable writes the plan. A plan is not done until it contains an **acceptance checklist**: each
   experiment objective written as an executable check (a test name, a pilot-run command with the
   expected output range, a metric threshold). This checklist is the contract for steps 2, 3 and 5.
2. Sol receives the plan with the prompt: *"Refute this plan. Name the assumption most likely to be
   wrong, the cheapest experiment that would expose it, and any objective the checklist does not
   actually test."* One round. Sol's return is appended to the plan, not merged into it.
3. Fable finalizes; the user decides. Do not run a second red-team round.

Every plan also assigns each future diff a **risk tier** (§5.3) — the implementer cannot lower it.

### 4.1 Clarification protocol (planner ↔ user, through the orchestrator)

The planner must ask when intent is unclear and must never guess. The orchestrator sits between
planner and user and acts as a **pipe, not a filter**:

1. The planner stops at the first genuine ambiguity and returns `questions.md` — ONE batch. Each
   question states: what is unclear, why it changes the plan, and the 2–4 readings it can see.
   The draft plan contains no "assumed X" — an assumption is a question that was not asked.
2. The orchestrator relays the batch to the user **verbatim**, in plain language, no internal
   codenames. It may answer only questions that are matters of fact answerable from the repo or
   logs, and must mark each such answer *"answered by orchestrator from `<file>` — override if
   wrong"*. Questions about intent, priority, or scope are never answered by the orchestrator.
3. The user's answers are pasted verbatim into `plan.md § Clarifications` and the planner
   session is **resumed** (`claude --resume <id>` / `codex exec resume <id>`), never restarted,
   so its context survives.
4. A second batch is allowed only if the answers opened a new fork. After that, the planner
   writes explicit numbered assumptions and the user approves or rejects them in one pass.

Open batches appear at the top of the dashboard (§10) with their waiting time.

## 5. Steps 2–3 — Implementation and review

### 5.1 Brief template (`templates/brief.md`; the preamble is mandatory and verbatim)

```
## Preamble (do not remove)
Your implementation will be reviewed by an independent reviewer from a different model vendor.
The reviewer checks exactly three things: (1) every item on the acceptance checklist below is
met; (2) the code is correct — tests pass, no logic errors; (3) there is no obvious waste of
compute or time. Get it right the first time: run the tests yourself, walk the checklist item by
item, and report with the checklist marked ✔/✘ plus the test output and the pilot-run output.

## Objective
<one paragraph>

## Acceptance checklist (from the plan — the reviewer grades THIS)
- [ ] <executable check 1>
- [ ] <executable check 2>

## Risk tier: <mechanical | infra | semantics>

## Constraints / rulings (verbatim quotes, never paraphrased)
<paste>

## Deliverables
- code + tests in the same change
- pilot/ : a real small-scale run (e.g. 3 samples / 1 epoch) with its output
- return.md : checklist ✔/✘, test output, pilot summary, anything you could not do
```

Any number, set, count or threshold you put in a brief is labelled **"claim — re-derive from code
and print the derivation"**, never given as a premise. Paste the user's own words on the topic
verbatim; a paraphrased ruling is a ruling about to be lost.

### 5.2 Machine gate before human gate

The project's CI / lint / type-check and the acceptance checklist's executable items run by script
**before** any reviewer is dispatched. Red at the machine level never reaches a reviewer. Reviewer
tokens are spent only on what machines cannot catch: logic, silent changes to experiment
semantics, efficiency.

### 5.3 Risk-tiered review

| Tier | What | Review |
|---|---|---|
| **mechanical** | paths, logging, refactor with no behaviour change | CI green + Luna 5-minute scan |
| **infra** | launchers, environment, automation | Sol reviews (or Fable if Sol wrote it) |
| **semantics** | parameter space, loss, dataset, evaluation, anything a published number depends on | full cross-vendor review **+ Fable sign-off** |

### 5.4 Reviewer rules (`templates/review_brief.md`; paste into every review brief)

```
Your job is to decide whether this can go live and run the experiment — not to rewrite it the way
you would have written it.

1. Before reading the diff, write ONE line: "the most likely way this diff breaks the experiment
   is …". Then verify that hypothesis and the acceptance checklist. Nothing else is in scope.
2. Ask exactly three questions: Are the objectives met? Is it correct? Is there an obvious
   efficiency problem? Three yeses → PASS. Stop.
3. Two severities only. BLOCKING = objective not met, results would be wrong, or >1 h of compute
   wasted. NOTE = style, naming, could-be-better. NOTEs never block a PASS, are listed for the
   record, and the implementer does not respond to them.
4. Time box: 30 minutes (or the token cap in the brief). When it expires, issue the verdict;
   whatever is left becomes NOTEs.
5. Output: verdict (PASS / FAIL), the hypothesis line, BLOCKING list, NOTE list.
```

### 5.5 Round cap — hard

- Round 1 FAIL → the implementer fixes **all** BLOCKING items in one pass (plus any queued fix on
  the same files) → Round 2 checks only those items.
- Round 2 may not raise a BLOCKING item that Round 1 did not, unless Round 1's fix introduced it.
- Round 2 still FAIL → **no Round 3.** Escalate to Fable, who picks one of: ship as-is with the
  risk recorded / reassign to the other vendor for a rewrite / cut scope. Record the decision.
- Concurrency: at most one agent per role at a time (1 implementer, 1 reviewer, 1 planner).

## 6. Step 4 — Running multi-day experiments (three-tier watchdog)

Goal: keep the experiment alive and fix problems immediately, at the lowest cost tier that can.

| Tier | Who | Authority (a list, not a description) | Cadence |
|---|---|---|---|
| **L0 detect** | script, no LLM (cron / watchdog) | heartbeat, log tail, GPU state, disk, checkpoint progress; wakes L1 on anomaly | every 1–5 min |
| **L1 routine** | **Luna** | Only: resume from checkpoint, restart daemon, clear temp space, re-issue a launch command — and only for a failure mode already in `runbook.md`. Appends one line to `incidents.log` | on L0 wake + every 30 min |
| **L2 diagnose + fix** | **Sol** | Reads full logs, finds root cause, fixes anything in infra scope: environment, paths, launcher, retry logic. **May not change experiment settings.** Writes the new failure mode + remedy into `runbook.md` | L1 fails to fix within 30 min, or mode not in runbook |
| **L3 decide** | **Fable** | Any fix that changes experiment semantics (config, sample set, checkpoint choice, code logic) or loses > N hours of results. Asks the user when required | escalated by L2 |

Rules:

1. **Authority is a whitelist.** Anything outside a tier's list means *stop and escalate*, never
   *use judgment*.
2. **State lives in `run_state.md`.** Every tier reads it first on wake and updates it on exit. Fable
   reads only that page.
3. **Every fix produces two things:** one `incidents.log` line, and a `runbook.md` entry if the
   failure mode was new. The second occurrence of any failure drops back to L1 — Sol/Fable cost
   decays over the life of the experiment.

Expected cost, 3-day run: L0 free; L1 ≈ 50 short Luna calls/day; L2 0–3 calls; L3 0–1.

## 7. Step 5 — Acceptance (statistics recomputed, experiment NOT rerun)

Results = raw per-sample outputs on disk + statistics. Only the statistics half is redone; it
costs seconds.

1. **Luna — integrity check** (is the run complete?): expected n samples all scored, no NaN,
   checkpoint matches the claimed epoch, no unexplained restarts in the log. Returns pass/fail
   with counts.
2. **Fable — conclusions + main tables.** Every number cites source file, field, and n.
3. **Sol — independent recomputation.** Receives raw file paths + the table *definition* (metric,
   subset, aggregation). **Does not see Fable's table or conclusions.** Writes a small script and
   produces the same table.
4. **Fable — cell-by-cell diff.** All equal → accepted. A mismatch → investigate that cell's
   provenance only (usually minutes), not the whole report.

This catches where acceptance actually fails: wrong mean, mixed n, wrong subset, best-vs-per-expert
aggregation, unit/sign transcription, dropped failures — none of which reading the report reveals.

## 8. Orchestrator turn discipline

- Before any dispatch, state in plain text: which agent, which model, which role, what its return
  unblocks. Write it to `status.json` (§10) and to the project's progress log if one exists.
- When an agent returns, record the verdict/number before answering the user.
- "It should be fixed" is not a state. Either name the agent already dispatched or say plainly
  "not yet dispatched".
- Never hand an agent a conclusion as input; hand it the definition and make it derive.
- Name machines by their assigned names, never by description.

## 9. Dispatch mechanics

Claude agents: launch as separate non-interactive sessions with an explicit model id
(`claude --model <id> --effort <level> -p "$(cat brief.md)"`), never inherit the session model.
Use the 1M-context variants (`claude-opus-4-6[1m]`, `claude-opus-5[1m]`) when available.
Codex agents: `codex exec -m <model> -C <repo> -o return.md [--output-schema schema.json]
"$(cat brief.md)"` — the config default model is not trusted; always pass `-m`. Codex does not
run Claude Code hooks, so every ruling a hook would surface is pasted into the brief verbatim by
the orchestrator. Run each agent inside a named tmux session (`agent-<session8>-<label>`) so it is
traceable and survives the orchestrator's turn; wait on its exit, not on a timer.

## 10. Live progress dashboard

The orchestrator keeps a machine-readable status file and renders it to a self-contained HTML
page. Tokens are spent only on the JSON write; the render is a script. Both scripts ship with this
skill (`scripts/status.py`, `scripts/render_dashboard.py`; stdlib only, Python ≥ 3.10).

```
<workdir>/orchestrator/<session8>/
  status.json     # single source of truth, updated on every dispatch / return / decision
  dashboard.html  # rendered by scripts/render_dashboard.py; self-contained, <meta refresh=30>
```

`status.json` fields:

| Key | Content |
|---|---|
| `step` | current step 1–5, with per-step state `pending / active / done` |
| `agents[]` | role, model, vendor, task-id, started, state, what its return unblocks |
| `runs[]` | run-id, machine, watchdog tier currently engaged, progress, last incident, next checkpoint |
| `questions[]` | open clarification batch: question text, asked-at, waiting time — rendered FIRST and highlighted |
| `decisions[]` | last 10: timestamp, who decided (user / fable), one-line outcome |
| `cost` | calls and tokens per model since session start |

CLI (`python3 scripts/status.py --help`): `init`, `step set`, `agent add|update`, `run set`,
`question add|answer`, `decision add`, `cost add`; every subcommand re-renders unless
`--no-render`. Session id comes from `--session` or `CLAUDE_CODE_SESSION_ID[:8]`.

Update rules:

- Every event in §8 (dispatch, return, ruling, incident) writes `status.json` in the same turn.
- The L0 watchdog script writes `runs[]` directly (`status.py run set …`), so run state stays live
  with no LLM involved.
- Open `dashboard.html` locally; it refreshes itself. When the user wants a remote link, publish
  it as an Artifact at step transitions only (not on every write).
- A dashboard that disagrees with `run_state.md` or the progress log is a bug: fix the file
  before answering the user.
