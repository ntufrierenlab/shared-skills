"""Hermetic tests for scripts/status.py + render_dashboard.py."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from scripts.render_dashboard import render_html
from scripts.status import (
    _empty_status,
    cmd_agent_add,
    cmd_agent_update,
    cmd_cost_add,
    cmd_decision_add,
    cmd_init,
    cmd_question_add,
    cmd_question_answer,
    cmd_run_set,
    cmd_step_set,
    dashboard_path,
    load,
    main,
    render,
    save,
    status_dir,
    status_path,
)

SESSION = "deadbeef"


@pytest.fixture()
def session_root(tmp_path: Path) -> str:
    return str(tmp_path)


class TestInit:
    def test_creates_status_and_dashboard(self, session_root: str) -> None:
        cmd_init(SESSION, root=session_root)
        render(SESSION, root=session_root)
        sdir = status_dir(SESSION, root=session_root)
        assert (sdir / "status.json").is_file()
        assert (sdir / "dashboard.html").is_file()

    def test_status_json_structure(self, session_root: str) -> None:
        cmd_init(SESSION, root=session_root)
        data = load(SESSION, root=session_root)
        assert data["session"] == SESSION
        assert set(data["steps"].keys()) == {"1", "2", "3", "4", "5"}
        assert all(v == "pending" for v in data["steps"].values())
        assert data["agents"] == []
        assert data["runs"] == []
        assert data["questions"] == []
        assert data["decisions"] == []
        assert data["cost"] == {}


class TestStepSet:
    def test_round_trip(self, session_root: str) -> None:
        cmd_init(SESSION, root=session_root)
        data = load(SESSION, root=session_root)
        cmd_step_set(data, "2", "active")
        save(data, root=session_root)
        reloaded = load(SESSION, root=session_root)
        assert reloaded["steps"]["2"] == "active"
        assert reloaded["steps"]["1"] == "pending"

    def test_invalid_step(self) -> None:
        data = _empty_status(SESSION)
        with pytest.raises(ValueError, match="step must be 1-5"):
            cmd_step_set(data, "0", "active")

    def test_invalid_state(self) -> None:
        data = _empty_status(SESSION)
        with pytest.raises(ValueError, match="state must be"):
            cmd_step_set(data, "1", "running")


class TestAgentAddUpdate:
    def test_add_and_update(self, session_root: str) -> None:
        cmd_init(SESSION, root=session_root)
        data = load(SESSION, root=session_root)
        cmd_agent_add(
            data, agent_id="impl-1", role="implementer", model="opus-4.6",
            vendor="claude", state="running", unblocks="step 3",
        )
        save(data, root=session_root)
        data = load(SESSION, root=session_root)
        assert len(data["agents"]) == 1
        assert data["agents"][0]["id"] == "impl-1"
        assert data["agents"][0]["state"] == "running"
        assert "started" in data["agents"][0]

        cmd_agent_update(data, agent_id="impl-1", state="done")
        save(data, root=session_root)
        data = load(SESSION, root=session_root)
        assert data["agents"][0]["state"] == "done"
        assert data["agents"][0]["role"] == "implementer"

    def test_update_nonexistent_raises(self) -> None:
        data = _empty_status(SESSION)
        with pytest.raises(ValueError, match="not found"):
            cmd_agent_update(data, agent_id="nope", state="done")

    def test_update_only_changes_given_fields(self) -> None:
        data = _empty_status(SESSION)
        cmd_agent_add(
            data, agent_id="a1", role="reviewer", model="sol",
            vendor="codex", state="pending", unblocks="acceptance",
        )
        cmd_agent_update(data, agent_id="a1", state="running")
        agent = data["agents"][0]
        assert agent["state"] == "running"
        assert agent["role"] == "reviewer"
        assert agent["model"] == "sol"
        assert agent["vendor"] == "codex"


class TestRunSet:
    def test_create_and_update(self) -> None:
        data = _empty_status(SESSION)
        cmd_run_set(data, run_id="run-1", machine="rtx3090", tier="L1")
        assert len(data["runs"]) == 1
        assert data["runs"][0]["machine"] == "rtx3090"

        cmd_run_set(data, run_id="run-1", incident="GPU OOM")
        assert data["runs"][0]["incident"] == "GPU OOM"
        assert data["runs"][0]["machine"] == "rtx3090"

    def test_missing_optional_fields(self) -> None:
        data = _empty_status(SESSION)
        cmd_run_set(data, run_id="run-2")
        assert data["runs"][0] == {"id": "run-2"}


class TestDecisions:
    def test_keeps_last_10(self, session_root: str) -> None:
        cmd_init(SESSION, root=session_root)
        data = load(SESSION, root=session_root)
        for i in range(12):
            cmd_decision_add(data, by="user", text=f"decision-{i}")
        save(data, root=session_root)

        data = load(SESSION, root=session_root)
        assert len(data["decisions"]) == 10
        assert data["decisions"][0]["text"] == "decision-11"
        assert data["decisions"][-1]["text"] == "decision-2"

    def test_newest_first_in_html(self) -> None:
        data = _empty_status(SESSION)
        for i in range(12):
            cmd_decision_add(data, by="user", text=f"decision-{i}")

        html_str = render_html(data, "test.json")
        pos_11 = html_str.find("decision-11")
        pos_2 = html_str.find("decision-2")
        assert pos_11 < pos_2, "newest decision should appear first in HTML"
        assert ">decision-0<" not in html_str
        assert ">decision-1<" not in html_str


class TestQuestions:
    def test_open_question_panel(self) -> None:
        data = _empty_status(SESSION)
        cmd_question_add(data, question_id="q1", text="What dataset?")
        html_str = render_html(data, "test.json")
        assert "Open Questions" in html_str
        assert "What dataset?" in html_str

    def test_answered_removes_from_open(self) -> None:
        data = _empty_status(SESSION)
        cmd_question_add(data, question_id="q1", text="What dataset?")
        cmd_question_answer(data, question_id="q1", text="FiveK")
        html_str = render_html(data, "test.json")
        assert "Open Questions" not in html_str

    def test_answer_nonexistent_raises(self) -> None:
        data = _empty_status(SESSION)
        with pytest.raises(ValueError, match="not found"):
            cmd_question_answer(data, question_id="nope", text="hi")


class TestCost:
    def test_accumulates(self) -> None:
        data = _empty_status(SESSION)
        cmd_cost_add(data, model="opus", calls=3, tokens=1000)
        cmd_cost_add(data, model="opus", calls=2, tokens=500)
        assert data["cost"]["opus"]["calls"] == 5
        assert data["cost"]["opus"]["tokens"] == 1500


class TestAtomicWrite:
    def test_concurrent_reader_never_sees_partial_json(self, session_root: str) -> None:
        cmd_init(SESSION, root=session_root)
        sp = status_path(SESSION, root=session_root)
        errors: list[str] = []
        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set():
                try:
                    raw = sp.read_text(encoding="utf-8")
                    json.loads(raw)
                except json.JSONDecodeError as e:
                    errors.append(f"partial JSON: {e}")
                except FileNotFoundError:
                    pass
                time.sleep(0.001)

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        try:
            for i in range(50):
                data = load(SESSION, root=session_root)
                cmd_decision_add(data, by="user", text=f"iter-{i}")
                save(data, root=session_root)
        finally:
            stop.set()
            t.join(timeout=2)

        assert errors == [], f"partial reads observed: {errors}"


class TestHtmlEscaping:
    def test_script_injection_escaped(self) -> None:
        data = _empty_status(SESSION)
        xss = '<script>alert("xss")</script>'
        cmd_decision_add(data, by="user", text=xss)
        cmd_question_add(data, question_id="q1", text=xss)
        cmd_agent_add(
            data, agent_id="a1", role=xss, model="m",
            vendor="claude", state="s", unblocks=xss,
        )
        cmd_run_set(data, run_id="r1", incident=xss)

        html_str = render_html(data, "test.json")
        assert "<script>" not in html_str
        assert "&lt;script&gt;" in html_str


class TestCli:
    def test_init_via_main(self, session_root: str) -> None:
        main(["--session", SESSION, "--root", session_root, "--no-render", "init"])
        data = load(SESSION, root=session_root)
        assert data["session"] == SESSION

    def test_step_set_via_main(self, session_root: str) -> None:
        main(["--session", SESSION, "--root", session_root, "--no-render", "init"])
        main(["--session", SESSION, "--root", session_root, "--no-render",
              "step", "set", "1", "active"])
        data = load(SESSION, root=session_root)
        assert data["steps"]["1"] == "active"

    def test_agent_add_via_main(self, session_root: str) -> None:
        main(["--session", SESSION, "--root", session_root, "--no-render", "init"])
        main([
            "--session", SESSION, "--root", session_root, "--no-render",
            "agent", "add", "--id", "a1", "--role", "impl", "--model", "opus",
            "--vendor", "claude", "--state", "running", "--unblocks", "review",
        ])
        data = load(SESSION, root=session_root)
        assert len(data["agents"]) == 1

    def test_full_sequence_via_main(self, session_root: str) -> None:
        m = ["--session", SESSION, "--root", session_root, "--no-render"]
        main([*m, "init"])
        main([*m, "step", "set", "1", "active"])
        main([
            *m, "agent", "add", "--id", "impl-1", "--role", "implementer",
            "--model", "opus-4.6", "--vendor", "claude", "--state", "running",
            "--unblocks", "step 3",
        ])
        main([*m, "agent", "update", "--id", "impl-1", "--state", "done"])
        main([*m, "run", "set", "--id", "run-1", "--machine", "rtx3090", "--tier", "L1"])
        main([*m, "question", "add", "--id", "q1", "--text", "What scope?"])
        main([*m, "question", "answer", "--id", "q1", "--text", "Full dataset"])
        main([*m, "decision", "add", "--by", "user", "--text", "Approved plan"])
        main([*m, "cost", "add", "--model", "opus", "--calls", "5", "--tokens", "10000"])

        data = load(SESSION, root=session_root)
        assert data["steps"]["1"] == "active"
        assert data["agents"][0]["state"] == "done"
        assert len(data["runs"]) == 1
        assert len(data["questions"]) == 1
        assert data["questions"][0]["answer"] == "Full dataset"
        assert len(data["decisions"]) == 1
        assert data["cost"]["opus"]["calls"] == 5

    def test_cli_as_subprocess(self, session_root: str) -> None:
        """Verify python3 scripts/status.py works as a subprocess."""
        script = str(Path(__file__).resolve().parent.parent / "scripts" / "status.py")
        result = subprocess.run(
            [sys.executable, script, "--session", SESSION,
             "--root", session_root, "--no-render", "init"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        data = load(SESSION, root=session_root)
        assert data["session"] == SESSION

    def test_cli_as_module(self, session_root: str) -> None:
        """Verify python3 -m scripts.status works."""
        repo_root = str(Path(__file__).resolve().parent.parent)
        result = subprocess.run(
            [sys.executable, "-m", "scripts.status", "--session", SESSION,
             "--root", session_root, "--no-render", "init"],
            capture_output=True, text=True, cwd=repo_root,
        )
        assert result.returncode == 0, result.stderr
        data = load(SESSION, root=session_root)
        assert data["session"] == SESSION

    def test_render_via_cli_init(self, session_root: str) -> None:
        """init without --no-render produces dashboard.html."""
        main(["--session", SESSION, "--root", session_root, "init"])
        dp = dashboard_path(SESSION, root=session_root)
        assert dp.is_file()
        content = dp.read_text(encoding="utf-8")
        assert "Orchestrator Dashboard" in content

    def test_help_flag(self) -> None:
        script = str(Path(__file__).resolve().parent.parent / "scripts" / "status.py")
        result = subprocess.run(
            [sys.executable, script, "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "status manager" in result.stdout.lower() or "usage" in result.stdout.lower()


class TestRenderIdempotent:
    def test_same_json_same_html_except_timestamp(self) -> None:
        data = _empty_status(SESSION)
        cmd_decision_add(data, by="user", text="test decision")
        html1 = render_html(data, "test.json")
        html2 = render_html(data, "test.json")
        lines1 = [ln for ln in html1.splitlines() if "Rendered at" not in ln]
        lines2 = [ln for ln in html2.splitlines() if "Rendered at" not in ln]
        assert lines1 == lines2


class TestDarkModeSupport:
    def test_prefers_color_scheme_in_css(self) -> None:
        data = _empty_status(SESSION)
        html_str = render_html(data, "test.json")
        assert "prefers-color-scheme: dark" in html_str


class TestAutoRefresh:
    def test_meta_refresh(self) -> None:
        data = _empty_status(SESSION)
        html_str = render_html(data, "test.json")
        assert "http-equiv='refresh'" in html_str
        assert "content='30'" in html_str


class TestRenderDashboardCli:
    def test_help_flag(self) -> None:
        script = str(Path(__file__).resolve().parent.parent / "scripts" / "render_dashboard.py")
        result = subprocess.run(
            [sys.executable, script, "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_standalone_render(self, session_root: str) -> None:
        cmd_init(SESSION, root=session_root)
        src = status_path(SESSION, root=session_root)
        dst = Path(session_root) / "out.html"
        script = str(Path(__file__).resolve().parent.parent / "scripts" / "render_dashboard.py")
        result = subprocess.run(
            [sys.executable, script, str(src), str(dst)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert dst.is_file()
        assert "Orchestrator Dashboard" in dst.read_text(encoding="utf-8")

    def test_default_output(self, session_root: str) -> None:
        """Without output arg, writes dashboard.html next to status.json."""
        cmd_init(SESSION, root=session_root)
        src = status_path(SESSION, root=session_root)
        script = str(Path(__file__).resolve().parent.parent / "scripts" / "render_dashboard.py")
        result = subprocess.run(
            [sys.executable, script, str(src)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        expected = src.parent / "dashboard.html"
        assert expected.is_file()
