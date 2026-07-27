"""Gate tests for write_script.py helpers -- no Claude CLI call, no network."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import write_script as ws


def test_slugify():
    assert ws.slugify("Why SCHOOL is a scam?!") == "why-school-is-a-scam"
    assert ws.slugify("  roth IRA vs 401k  ") == "roth-ira-vs-401k"
    assert ws.slugify("!!!") == "script"
    assert len(ws.slugify("x" * 100)) <= 40


def test_build_prompt_mentions_topic_and_mode():
    mono = ws.build_prompt("credit cards", dialogue=False)
    assert "credit cards" in mono and "ONLY the script text" in mono
    dia = ws.build_prompt("credit cards", dialogue=True)
    assert '"A: "' in dia and '"B: "' in dia


def test_strip_fences():
    assert ws.strip_fences("```\nhello\nworld\n```") == "hello\nworld"
    assert ws.strip_fences("no fences") == "no fences"
    assert ws.strip_fences("```text\nA: hi\nB: yo\n```\n") == "A: hi\nB: yo"


def test_build_front_matter_dialogue():
    fm = ws.build_front_matter(True, "hype", "minecraft", "V-A", "V-B")
    assert fm == "speakers: A=V-A, B=V-B\nstyle: hype\nbg: minecraft\n---\n"


def test_build_front_matter_plain_monologue_is_empty():
    assert ws.build_front_matter(False, None, None, "V-A", "V-B") == ""


def test_ask_claude_resolves_shim_via_which(monkeypatch):
    """Windows npm installs expose claude.cmd; bare 'claude' won't spawn."""
    seen = {}

    class FakeProc:
        returncode = 0
        stdout = "A: hi\nB: yo"
        stderr = ""

    monkeypatch.setattr(ws.shutil, "which", lambda _: r"C:\npm\claude.CMD")
    monkeypatch.setattr(ws.subprocess, "run",
                        lambda cmd, **k: seen.setdefault("cmd", cmd) and FakeProc()
                        or FakeProc())
    out = ws.ask_claude("prompt")
    assert seen["cmd"][0] == r"C:\npm\claude.CMD"
    assert out == "A: hi\nB: yo"


def test_ask_claude_missing_cli_raises(monkeypatch):
    monkeypatch.setattr(ws.shutil, "which", lambda _: None)
    try:
        ws.ask_claude("prompt")
        assert False, "should have raised"
    except RuntimeError as e:
        assert "not found" in str(e)


def test_ask_claude_surfaces_stdout_errors(monkeypatch):
    """Auth failures print to stdout with empty stderr - must not be hidden."""

    class FakeProc:
        returncode = 1
        stdout = "Failed to authenticate. API Error: 401"
        stderr = ""

    monkeypatch.setattr(ws.shutil, "which", lambda _: "claude.cmd")
    monkeypatch.setattr(ws.subprocess, "run", lambda *a, **k: FakeProc())
    try:
        ws.ask_claude("prompt")
        assert False, "should have raised"
    except RuntimeError as e:
        assert "401" in str(e)
