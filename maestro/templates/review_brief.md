# Review brief: <task-id> (cross-vendor reviewer — you are NOT the implementer)

Your job is to decide whether this can go live and run the experiment — not to rewrite it the way
you would have written it.

1. Before reading the diff, write ONE line: "the most likely way this diff breaks the experiment
   is …". Then verify that hypothesis and the acceptance checklist. Nothing else is in scope.
2. Ask exactly three questions: Are the objectives met? Is it correct? Is there an obvious
   efficiency problem? Three yeses → PASS. Stop.
3. Two severities only. BLOCKING = objective not met, results would be wrong, or >1 h of compute
   wasted. NOTE = style, naming, could-be-better. NOTEs never block a PASS, are listed for the
   record, and the implementer does not respond to them.
4. Time box: 30 minutes. When it expires, issue the verdict; whatever is left becomes NOTEs.
5. Output (write to `review.md` AND as your final message): verdict (PASS / FAIL), the hypothesis
   line, BLOCKING list, NOTE list.

## What to review
- Brief the implementer received: `brief.md` — its Acceptance checklist is what you grade.
- Implementer's return: `return.md` (claims — verify each ✔ yourself).
- Code / tests / pilot: <paths>

## Verification you must actually run (not read)
- <test command>
- <lint command>
- <one fresh end-to-end sequence against a temp root>

## Constraints on you
- Read-only git. Modify nothing except `review.md`.
- No GPU, no daemons, no long jobs.
