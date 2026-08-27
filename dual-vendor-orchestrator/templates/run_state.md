# run_state — <run-id>

<!-- Every tier reads this first on wake and updates it on exit. Fable reads only this page. -->

- **Machine:** <name>
- **Launched:** <UTC>   **Command:** `<cmd>`
- **Progress:** <epoch / sample / step> of <total>   (as of <UTC>)
- **Next checkpoint:** <path or time>
- **Tier engaged:** L0 | L1 | L2 | L3
- **Last incident:** <UTC> — <one line>  (see incidents.log)
- **Remedies tried this incident:** 1. … 2. …
- **Open escalation:** none | L1→L2 <UTC> | L2→L3 <UTC> — <why>
