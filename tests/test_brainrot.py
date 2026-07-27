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


# ---- styles ----------------------------------------------------------------

def test_shipped_styles_file_is_valid():
    styles = br.load_styles()  # reads the real styles.json -- catches typos
    assert "default" in styles
    assert len(styles) >= 4


def test_validate_style_rejects_unknown_key():
    with pytest.raises(ValueError, match="unknown keys"):
        br.validate_style("x", {"speed": "+18%"})


def test_validate_style_rejects_bad_rate_and_color():
    with pytest.raises(ValueError, match="rate"):
        br.validate_style("x", {"rate": "fast"})
    with pytest.raises(ValueError, match="colors"):
        br.validate_style("x", {"highlight": ["chartreuse"]})


def test_load_styles_requires_default(tmp_path):
    f = tmp_path / "styles.json"
    f.write_text('{"hype": {"rate": "+28%"}}', encoding="utf-8")
    with pytest.raises(ValueError, match="default"):
        br.load_styles(f)


# ---- parse_script ----------------------------------------------------------

def test_parse_script_plain_text(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("  hello world \n", encoding="utf-8")
    assert br.parse_script(f) == ("hello world", {})


def test_parse_script_prose_colon_is_not_front_matter(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("note: this whole line is narration\nmore text", encoding="utf-8")
    text, meta = br.parse_script(f)
    assert meta == {}
    assert text.startswith("note:")


def test_parse_script_front_matter(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text(
        "voice: en-US-BrianNeural\n"
        "rate: +25%\n"
        "fontsize: 140\n"
        "highlight: yellow, red\n"
        "bg: minecraft\n"
        "---\n"
        "the actual script\n",
        encoding="utf-8",
    )
    text, meta = br.parse_script(f)
    assert text == "the actual script"
    assert meta == {
        "voice": "en-US-BrianNeural", "rate": "+25%", "fontsize": 140,
        "highlight": ["yellow", "red"], "bg": "minecraft",
    }


def test_parse_script_highlight_none(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("highlight: none\n---\nbody", encoding="utf-8")
    assert br.parse_script(f)[1] == {"highlight": []}


def test_parse_script_unknown_key_raises(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("voice: x\nspeed: +25%\n---\nbody", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown front matter key 'speed'"):
        br.parse_script(f)


def test_parse_script_unclosed_front_matter_raises(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("voice: x\nrate: +20%", encoding="utf-8")
    with pytest.raises(ValueError, match="never closed"):
        br.parse_script(f)


def test_parse_script_bad_number_raises(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("fontsize: huge\n---\nbody", encoding="utf-8")
    with pytest.raises(ValueError, match="not a number"):
        br.parse_script(f)


def test_parse_script_empty_raises(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("   \n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        br.parse_script(f)
    f.write_text("voice: x\n---\n  \n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        br.parse_script(f)


# ---- dialogue --------------------------------------------------------------

SPEAKERS = {"a": "en-US-BrianNeural", "b": "en-US-JennyNeural"}


def test_parse_speakers_front_matter(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("speakers: A=en-US-BrianNeural, B=en-US-JennyNeural\n---\n"
                 "A: hi\nB: hey", encoding="utf-8")
    _, meta = br.parse_script(f)
    assert meta["speakers"] == SPEAKERS


def test_parse_speakers_rejects_bad_entries():
    with pytest.raises(ValueError, match="name=voice"):
        br._parse_speakers("A en-US-BrianNeural")
    with pytest.raises(ValueError, match="at least 2"):
        br._parse_speakers("A=en-US-BrianNeural")


def test_parse_dialogue_basic_and_continuation():
    text = "A: first line\nstill the first line\nB: reply\nA: closer"
    lines = br.parse_dialogue(text, SPEAKERS)
    assert lines == [
        ("a", "first line still the first line"),
        ("b", "reply"),
        ("a", "closer"),
    ]


def test_parse_dialogue_unknown_speaker_raises():
    with pytest.raises(ValueError, match="unknown speaker 'C'"):
        br.parse_dialogue("A: hi\nC: who am i", SPEAKERS)


def test_parse_dialogue_must_start_with_tag():
    with pytest.raises(ValueError, match="must start with"):
        br.parse_dialogue("no tag here\nA: hi", SPEAKERS)


def test_parse_dialogue_needs_two_speakers_used():
    with pytest.raises(ValueError, match="fewer than 2"):
        br.parse_dialogue("A: hi\nA: still me", SPEAKERS)


def test_speaker_spans_and_lookup():
    spans = br.speaker_spans([("a", 2.0), ("b", 3.0), ("a", 1.0)], gap=0.5)
    assert spans[0] == (0.0, 2.5, "a")
    assert spans[1] == (2.5, 6.0, "b")
    assert spans[2][0] == 6.0 and spans[2][1] == float("inf")
    assert br.speaker_at(spans, 0.1) == "a"
    assert br.speaker_at(spans, 2.4) == "a"   # gap belongs to previous line
    assert br.speaker_at(spans, 3.0) == "b"
    assert br.speaker_at(spans, 99.0) == "a"  # trailing drift -> last speaker


def test_speaker_color_tags_first_is_white():
    tags = br.speaker_color_tags(["a", "b", "c"])
    assert tags["a"] == ""
    assert tags["b"] == "{\\c" + br.COLORS["yellow"] + "}"
    assert tags["c"] == "{\\c" + br.COLORS["cyan"] + "}"


def test_build_ass_dialogue_colors_beat_highlights():
    words = [("Your", 0.0, 0.2), ("brain", 2.6, 2.9)]
    spans = br.speaker_spans([("a", 2.0), ("b", 3.0)], gap=0.5)
    tags = br.speaker_color_tags(["a", "b"])
    ass = br.build_ass(words, {"highlight": ["red"], "highlight_chance": 1.0},
                       spans, tags)
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert br.COLORS["red"] not in dialogues[0] and br.COLORS["red"] not in dialogues[1]
    assert "\\c&H" not in dialogues[0]              # speaker a stays white
    assert "{\\c" + br.COLORS["yellow"] + "}" in dialogues[1]  # speaker b yellow


# ---- resolve_settings ------------------------------------------------------

STYLES = {"default": {}, "hype": {"voice": "V-style", "rate": "+28%"}}


def test_resolve_defaults():
    s, tag, name = br.resolve_settings(STYLES)
    assert name == "default"
    assert tag is None
    assert s["voice"] == br.DEFAULT_VOICE
    assert s["rate"] == br.DEFAULT_RATE


def test_resolve_front_style_applies():
    s, _, name = br.resolve_settings(STYLES, front={"style": "hype"})
    assert (name, s["voice"], s["rate"]) == ("hype", "V-style", "+28%")


def test_resolve_precedence_cli_beats_front_beats_weak_beats_style():
    s, _, _ = br.resolve_settings(
        STYLES,
        cli={"voice": "V-cli"},
        weak={"voice": "V-weak"},
        front={"style": "hype", "voice": "V-front"},
    )
    assert s["voice"] == "V-cli"
    without_cli, _, _ = br.resolve_settings(
        STYLES, weak={"voice": "V-weak"}, front={"style": "hype", "voice": "V-front"})
    assert without_cli["voice"] == "V-front"
    weak_only, _, _ = br.resolve_settings(
        STYLES, weak={"voice": "V-weak"}, front={"style": "hype"})
    assert weak_only["voice"] == "V-weak"


def test_resolve_none_values_are_ignored():
    s, _, _ = br.resolve_settings(STYLES, cli={"voice": None}, weak={"rate": None})
    assert s["voice"] == br.DEFAULT_VOICE
    assert s["rate"] == br.DEFAULT_RATE


def test_resolve_bg_tag_cli_beats_front():
    _, tag, _ = br.resolve_settings(STYLES, cli={"bg_tag": "gta"}, front={"bg": "minecraft"})
    assert tag == "gta"
    _, tag, _ = br.resolve_settings(STYLES, front={"bg": "minecraft"})
    assert tag == "minecraft"


def test_resolve_unknown_style_raises():
    with pytest.raises(ValueError, match="unknown style"):
        br.resolve_settings(STYLES, cli={"style": "nope"})


def test_resolve_validates_merged_result():
    with pytest.raises(ValueError, match="rate"):
        br.resolve_settings(STYLES, front={"rate": "fast"})


# ---- highlight -------------------------------------------------------------

def test_highlight_deterministic():
    a = br.highlight_tag(3, "SCAM", ["yellow", "red"], 1.0)
    b = br.highlight_tag(3, "SCAM", ["yellow", "red"], 1.0)
    assert a == b != ""


def test_highlight_off_when_disabled():
    assert br.highlight_tag(3, "SCAM", [], 1.0) == ""
    assert br.highlight_tag(3, "SCAM", ["yellow"], 0.0) == ""


def test_highlight_always_on_at_chance_one():
    tags = {br.highlight_tag(i, "WORD", ["yellow", "red"], 1.0) for i in range(50)}
    assert "" not in tags
    assert all(t.startswith("{\\c&H") for t in tags)


def test_highlight_rate_roughly_matches_chance():
    hits = sum(bool(br.highlight_tag(i, f"W{i}", ["yellow"], 0.25)) for i in range(400))
    assert 60 <= hits <= 140  # 25% of 400 = 100, generous band


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


def test_build_ass_custom_style():
    ass = br.build_ass(WORDS, {"font": "Anton", "fontsize": 150})
    assert "Style: Pop,Anton,150," in ass


def test_build_ass_one_dialogue_per_word():
    ass = br.build_ass(WORDS)
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogues) == len(WORDS)
    assert dialogues[0].endswith("YOUR")
    assert dialogues[2].endswith("LIES")  # trailing period dropped


def test_build_ass_highlights_forced_on():
    ass = br.build_ass(WORDS, {"highlight": ["red"], "highlight_chance": 1.0})
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert all("{\\c&H0000FF&}" in l for l in dialogues)


def test_build_ass_highlights_off():
    ass = br.build_ass(WORDS, {"highlight": [], "highlight_chance": 0.0})
    assert "\\c&H" not in ass


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


def test_list_backgrounds_dir_filters_and_recurses(tmp_path):
    (tmp_path / "b.mp4").write_bytes(b"x")
    (tmp_path / "minecraft").mkdir()
    (tmp_path / "minecraft" / "a.MP4").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("no")
    got = br.list_backgrounds(tmp_path)
    assert {p.name for p in got} == {"a.MP4", "b.mp4"}


def test_list_backgrounds_tag_narrows(tmp_path):
    (tmp_path / "minecraft").mkdir()
    (tmp_path / "gta").mkdir()
    (tmp_path / "minecraft" / "mc.mp4").write_bytes(b"x")
    (tmp_path / "gta" / "car.mp4").write_bytes(b"x")
    got = br.list_backgrounds(tmp_path, tag="minecraft")
    assert [p.name for p in got] == ["mc.mp4"]


def test_list_backgrounds_missing_tag_names_existing(tmp_path):
    (tmp_path / "minecraft").mkdir()
    (tmp_path / "minecraft" / "mc.mp4").write_bytes(b"x")
    with pytest.raises(ValueError, match="existing tags: minecraft"):
        br.list_backgrounds(tmp_path, tag="subway")


def test_list_backgrounds_tag_with_file_raises(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    with pytest.raises(ValueError, match="needs --bg to be a folder"):
        br.list_backgrounds(f, tag="minecraft")


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


# ---- fonts -----------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="registry check is Windows-only")
def test_installed_fonts_sees_impact():
    fonts = br.installed_fonts()
    assert any(n.lower().startswith("impact") for n in fonts)


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
