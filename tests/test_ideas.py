"""Gate tests for ideas.py -- no Claude calls, no network."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ideas

GOOD = """BEGIN IDEAS
Why your brain deletes boring memories | your brain runs storage cleanup nightly
The bank fee with a secret name | it has cost you more than your phone
END IDEAS"""


def test_parse_ideas_happy():
    got = ideas.parse_ideas("junk before\n" + GOOD + "\njunk after")
    assert len(got) == 2
    assert got[0]["title"].startswith("Why your brain")
    assert got[1]["hook"].startswith("it has cost")


def test_parse_ideas_rejects_garbage():
    assert ideas.parse_ideas("no sentinels at all") == []
    messy = "BEGIN IDEAS\nno pipe on this line\n | hook without title\nEND IDEAS"
    assert ideas.parse_ideas(messy) == []


def test_prompt_carries_theme_count_sentinels():
    p = ideas.PROMPT.format(count=8, theme="money traps")
    assert "money traps" in p and "8" in p
    assert "BEGIN IDEAS" in p and "END IDEAS" in p
    assert "text generator" in p            # the anti-agentic guard rides along


def test_generate_retries_then_succeeds(monkeypatch):
    replies = iter(["nonsense", GOOD])
    prompts = []
    monkeypatch.setattr(ideas, "ask_claude",
                        lambda p, **k: prompts.append(p) or next(replies))
    got = ideas.generate_ideas("t", count=2)
    assert len(got) == 2
    assert "REMINDER" in prompts[1]


def test_generate_fails_loudly(monkeypatch):
    monkeypatch.setattr(ideas, "ask_claude", lambda p, **k: "still nonsense")
    with pytest.raises(RuntimeError, match="unparseable"):
        ideas.generate_ideas("t", count=5)
