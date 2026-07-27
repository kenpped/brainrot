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
