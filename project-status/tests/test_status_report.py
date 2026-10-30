import json, subprocess, sys
from pathlib import Path
import importlib.util

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "status_report.py"
spec = importlib.util.spec_from_file_location("sr", SCRIPT); sr = importlib.util.module_from_spec(spec); spec.loader.exec_module(sr)


def make_project(tmp_path):
    p = tmp_path / "proj"; jobs = tmp_path / "jobs"
    r1 = p / "logbook" / "R001-foo"; r1.mkdir(parents=True)
    (r1 / "proposal.md").write_text('---\nround: R001\nhypothesis: "h1"\nstatus: done\ncreated: 2026-01-01\n---\n# x\n')
    (r1 / "results.md").write_text("---\nround: R001\n---\n## 數據\n1\n## 結論\n<!-- c -->\n**判定 fail，D=+0.001**\nmore\n## 教訓\n- a\n- b\n")
    r2 = p / "logbook" / "R002-bar"; r2.mkdir()
    (r2 / "proposal.md").write_text('---\nround: R002\nhypothesis: "h2"\nstatus: running\ncreated: 2026-01-02\n---\n')
    (r2 / "jobs.tsv").write_text("job_id\thost\tgpus\tsubmitted\tcmd\nj1\tgpu1\t0\tt\tc\nj2\tgpu1\t1\tt\tc\n")
    (jobs / "j1").mkdir(parents=True)
    for k, v in dict(status="DONE:1", host="gpu1", gpu="0", started="s", ended="e").items():
        (jobs / "j1" / k).write_text(v + "\n")
    (p / "runs").mkdir(); (p / "runs" / "rep.json").write_text(json.dumps({"summary": {"srcc": {"D": 0.00209}}}))
    (p / "status_sources.json").write_text(json.dumps([
        {"label": "D", "file": "runs/rep.json", "key": "summary.srcc.D", "fmt": "+.4f"},
        {"label": "missing", "file": "runs/nope.json", "key": "a"},
        {"label": "cells", "glob": "runs/cells/*.json", "count_where": {"key": "s", "equals": "ok"}, "of": 3}]))
    return p, jobs


def test_render_copies_facts_and_marks_missing(tmp_path):
    p, jobs = make_project(tmp_path)
    out = sr.render(p, jobs, 3)
    assert "current_round: R002" in out and "current_status: running" in out
    assert "| j1 | gpu1:0 | DONE:1 |" in out          # exit code preserved, not "done"
    assert "| j2 | 無資料:無資料 | 紀錄已刪除 |" in out
    assert "| D | +0.0021 | runs/rep.json:summary.srcc.D |" in out
    assert "| missing | 無資料 | runs/nope.json（檔案不存在） |" in out
    assert "| cells | 無資料 |" in out
    assert "**R001**（done）：**判定 fail，D=+0.001**" in out
    assert "blocked: false" in out and "## 目標\n無資料" in out


def test_note_roundtrip_and_blocked_flag(tmp_path):
    p, jobs = make_project(tmp_path)
    sr.main(["note", "--project", str(p), "--goal", "beat 0.808", "--blocked", "waiting gpu", "--next", "launch"])
    out = sr.render(p, jobs, 3)
    assert "blocked: true" in out and "- waiting gpu" in out and "- launch" in out and "beat 0.808" in out
    sr.main(["note", "--project", str(p), "--clear-blocked"])
    out = sr.render(p, jobs, 3)
    assert "blocked: false" in out and "## 卡住的事\n無" in out
    assert "- launch" in out  # untouched sections survive


def test_cli_render_writes_atomically(tmp_path):
    p, jobs = make_project(tmp_path)
    r = subprocess.run([sys.executable, str(SCRIPT), "render", "--project", str(p), "--jobs-dir", str(jobs)], capture_output=True, text=True)
    assert r.returncode == 0 and (p / "STATUS.md").exists() and not (p / "STATUS.md.tmp").exists()


def test_first_statement_skips_bold_labels():
    lines = ["判定：成功——E1 在等效界內", "**逐條對照 proposal §成功判準：**", "- a"]
    assert sr.first_statement(lines) == "判定：成功——E1 在等效界內"
    assert sr.first_statement(["**Criteria:**", "**判定 fail**"]) == "**判定 fail**"


def test_render_all_skips_worktrees_and_optouts(tmp_path):
    root = tmp_path / "Project"
    for name in ("A", "A-wt", "B-off"):
        (root / name / "logbook").mkdir(parents=True)
    (root / "A-wt" / ".git").write_text("gitdir: /x/.git/worktrees/A-wt\n")   # worktree copy
    (root / "B-off" / ".no-status").touch()
    sr.main(["render-all", "--root", str(root), "--jobs-dir", str(tmp_path / "jobs")])
    assert (root / "A" / "STATUS.md").exists()
    assert not (root / "A-wt" / "STATUS.md").exists()
    assert not (root / "B-off" / "STATUS.md").exists()
