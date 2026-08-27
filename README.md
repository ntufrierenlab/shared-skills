# shared-skills

A collection of Claude Code skills, one directory per skill. Each skill is self-contained:
`SKILL.md` (the skill), optional `scripts/`, `templates/`, `tests/`, and its own `README.md`.

| Skill | What it does |
|---|---|
| [`maestro`](maestro/) | Orchestrate a Claude + Codex plan → implement → review → run → accept loop with cross-vendor review, a three-tier experiment watchdog, and a live HTML dashboard |

## Install

**One skill, user-wide** (symlink so `git pull` updates it):

```
git clone https://github.com/<you>/shared-skills ~/Courses/shared-skills
ln -s ~/Courses/shared-skills/maestro ~/.claude/skills/maestro
```

**One skill, per project:** same symlink into `<project>/.claude/skills/`.

**As a plugin marketplace** (all skills, managed by Claude Code):

```
claude plugin marketplace add <you>/shared-skills
claude plugin install maestro@shared-skills
```

## Conventions for adding a skill

1. `mkdir <skill-name>`; write `<skill-name>/SKILL.md` with YAML frontmatter `name:` (== dir
   name) and a one-paragraph `description:` that says WHEN to use it (Claude matches on this).
2. Scripts: Python ≥ 3.10, stdlib only unless the skill's README says otherwise; no absolute
   paths; every script has `--help`.
3. Tests: `<skill-name>/tests/`, hermetic (`tmp_path` only), runnable with
   `python3 -m pytest <skill-name>/tests -q` from the repo root.
4. Add the skill to the table above and to `.claude-plugin/marketplace.json`.
5. `bash run_tests.sh` must be green before pushing.
