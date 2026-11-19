# launching-experiments — run protocol for explog projects on the CPLab cluster

Shared protocol so every new Claude Code or Codex session launches, monitors and closes out
experiments the same way: a logbook round written before launch, ntfy pushes for every
finish/failure/stage change, STATUS notes at the required events, tmux watchers with real
liveness checks, and the Accept close-out (fill → backfill → blind recompute → lessons → done).

It covers the gap the cluster tools don't: `gpurun --round` links jobs and notifies
automatically, but custom supervisors, ssh/tmux launchers and Codex workers do neither.

## Install

```bash
ln -s /home_nfs/Neillee/shared-skills/launching-experiments ~/.claude/skills/launching-experiments
ln -s /home_nfs/Neillee/shared-skills/launching-experiments ~/.codex/skills/launching-experiments
```

Depends on the cluster tools `explog`, `gpurun`, `gpus`, `notify` and on the `project-status`
skill. Pairs with `agent-conductor` for orchestrated rounds.

## Testing

Pressure-scenario tested (writing-skills RED/GREEN): a baseline session without the skill
rerouted a custom supervisor to gpurun but, when the supervisor had to be kept, only added a
single `notify`, missed per-stage pushes and logbook job linkage, wrote no `--blocked` note,
and treated user approval of a new proposal as skippable. The skill closes those gaps.
