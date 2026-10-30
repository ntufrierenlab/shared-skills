# project-status

Hourly, zero-token project progress report. `scripts/status_report.py render` copies facts from
`logbook/*/proposal.md`, `results.md`, `jobs.tsv` + gpurun job records, and `status_sources.json`
into `<project>/STATUS.md`; the root session writes judgment fields with `note`. See SKILL.md.

Tests: `python3 -m pytest project-status/tests -q`.
