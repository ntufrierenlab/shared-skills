#!/usr/bin/env python3
"""Render a project-level STATUS.md from structured files only. Zero LLM tokens.

Facts are copied, never estimated. A field whose source is missing prints 「無資料」.
Judgment fields (goal, blockers, next step) come from STATUS.notes.md, written by the
root session via `note`.

Usage:
  status_report.py render --project <dir> [--jobs-dir <dir>] [--recent 3]
  status_report.py note   --project <dir> [--goal ..] [--blocked ..] [--next ..] [--clear-blocked]
  status_report.py render-all --root <Project dir> [--jobs-dir <dir>]
Project-specific metrics: optional <project>/status_sources.json, see --help-sources.
"""
import argparse, json, os, re, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

NA = "無資料"
NOTES = "STATUS.notes.md"
OUT = "STATUS.md"
SOURCES = "status_sources.json"
HELP_SOURCES = """status_sources.json — list of metric specs rendered under 「關鍵指標」.
[
  {"label": "R016 N 官方 test 中位 SRCC", "file": "results/R016/report.json",
   "key": "median_S_cand", "fmt": ".4f"},
  {"label": "R017 訓練完成格數", "glob": "results/R017/chains/*/metrics.json",
   "count_where": {"key": "status", "equals": "passed"}, "of": 30}
]
"file"+"key": dotted path into one JSON. "glob"+"count_where": count files whose key equals value.
Missing file or key → 無資料. Never computed beyond copy/count."""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"')
    return meta, m.group(2)


def section(body, title):
    m = re.search(rf"^## {re.escape(title)}\s*\n(.*?)(?=^## |\Z)", body, re.S | re.M)
    if not m:
        return None
    lines = [l for l in m.group(1).splitlines() if l.strip() and not l.strip().startswith("<!--")]
    # drop html comment blocks spanning lines
    out, skip = [], False
    for l in lines:
        if "<!--" in l:
            skip = "-->" not in l
            continue
        if skip:
            skip = "-->" not in l
            continue
        out.append(l)
    return out


def first_statement(lines):
    """First bold line of a 結論 section, else its first line.
    Bold labels ending in a colon (「**逐條對照判準：**」) introduce a list, not a verdict; skip them."""
    if not lines:
        return None
    for l in lines:
        s = l.strip()
        if s.startswith("**") and not s.rstrip("*").rstrip().endswith(("：", ":")):
            return s
    return lines[0].strip()


def read(p):
    try:
        return Path(p).read_text(encoding="utf-8")
    except OSError:
        return None


def job_record(jobs_dir, jid):
    d = Path(jobs_dir) / jid
    if not d.is_dir():
        return dict(id=jid, status="紀錄已刪除", host=NA, gpu=NA, started=NA, ended=NA)
    g = lambda f: (read(d / f) or NA).strip() or NA
    return dict(id=jid, status=g("status"), host=g("host"), gpu=g("gpu"),
                started=g("started"), ended=g("ended"))


def rounds(project):
    lb = Path(project) / "logbook"
    out = []
    for d in sorted(lb.glob("R[0-9][0-9][0-9]-*")):
        p = read(d / "proposal.md")
        if p is None:
            continue
        meta, _ = frontmatter(p)
        r = dict(dir=d, rid=meta.get("round", d.name[:4]), status=meta.get("status", "?"),
                 hypothesis=meta.get("hypothesis", NA), created=meta.get("created", NA),
                 conclusion=None, lessons=None, jobs=[])
        res = read(d / "results.md")
        if res is not None:
            _, body = frontmatter(res)
            r["conclusion"] = first_statement(section(body, "結論"))
            les = section(body, "教訓")
            r["lessons"] = len([l for l in les if l.strip().startswith("-")]) if les else None
        jt = read(d / "jobs.tsv")
        if jt:
            for line in jt.splitlines()[1:]:
                cols = line.split("\t")
                if cols and cols[0]:
                    r["jobs"].append(cols[0])
        out.append(r)
    return out


def get_dotted(obj, key):
    for part in key.split("."):
        if isinstance(obj, dict) and part in obj:
            obj = obj[part]
        elif isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return None
    return obj


def metrics(project):
    spec = read(Path(project) / SOURCES)
    if spec is None:
        return []
    try:
        items = json.loads(spec)
    except json.JSONDecodeError as e:
        return [("status_sources.json 解析失敗", f"{e}", SOURCES)]
    rows = []
    for it in items:
        label = it.get("label", "?")
        if "glob" in it:
            files = sorted(Path(project).glob(it["glob"]))
            cw = it.get("count_where")
            n = 0
            for f in files:
                try:
                    j = json.loads(f.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                if cw is None or get_dotted(j, cw["key"]) == cw["equals"]:
                    n += 1
            val = f"{n}/{it['of']}" if "of" in it else str(n)
            rows.append((label, val if files else NA, it["glob"]))
            continue
        f = Path(project) / it["file"]
        txt = read(f)
        if txt is None:
            rows.append((label, NA, f"{it['file']}（檔案不存在）"))
            continue
        try:
            v = get_dotted(json.loads(txt), it["key"])
        except json.JSONDecodeError:
            v = None
        if v is None:
            rows.append((label, NA, f"{it['file']}:{it['key']}（欄位不存在）"))
        else:
            if "fmt" in it and isinstance(v, (int, float)):
                v = format(v, it["fmt"])
            rows.append((label, str(v), f"{it['file']}:{it['key']}"))
    return rows


def git_head(project):
    try:
        h = subprocess.run(["git", "-C", str(project), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(project), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return h or NA, bool(dirty)
    except (OSError, subprocess.TimeoutExpired):
        return NA, None


def notes(project):
    t = read(Path(project) / NOTES)
    if t is None:
        return dict(goal=[NA], blocked=[NA], next=[NA], updated=NA)
    meta, body = frontmatter(t)
    g = lambda title: section(body, title) or [NA]
    return dict(goal=g("目標"), blocked=g("卡住的事"), next=g("下一步"), updated=meta.get("updated", NA))


def render(project, jobs_dir, recent):
    project = Path(project).resolve()
    rs = rounds(project)
    active = [r for r in rs if r["status"] in ("proposed", "running")]
    done = [r for r in rs if r["status"] not in ("proposed", "running")]
    n = notes(project)
    head, dirty = git_head(project)
    cur = active[-1] if active else None
    cur_jobs = [job_record(jobs_dir, j) for j in cur["jobs"]] if cur else []
    live = [j for j in cur_jobs if j["status"] in ("RUNNING", "running") or j["status"].startswith("RUN")]
    blocked_flag = n["blocked"] != [NA] and any(l.strip() and l.strip() != "無" for l in n["blocked"])

    fm = [
        "---",
        f"project: {project.name}",
        f"updated: {now()}",
        f"current_round: {cur['rid'] if cur else 'none'}",
        f"current_status: {cur['status'] if cur else 'idle'}",
        f"jobs_running: {len(live)}",
        f"blocked: {'true' if blocked_flag else 'false'}",
        f"notes_updated: {n['updated']}",
        f"git_head: {head}{' (dirty)' if dirty else ''}",
        f"rounds_total: {len(rs)}",
        f"rounds_done: {len(done)}",
        "---",
    ]
    L = fm + [
        f"# {project.name} 進度報表",
        "",
        "> 由 status_report.py 自動產生。數字一律抄自所列來源檔，未經盲重算；正式數據以各輪 results.md 為準。"
        " 「目標」「卡住的事」「下一步」為 root session 在 STATUS.notes.md 寫的判斷。",
        "",
        "## 目標",
        *n["goal"],
        "",
        "## 目前在跑",
    ]
    if cur:
        L += [f"- **{cur['rid']}**（{cur['status']}）：{cur['hypothesis']}",
              f"  - 來源：logbook/{cur['dir'].name}/proposal.md:status,hypothesis"]
        if cur_jobs:
            L += ["", "| job | 節點:卡 | 狀態 | 開始 | 結束 |", "|---|---|---|---|---|"]
            for j in cur_jobs:
                L.append(f"| {j['id']} | {j['host']}:{j['gpu']} | {j['status']} | {j['started']} | {j['ended']} |")
            L.append(f"  - 來源：logbook/{cur['dir'].name}/jobs.tsv → {jobs_dir}/<id>/{{status,host,gpu,started,ended}}")
        else:
            L.append(f"- 本輪 jobs.tsv 無 `--round` 派工紀錄（{NA}）")
    else:
        L.append(f"- 無進行中輪次（logbook 無 status 為 proposed/running 的輪次）")
    m = metrics(project)
    L += ["", "## 關鍵指標"]
    if m:
        L += ["| 指標 | 值 | 來源 |", "|---|---|---|"] + [f"| {a} | {b} | {c} |" for a, b, c in m]
    else:
        L.append(f"- 未設定 {SOURCES}（{NA}）")
    L += ["", f"## 最近結論（最近 {recent} 輪已結束）"]
    for r in done[-recent:][::-1]:
        c = r["conclusion"] or f"{NA}（results.md 無結論段）"
        L += [f"- **{r['rid']}**（{r['status']}）：{c}",
              f"  - 來源：logbook/{r['dir'].name}/results.md:結論（首句）"]
    if not done:
        L.append(f"- {NA}")
    L += ["", "## 卡住的事", *n["blocked"], "", "## 下一步", *n["next"], "",
          f"## 所有輪次（{len(rs)}）", "| 輪次 | 狀態 | 建立 | 假設 |", "|---|---|---|---|"]
    for r in rs:
        h = r["hypothesis"] if len(r["hypothesis"]) <= 60 else r["hypothesis"][:60] + "…"
        L.append(f"| {r['rid']} | {r['status']} | {r['created']} | {h} |")
    L += ["", f"- 來源：logbook/*/proposal.md frontmatter；notes：{NOTES}（updated {n['updated']}）", ""]
    return "\n".join(L)


def cmd_render(a):
    text = render(a.project, a.jobs_dir, a.recent)
    out = Path(a.project) / OUT
    tmp = out.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, out)
    print(out)


def cmd_render_all(a):
    for d in sorted(Path(a.root).iterdir()):
        if (d / "logbook").is_dir():
            cmd_render(argparse.Namespace(project=d, jobs_dir=a.jobs_dir, recent=a.recent))


def cmd_note(a):
    p = Path(a.project) / NOTES
    t = read(p)
    if t is None:
        meta, body = {}, "## 目標\n無資料\n\n## 卡住的事\n無\n\n## 下一步\n無資料\n"
    else:
        meta, body = frontmatter(t)

    def put(title, lines):
        nonlocal body
        new = f"## {title}\n" + "\n".join(lines) + "\n\n"
        if re.search(rf"^## {re.escape(title)}\s*\n", body, re.M):
            body = re.sub(rf"^## {re.escape(title)}\s*\n.*?(?=^## |\Z)", new, body, flags=re.S | re.M)
        else:
            body += new

    if a.goal:
        put("目標", a.goal)
    if a.clear_blocked:
        put("卡住的事", ["無"])
    elif a.blocked:
        put("卡住的事", [f"- {b}" for b in a.blocked])
    if a.next:
        put("下一步", [f"- {x}" for x in a.next])
    meta["updated"] = now()
    fm = "---\n" + "\n".join(f"{k}: {v}" for k, v in meta.items()) + "\n---\n"
    p.write_text(fm + body.rstrip() + "\n", encoding="utf-8")
    print(p)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--help-sources", action="store_true", help="explain status_sources.json")
    sub = ap.add_subparsers(dest="cmd")
    default_jobs = str(Path(os.environ.get("CPLAB_NFS", str(Path.home()))) / "jobs")
    r = sub.add_parser("render", help="write <project>/STATUS.md")
    r.add_argument("--project", required=True)
    r.add_argument("--jobs-dir", default=default_jobs)
    r.add_argument("--recent", type=int, default=3)
    r.set_defaults(fn=cmd_render)
    ra = sub.add_parser("render-all", help="render every <root>/*/ that has logbook/")
    ra.add_argument("--root", required=True)
    ra.add_argument("--jobs-dir", default=default_jobs)
    ra.add_argument("--recent", type=int, default=3)
    ra.set_defaults(fn=cmd_render_all)
    n = sub.add_parser("note", help="root session: update judgment fields in STATUS.notes.md")
    n.add_argument("--project", required=True)
    n.add_argument("--goal", nargs="+", help="lines for 目標 (replaces)")
    n.add_argument("--blocked", nargs="+", help="items for 卡住的事 (replaces)")
    n.add_argument("--clear-blocked", action="store_true")
    n.add_argument("--next", nargs="+", help="items for 下一步 (replaces)")
    n.set_defaults(fn=cmd_note)
    a = ap.parse_args(argv)
    if a.help_sources:
        print(HELP_SOURCES)
        return 0
    if not a.cmd:
        ap.print_help()
        return 2
    a.fn(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
