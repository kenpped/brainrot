"""Gate tests for brainrot.py -- pure helpers only, no network, no ffmpeg.

Run: python -m pytest tests -q   (must stay under 2 seconds)
"""

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brainrot as br


# ---- ass_time --------------------------------------------------------------

def test_ass_time_zero():
    assert br.ass_time(0.0) == "0:00:00.00"


def test_ass_time_minutes_and_centis():
    assert br.ass_time(61.234) == "0:01:01.23"


def test_ass_time_hours():
    assert br.ass_time(3600.5) == "1:00:00.50"


def test_ass_time_never_negative():
    assert br.ass_time(-0.5) == "0:00:00.00"


# ---- sanitize_word ---------------------------------------------------------

def test_sanitize_uppercases_and_strips():
    assert br.sanitize_word("  hello ") == "HELLO"


def test_sanitize_drops_trailing_period_and_comma():
    assert br.sanitize_word("scam.") == "SCAM"
    assert br.sanitize_word("brain,") == "BRAIN"


def test_sanitize_keeps_question_and_bang():
    assert br.sanitize_word("what?") == "WHAT?"
    assert br.sanitize_word("go!") == "GO!"


def test_sanitize_neutralizes_ass_syntax():
    assert br.sanitize_word("a{b}c\\d") == "A(B)C/D"


# ---- word_events -----------------------------------------------------------

def test_word_events_stretch_into_small_gap():
    words = [("a", 0.0, 0.2), ("b", 0.5, 0.7)]
    events = br.word_events(words)
    assert events[0] == (0.0, 0.5, "a")  # gap 0.3 <= 0.6 hold cap -> stretch to next


def test_word_events_cap_long_pause():
    words = [("a", 0.0, 0.2), ("b", 2.0, 2.2)]
    events = br.word_events(words)
    assert events[0][1] == pytest.approx(0.2 + br.MAX_WORD_HOLD)


def test_word_events_clip_overlap():
    words = [("a", 0.0, 0.9), ("b", 0.5, 1.0)]
    events = br.word_events(words)
    assert events[0][1] == 0.5  # clipped to next start, no two-word overlap


def test_word_events_min_duration():
    words = [("a", 1.0, 1.0)]
    events = br.word_events(words)
    start, end, _ = events[0]
    assert end >= start + br.MIN_WORD_DUR


def test_word_events_last_word_holds():
    words = [("a", 0.0, 0.5)]
    assert br.word_events(words)[0][1] == pytest.approx(0.5 + br.LAST_WORD_HOLD)


# ---- build_ass -------------------------------------------------------------

WORDS = [("Your", 0.0, 0.2), ("brain", 0.25, 0.5), ("lies.", 0.55, 0.9)]


def test_build_ass_style_line_defaults():
    ass = br.build_ass(WORDS)
    style = next(l for l in ass.splitlines() if l.startswith("Style: Pop,"))
    fields = style.split(",")
    assert fields[1] == "Impact"
    assert fields[2] == "130"
    assert fields[16] == "9"     # Outline
    assert fields[18] == "2"     # Alignment: bottom-center
    assert fields[21] == "760"   # MarginV


def test_build_ass_custom_font():
    assert "Style: Pop,Anton," in br.build_ass(WORDS, font="Anton")


def test_build_ass_one_dialogue_per_word():
    ass = br.build_ass(WORDS)
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogues) == len(WORDS)
    assert dialogues[0].endswith("YOUR")
    assert dialogues[2].endswith("LIES")  # trailing period dropped


def test_build_ass_has_pop_animation_and_playres():
    ass = br.build_ass(WORDS)
    assert f"\\t(0,{br.POP_MS},\\fscx100\\fscy100)" in ass
    assert f"PlayResX: {br.OUT_W}" in ass
    assert f"PlayResY: {br.OUT_H}" in ass


def test_build_ass_times_are_ordered():
    ass = br.build_ass(WORDS)
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    starts = [l.split(",")[1] for l in dialogues]
    assert starts == sorted(starts)


# ---- ffmpeg filter building ------------------------------------------------

def test_ffmpeg_filter_path_windows():
    p = Path(r"C:\Users\ken\my clips\subs.ass")
    assert br.ffmpeg_filter_path(p) == "C\\:/Users/ken/my clips/subs.ass"


def test_ffmpeg_filter_path_rejects_quote():
    with pytest.raises(ValueError):
        br.ffmpeg_filter_path(Path("C:\\ken's\\subs.ass"))


def test_build_filter_crops_scales_burns():
    f = br.build_filter(Path(r"C:\tmp\subs.ass"))
    assert "crop=min(iw\\,ih*1080/1920):min(ih\\,iw*1920/1080)" in f
    assert "scale=1080:1920" in f
    assert f.endswith("ass='C\\:/tmp/subs.ass'")


# ---- backgrounds -----------------------------------------------------------

def test_list_backgrounds_single_file(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    assert br.list_backgrounds(f) == [f]


def test_list_backgrounds_dir_filters_and_sorts(tmp_path):
    (tmp_path / "b.mp4").write_bytes(b"x")
    (tmp_path / "a.MP4").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("no")
    got = br.list_backgrounds(tmp_path)
    assert [p.name for p in got] == ["a.MP4", "b.mp4"]


def test_list_backgrounds_empty_dir_raises(tmp_path):
    with pytest.raises(ValueError):
        br.list_backgrounds(tmp_path)


def test_list_backgrounds_missing_raises(tmp_path):
    with pytest.raises(ValueError):
        br.list_backgrounds(tmp_path / "nope")


# ---- choose_clip -----------------------------------------------------------

def test_choose_clip_offset_in_range_and_deterministic():
    a = br.choose_clip(600.0, 60.0, random.Random(7))
    b = br.choose_clip(600.0, 60.0, random.Random(7))
    assert a == b
    offset, loop = a
    assert 0.0 <= offset <= 540.0
    assert loop is False


def test_choose_clip_short_background_loops():
    offset, loop = br.choose_clip(30.0, 60.0, random.Random(7))
    assert offset == 0.0
    assert loop is True


# ---- script loading --------------------------------------------------------

def test_load_script_strips(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("  hello world \n", encoding="utf-8")
    assert br.load_script(f) == "hello world"


def test_load_script_empty_raises(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("   \n", encoding="utf-8")
    with pytest.raises(ValueError):
        br.load_script(f)


# ---- render fail-fast ------------------------------------------------------

def test_render_validates_background_before_tts(tmp_path, monkeypatch):
    """A bad --bg path must fail instantly, not after 40s of TTS + whisper."""
    script = tmp_path / "s.txt"
    script.write_text("hello", encoding="utf-8")

    def boom(*a, **k):
        raise AssertionError("TTS ran before background validation")

    monkeypatch.setattr(br, "synth_voiceover", boom)
    monkeypatch.setattr(br.shutil, "which", lambda _: "ffmpeg")  # not the check under test
    with pytest.raises(ValueError, match="background not found"):
        br.render(script, tmp_path / "missing.mp4", tmp_path / "o.mp4")


# ---- CLI validation --------------------------------------------------------

def test_rate_regex():
    assert br.RATE_RE.match("+18%")
    assert br.RATE_RE.match("-5%")
    assert not br.RATE_RE.match("18%")
    assert not br.RATE_RE.match("+18")
    assert not br.RATE_RE.match("fast")
