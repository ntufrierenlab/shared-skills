# runbook — <run-id>

<!-- One entry per known failure mode. L1 (Terra) may act ONLY on entries listed here. -->

## FM-01 <short name>
- **Command ID:** <stable-id>
- **Signature:** <what L0/L1 sees: log line, exit code, metric>
- **Root cause:** <one line>
- **Remedy argv (L1-authorised):** `["executable", "fixed-arg", "fixed-target"]`
- **Validated target:** <exact process/run directory owned by this run>
- **Cooldown:** <duration>
- **Maximum executions per 24 h:** <count>
- **Verify fixed:** <what to check>
- **First seen:** <UTC>  **Added by:** L2 Sol
