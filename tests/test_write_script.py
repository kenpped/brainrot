"""Gate tests for write_script.py helpers -- no Claude CLI call, no network."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import write_script as ws

# Abbreviated from a real claude -p reply (2026-07-27): asked for a script
# about phone batteries in winter, it explored the repo, went agentic, and
# wrote about bananas with commentary around it. No sentinels, off topic.
BANANA_REPLY = """\
Write permission wasn't granted in this session, so here's the script. Save it
as `scripts/radioactive-banana.txt` - it's in the dialogue format from
`scripts/phone-bowl-speaker.txt:1`.

A: Do not eat that banana.
B: It is a banana.
A: Every banana has potassium forty in it. That is a radioactive isotope.
B: Okay but a tiny amount.

135 words, 16 lines, roughly 40 seconds at the `hype` rate of +28%.
Next: `python brainrot.py scripts/radioactive-banana.txt` once the file is saved.
"""

GOOD_REPLY = """\
BEGIN SCRIPT
A: Why does my phone battery die so fast in winter?
B: Because lithium ions move slower in the cold. The battery is fine, the chemistry is just frozen.
A: So the percent number is lying to me?
B: The gauge guesses from voltage, and cold voltage sags. Warm the phone and the charge comes back.
A: That is actually insane.
B: Keep it in an inside pocket and winter stops eating your battery.
END SCRIPT
"""


def test_slugify():
    assert ws.slugify("Why SCHOOL is a scam?!") == "why-school-is-a-scam"
    assert ws.slugify("  roth IRA vs 401k  ") == "roth-ira-vs-401k"
    assert ws.slugify("!!!") == "script"
    assert len(ws.slugify("x" * 100)) <= 40


def test_build_prompt_mentions_topic_mode_and_sentinels():
    mono = ws.build_prompt("credit cards", dialogue=False)
    assert "credit cards" in mono
    assert ws.SENTINEL_BEGIN in mono and ws.SENTINEL_END in mono
    assert "text generator" in mono          # the anti-agentic guard
    dia = ws.build_prompt("credit cards", dialogue=True)
    assert '"A: "' in dia and '"B: "' in dia


def test_strip_fences():
    assert ws.strip_fences("```\nhello\nworld\n```") == "hello\nworld"
    assert ws.strip_fences("no fences") == "no fences"


def test_extract_script_takes_only_the_block():
    body = ws.extract_script("junk before\n" + GOOD_REPLY + "junk after\n")
    assert body.startswith("A: Why does my phone")
    assert "junk" not in body
    assert "BEGIN" not in body


def test_extract_script_none_without_sentinels():
    assert ws.extract_script(BANANA_REPLY) is None
    assert ws.extract_script("") is None


def test_topic_words_drop_stopwords_and_shorts():
    assert ws.topic_words("why your phone battery dies faster in winter") == \
        ["phone", "battery", "dies", "faster", "winter"]


def test_on_topic_catches_the_banana_drift():
    topic = "why your phone battery dies faster in winter"
    assert not ws.on_topic("bananas have potassium forty in them", topic)
    assert ws.on_topic("cold weather slows the battery chemistry", topic)
    assert ws.on_topic("anything", "a of to in")  # no content words -> no check


def test_generate_retries_then_succeeds(monkeypatch):
    replies = iter([BANANA_REPLY, GOOD_REPLY])
    prompts = []

    def fake_ask(prompt, timeout=300):
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr(ws, "ask_claude", fake_ask)
    body = ws.generate("why your phone battery dies faster in winter", dialogue=True)
    assert body.startswith("A: Why does my phone")
    assert len(prompts) == 2
    assert "REMINDER" in prompts[1]          # retry uses the harder prompt


def test_generate_fails_loudly_after_two_bad_replies(monkeypatch):
    monkeypatch.setattr(ws, "ask_claude", lambda *a, **k: BANANA_REPLY)
    with pytest.raises(RuntimeError, match="BEGIN SCRIPT"):
        ws.generate("phone battery winter", dialogue=True)


def test_generate_rejects_on_topic_but_tiny(monkeypatch):
    tiny = "BEGIN SCRIPT\nA: battery.\nB: winter.\nEND SCRIPT"
    monkeypatch.setattr(ws, "ask_claude", lambda *a, **k: tiny)
    with pytest.raises(RuntimeError, match="words"):
        ws.generate("phone battery winter", dialogue=True)


def test_build_front_matter_dialogue():
    fm = ws.build_front_matter(True, "hype", "minecraft", "V-A", "V-B")
    assert fm == "speakers: A=V-A, B=V-B\nstyle: hype\nbg: minecraft\n---\n"


def test_build_front_matter_plain_monologue_is_empty():
    assert ws.build_front_matter(False, None, None, "V-A", "V-B") == ""


def test_build_front_matter_cast_wins_over_speakers():
    fm = ws.build_front_matter(True, "hype", None, "V-A", "V-B",
                               cast=["grump", "hype"])
    assert fm == "cast: grump, hype\nstyle: hype\n---\n"
    assert "speakers" not in fm


def test_build_prompt_cast_injects_personas():
    chars = {
        "grump": {"voice": "v", "persona": "a permanently unimpressed old man"},
        "hype": {"voice": "v", "persona": "an overcaffeinated hype kid"},
    }
    p = ws.build_prompt("space junk", True, cast=["grump", "hype"], characters=chars)
    assert "permanently unimpressed old man" in p
    assert "overcaffeinated hype kid" in p
    assert '"grump: "' in p and '"hype: "' in p
    assert "space junk" in p
    assert ws.SENTINEL_BEGIN in p


def test_ask_claude_resolves_shim_and_runs_in_neutral_cwd(monkeypatch):
    """Windows npm shims need which(); cwd must NOT be the repo, or claude -p
    goes agentic (reads files, writes commentary, wanders off topic)."""
    seen = {}

    class FakeProc:
        returncode = 0
        stdout = GOOD_REPLY
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["cwd"] = cmd, kwargs.get("cwd")
        seen["input"] = kwargs.get("input")
        return FakeProc()

    monkeypatch.setattr(ws.shutil, "which", lambda _: r"C:\npm\claude.CMD")
    monkeypatch.setattr(ws.subprocess, "run", fake_run)
    ws.ask_claude("line one\nline two")
    assert seen["cmd"][0] == r"C:\npm\claude.CMD"
    repo = str(Path(ws.__file__).resolve().parent).lower()
    assert seen["cwd"] is not None and not str(seen["cwd"]).lower().startswith(repo)


def test_ask_claude_passes_prompt_via_stdin_not_argv(monkeypatch):
    """cmd.exe shims truncate argv at the first newline: the model got one
    line of the prompt and never saw the topic (the banana incident, part 2).
    Multiline prompts must travel via stdin."""
    seen = {}

    class FakeProc:
        returncode = 0
        stdout = GOOD_REPLY
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["input"] = cmd, kwargs.get("input")
        return FakeProc()

    monkeypatch.setattr(ws.shutil, "which", lambda _: "claude.cmd")
    monkeypatch.setattr(ws.subprocess, "run", fake_run)
    prompt = "line one\nline two\nline three"
    ws.ask_claude(prompt)
    assert seen["input"] == prompt
    assert all("\n" not in part for part in seen["cmd"])
    assert prompt not in seen["cmd"]


def test_ask_claude_missing_cli_raises(monkeypatch):
    monkeypatch.setattr(ws.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="not found"):
        ws.ask_claude("prompt")


def test_ask_claude_surfaces_stdout_errors(monkeypatch):
    """Auth failures print to stdout with empty stderr - must not be hidden."""

    class FakeProc:
        returncode = 1
        stdout = "Failed to authenticate. API Error: 401"
        stderr = ""

    monkeypatch.setattr(ws.shutil, "which", lambda _: "claude.cmd")
    monkeypatch.setattr(ws.subprocess, "run", lambda *a, **k: FakeProc())
    with pytest.raises(RuntimeError, match="401"):
        ws.ask_claude("prompt")
