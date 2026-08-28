"""Render status.json to a self-contained dashboard.html.

Usage:
    python3 scripts/render_dashboard.py status.json [dashboard.html]
    python3 -m scripts.render_dashboard status.json [dashboard.html]
"""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path

CSS = """
:root {
  --bg: #f8f9fa; --fg: #212529; --card: #fff; --border: #dee2e6;
  --accent: #0d6efd; --warn: #ffc107; --warn-bg: #fff3cd; --warn-fg: #664d03;
  --table-stripe: #f1f3f5; --muted: #6c757d;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1a1a2e; --fg: #e0e0e0; --card: #16213e; --border: #3a3a5c;
    --accent: #4dabf7; --warn: #ffd43b; --warn-bg: #3d3000; --warn-fg: #ffd43b;
    --table-stripe: #1e2a45; --muted: #909090;
  }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: var(--bg); color: var(--fg); padding: 1.5rem; max-width: 960px; margin: 0 auto; }
h1 { font-size: 1.4rem; margin-bottom: 1rem; }
h2 { font-size: 1.1rem; margin: 1.2rem 0 0.5rem; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
        padding: 1rem; margin-bottom: 1rem; }
.warn-card { background: var(--warn-bg); border-color: var(--warn); color: var(--warn-fg); }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border); }
tr:nth-child(even) { background: var(--table-stripe); }
th { font-weight: 600; }
.step-track { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.step { padding: 0.4rem 0.8rem; border-radius: 4px; font-size: 0.85rem;
        border: 1px solid var(--border); }
.step-pending { opacity: 0.5; }
.step-active { background: var(--accent); color: #fff; border-color: var(--accent);
               font-weight: 600; }
.step-done { opacity: 0.7; text-decoration: line-through; }
.muted { color: var(--muted); font-size: 0.85rem; }
footer { margin-top: 2rem; font-size: 0.8rem; color: var(--muted); }
"""

STEP_LABELS = {
    "1": "1. Plan",
    "2": "2. Implement",
    "3": "3. Review",
    "4": "4. Run",
    "5": "5. Accept",
}


def _esc(text: object) -> str:
    return html.escape(str(text))


def _waiting_time(asked_at: str) -> str:
    try:
        asked = datetime.fromisoformat(asked_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - asked
        total_minutes = int(delta.total_seconds() // 60)
        hours, minutes = divmod(total_minutes, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except (ValueError, TypeError):
        return "?"


def render_html(data: dict, source_path: str) -> str:
    parts: list[str] = []

    parts.append("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>")
    parts.append("<meta http-equiv='refresh' content='30'>")
    parts.append(f"<title>Orchestrator — {_esc(data.get('session', '?'))}</title>")
    parts.append(f"<style>{CSS}</style></head><body>")
    parts.append(f"<h1>Orchestrator Dashboard — {_esc(data.get('session', '?'))}</h1>")

    open_qs = [q for q in data.get("questions", []) if "answered_at" not in q]
    if open_qs:
        parts.append('<div class="card warn-card"><h2>Open Questions</h2>')
        parts.append('<div class="table-wrap"><table>')
        parts.append("<tr><th>ID</th><th>Question</th><th>Waiting</th></tr>")
        for q in open_qs:
            parts.append(
                f"<tr><td>{_esc(q.get('id', ''))}</td>"
                f"<td>{_esc(q.get('text', ''))}</td>"
                f"<td>{_esc(_waiting_time(q.get('asked_at', '')))}</td></tr>"
            )
        parts.append("</table></div></div>")

    steps = data.get("steps", {})
    parts.append('<div class="card"><h2>Steps</h2><div class="step-track">')
    for sn in ("1", "2", "3", "4", "5"):
        state = steps.get(sn, "pending")
        cls = f"step step-{_esc(state)}"
        parts.append(f'<span class="{cls}">{STEP_LABELS.get(sn, sn)}</span>')
    parts.append("</div></div>")

    agents = data.get("agents", [])
    if agents:
        parts.append('<div class="card"><h2>Agents</h2><div class="table-wrap">')
        parts.append(
            "<table><tr><th>ID</th><th>Role</th><th>Model</th><th>Vendor</th>"
            "<th>State</th><th>Exit</th><th>Finished</th><th>Artifact</th>"
            "<th>Unblocks</th><th>Started</th></tr>"
        )
        for a in agents:
            parts.append(
                f"<tr><td>{_esc(a.get('id', ''))}</td>"
                f"<td>{_esc(a.get('role', ''))}</td>"
                f"<td>{_esc(a.get('model', ''))}</td>"
                f"<td>{_esc(a.get('vendor', ''))}</td>"
                f"<td>{_esc(a.get('state', ''))}</td>"
                f"<td>{_esc(a.get('exit_code', ''))}</td>"
                f"<td>{_esc(a.get('finished', ''))}</td>"
                f"<td>{_esc(Path(a['artifact']).name if a.get('artifact') else '')}</td>"
                f"<td>{_esc(a.get('unblocks', ''))}</td>"
                f"<td>{_esc(a.get('started', ''))}</td></tr>"
            )
        parts.append("</table></div></div>")

    runs = data.get("runs", [])
    if runs:
        parts.append('<div class="card"><h2>Runs</h2><div class="table-wrap">')
        parts.append(
            "<table><tr><th>ID</th><th>Machine</th><th>Tier</th>"
            "<th>Progress</th><th>Last Incident</th><th>Next Checkpoint</th></tr>"
        )
        for r in runs:
            parts.append(
                f"<tr><td>{_esc(r.get('id', ''))}</td>"
                f"<td>{_esc(r.get('machine', ''))}</td>"
                f"<td>{_esc(r.get('tier', ''))}</td>"
                f"<td>{_esc(r.get('progress', ''))}</td>"
                f"<td>{_esc(r.get('incident', ''))}</td>"
                f"<td>{_esc(r.get('next_checkpoint', r.get('next-checkpoint', '')))}</td></tr>"
            )
        parts.append("</table></div></div>")

    decisions = data.get("decisions", [])
    if decisions:
        parts.append('<div class="card"><h2>Decisions (last 10)</h2><div class="table-wrap">')
        parts.append("<table><tr><th>Time</th><th>By</th><th>Decision</th></tr>")
        for d in decisions:
            parts.append(
                f"<tr><td>{_esc(d.get('timestamp', ''))}</td>"
                f"<td>{_esc(d.get('by', ''))}</td>"
                f"<td>{_esc(d.get('text', ''))}</td></tr>"
            )
        parts.append("</table></div></div>")

    cost = data.get("cost", {})
    if cost:
        parts.append('<div class="card"><h2>Cost per Model</h2><div class="table-wrap">')
        parts.append("<table><tr><th>Model</th><th>Calls</th><th>Tokens</th></tr>")
        for model_name, vals in sorted(cost.items()):
            parts.append(
                f"<tr><td>{_esc(model_name)}</td>"
                f"<td>{_esc(vals.get('calls', 0))}</td>"
                f"<td>{_esc(vals.get('tokens', 0))}</td></tr>"
            )
        parts.append("</table></div></div>")

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts.append(f'<footer>Rendered at {_esc(now_utc)} from {_esc(Path(source_path).name)}</footer>')
    parts.append("</body></html>")

    return "\n".join(parts)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="render_dashboard.py",
        description="Render status.json to dashboard.html",
    )
    parser.add_argument("input", help="Path to status.json")
    parser.add_argument("output", nargs="?", default=None, help="Output path (default: dashboard.html next to input)")
    args = parser.parse_args(argv)

    src = Path(args.input)
    if args.output:
        dst = Path(args.output)
    else:
        dst = src.parent / "dashboard.html"

    with open(src, encoding="utf-8") as f:
        data = json.load(f)

    html_str = render_html(data, str(src))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(html_str, encoding="utf-8")


if __name__ == "__main__":
    main()
