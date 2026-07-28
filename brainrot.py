#!/usr/bin/env python3
"""brainrot.py -- script in, 9:16 narrated video with word-by-word captions out.

Pipeline:
  1. edge-tts reads the script aloud (free Microsoft voices, no API key)
  2. faster-whisper transcribes the voiceover to get word-level timestamps
  3. build_ass() turns those words into pop-in .ass captions
  4. ffmpeg center-crops a random slice of background gameplay to 1080x1920
     and burns captions + voiceover onto it

Customization layers, weakest to strongest:
  built-in defaults < styles.json preset < batch voice rotation
  < front matter in the script file < explicit CLI flags

Front matter: start a script with `key: value` lines and close with `---`:

    voice: en-US-BrianNeural
    rate: +25%
    font: Georgia
    style: hype
    bg: minecraft
    ---
    The actual script text starts here.

Dialogue mode: declare `speakers`, then write "name: line" dialogue. Each
speaker gets their own voice and caption color, with a short pause between
lines:

    speakers: A=en-US-BrianNeural, B=en-US-JennyNeural
    ---
    A: Chat, why is nobody talking about this?
    B: Because nobody wants to admit it works.

Only stdlib is imported at module level so the gate tests run with zero deps
installed; edge-tts and faster-whisper import lazily inside the functions
that need them.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

# ---- knobs -----------------------------------------------------------------
WHISPER_MODEL = "base"        # "tiny" = faster transcription, sloppier timing
LANGUAGE = "en"
OUT_W, OUT_H = 1080, 1920
FPS = 30
DEFAULT_VOICE = "en-US-ChristopherNeural"
DEFAULT_RATE = "+18%"         # brainrot pacing lives at +15% to +30%
DEFAULT_FONT = "Impact"
TAIL_PAD = 0.4                # seconds of background kept after the voice ends
MAX_WORD_HOLD = 0.6           # how long a word may hang on screen into a pause
LAST_WORD_HOLD = 0.3
MIN_WORD_DUR = 0.08
POP_MS = 60                   # pop-in animation length in milliseconds
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
RATE_RE = re.compile(r"^[+-]\d+%$")
PITCH_RE = re.compile(r"^[+-]\d+Hz$")
STYLES_FILE = Path(__file__).resolve().parent / "styles.json"
CHARACTERS_FILE = Path(__file__).resolve().parent / "characters.json"

# voice effects: ffmpeg audio filter chains applied AFTER TTS. Captions are
# built from the clean audio (whisper hears normal speech); the effected
# audio is what lands in the video. Chains must preserve duration so the
# word timings still line up. 24000 = edge-tts output sample rate.
VOICE_FX = {
    "none": "",
    "radio": "highpass=f=400,lowpass=f=3000,volume=1.3",
    "phone": "highpass=f=300,lowpass=f=3400",
    "megaphone": "highpass=f=600,lowpass=f=2600,acrusher=bits=12:mode=log:aa=1,volume=1.4",
    "robot": ("afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)'"
              ":win_size=512:overlap=0.75"),
    "demon": "asetrate=24000*0.82,aresample=24000,atempo=1.2195,volume=1.2",
    "chipmunk": "asetrate=24000*1.28,aresample=24000,atempo=0.78125",
    "echo": "aecho=0.8:0.6:40:0.22",
}

# ASS colors are &HBBGGRR& (blue-green-red, not RGB)
COLORS = {
    "white": "&HFFFFFF&",
    "yellow": "&H00FFFF&",
    "lime": "&H00FF00&",
    "red": "&H0000FF&",
    "cyan": "&HFFFF00&",
    "pink": "&HFF00FF&",
    "orange": "&H00A5FF&",
}

DEFAULTS = {
    "voice": DEFAULT_VOICE,
    "rate": DEFAULT_RATE,
    "pitch": "+0Hz",
    "fx": "none",
    "font": DEFAULT_FONT,
    "fontsize": 130,
    "outline": 9,
    "margin_v": 760,
    "captions": "phrase",              # phrase = few words shown, spoken word lit
    "highlight": ["yellow", "lime"],   # word mode: random accents / phrase mode:
    "highlight_chance": 0.22,          #   first color is the active-word light
}
PHRASE_MAX_WORDS = 4
PHRASE_GAP_BREAK = 0.6
STYLE_KEYS = set(DEFAULTS)
FRONT_KEYS = STYLE_KEYS | {"style", "bg", "speakers", "cast", "overlay"}
OVERLAY_Y = 96                 # px from the top for the overlay card
DIALOGUE_GAP = 0.35            # silence between dialogue lines, seconds
SPEAKER_PALETTE = ["white", "yellow", "cyan", "lime", "pink"]  # caption color per speaker


_TOOL_CACHE: dict[str, str] = {}


def _discover_tool(tool: str) -> str:
    """Find ffmpeg/ffprobe: env override, then PATH, then the winget install
    (a terminal opened before `winget install Gyan.FFmpeg` has a stale PATH)."""
    env = os.environ.get(f"{tool.upper()}_BIN")
    if env:
        return env
    if tool in _TOOL_CACHE:
        return _TOOL_CACHE[tool]
    path = shutil.which(tool)
    if path is None and sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        hits = sorted(base.glob(f"Gyan.FFmpeg*/*/bin/{tool}.exe")) if base.exists() else []
        if hits:
            path = str(hits[-1])
    _TOOL_CACHE[tool] = path or tool
    return _TOOL_CACHE[tool]


def ffmpeg_bin() -> str:
    return _discover_tool("ffmpeg")


def ffprobe_bin() -> str:
    return _discover_tool("ffprobe")


# ---- styles and script parsing (gate-tested, no deps) ----------------------

def validate_style(name: str, s: dict) -> None:
    unknown = set(s) - STYLE_KEYS
    if unknown:
        raise ValueError(f"style '{name}': unknown keys {sorted(unknown)}, "
                         f"allowed: {sorted(STYLE_KEYS)}")
    if "rate" in s and not RATE_RE.match(str(s["rate"])):
        raise ValueError(f"style '{name}': rate must look like +18%, got {s['rate']!r}")
    for k in ("fontsize", "outline", "margin_v"):
        if k in s and (isinstance(s[k], bool) or not isinstance(s[k], int) or s[k] < 0):
            raise ValueError(f"style '{name}': {k} must be a non-negative integer")
    if "highlight" in s:
        bad = [c for c in s["highlight"] if c not in COLORS]
        if bad:
            raise ValueError(f"style '{name}': unknown colors {bad}, "
                             f"pick from {sorted(COLORS)}")
    if "highlight_chance" in s and not 0 <= float(s["highlight_chance"]) <= 1:
        raise ValueError(f"style '{name}': highlight_chance must be 0..1")
    if "pitch" in s and not PITCH_RE.match(str(s["pitch"])):
        raise ValueError(f"style '{name}': pitch must look like -15Hz or +20Hz, "
                         f"got {s['pitch']!r}")
    if "fx" in s and s["fx"] not in VOICE_FX:
        raise ValueError(f"style '{name}': fx must be one of {sorted(VOICE_FX)}")
    if "captions" in s and s["captions"] not in ("word", "phrase"):
        raise ValueError(f"style '{name}': captions must be 'word' or 'phrase'")


def load_styles(path: Path | None = None) -> dict:
    p = Path(path) if path else STYLES_FILE
    styles = json.loads(p.read_text(encoding="utf-8"))
    for name, s in styles.items():
        validate_style(name, s)
    if "default" not in styles:
        raise ValueError(f"{p} must define a 'default' style")
    return styles


def validate_character(name: str, c: dict) -> None:
    allowed = {"voice", "pitch", "rate_bump", "color", "persona", "fx"}
    unknown = set(c) - allowed
    if unknown:
        raise ValueError(f"character '{name}': unknown keys {sorted(unknown)}")
    if not c.get("voice"):
        raise ValueError(f"character '{name}': voice is required")
    if not c.get("persona"):
        raise ValueError(f"character '{name}': persona is required "
                         "(the script writer speaks in it)")
    if "pitch" in c and not PITCH_RE.match(str(c["pitch"])):
        raise ValueError(f"character '{name}': pitch must look like -15Hz or +20Hz")
    if "rate_bump" in c and (isinstance(c["rate_bump"], bool)
                             or not isinstance(c["rate_bump"], int)
                             or not -40 <= c["rate_bump"] <= 40):
        raise ValueError(f"character '{name}': rate_bump must be an int in -40..40")
    if "color" in c and c["color"] not in COLORS:
        raise ValueError(f"character '{name}': color must be one of {sorted(COLORS)}")
    if "fx" in c and c["fx"] not in VOICE_FX:
        raise ValueError(f"character '{name}': fx must be one of {sorted(VOICE_FX)}")


def load_characters(path: Path | None = None) -> dict:
    p = Path(path) if path else CHARACTERS_FILE
    characters = json.loads(p.read_text(encoding="utf-8"))
    for name, c in characters.items():
        validate_character(name, c)
    return characters


def bump_rate(rate: str, bump: int) -> str:
    """'+28%' bumped by -8 -> '+20%'. Clamped so characters can't leave the
    range edge-tts accepts."""
    value = max(-40, min(90, int(rate.rstrip("%")) + bump))
    return f"{value:+d}%"


def _parse_speakers(value: str) -> dict[str, str]:
    """'A=en-US-BrianNeural, B=en-US-JennyNeural' -> {'a': ..., 'b': ...}"""
    speakers = {}
    for pair in value.split(","):
        if "=" not in pair:
            raise ValueError(f"speakers entry {pair.strip()!r} must be name=voice")
        name, voice = pair.split("=", 1)
        speakers[name.strip().lower()] = voice.strip()
    if len(speakers) < 2:
        raise ValueError("dialogue needs at least 2 speakers (name=voice, name=voice)")
    return speakers


def _convert_front_value(key: str, value: str):
    try:
        if key in ("fontsize", "outline", "margin_v"):
            return int(value)
        if key == "highlight_chance":
            return float(value)
    except ValueError:
        raise ValueError(f"front matter {key}: {value!r} is not a number") from None
    if key == "highlight":
        if value.lower() in ("none", "off", ""):
            return []
        return [c.strip().lower() for c in value.split(",")]
    if key == "speakers":
        return _parse_speakers(value)
    if key == "cast":
        names = [n.strip().lower() for n in value.split(",") if n.strip()]
        if len(names) < 2:
            raise ValueError("cast needs at least 2 characters, e.g. cast: grump, hype")
        return names
    return value


def parse_script(path: Path) -> tuple[str, dict]:
    """Script file -> (text to narrate, front matter overrides).

    Front matter is optional: the file starts with `key: value` lines (first
    key must be a known one, so prose like "Fun fact: ..." never triggers it)
    and is closed by a line that is exactly `---`.
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"script not found: {path}")
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    first = next((l.strip() for l in lines if l.strip()), "")
    m = re.match(r"^(\w+)\s*:\s*(.*)$", first)
    if not (m and m.group(1).lower() in FRONT_KEYS):
        text = raw.strip()
        if not text:
            raise ValueError(f"script is empty: {path}")
        return text, {}

    meta, end = {}, None
    for i, line in enumerate(lines):
        t = line.strip()
        if not t:
            continue
        if t == "---":
            end = i
            break
        km = re.match(r"^(\w+)\s*:\s*(.*)$", t)
        if not km:
            raise ValueError(f"{path}: bad front matter line {t!r} "
                             "(need 'key: value', close the block with ---)")
        key = km.group(1).lower()
        if key not in FRONT_KEYS:
            raise ValueError(f"{path}: unknown front matter key '{key}', "
                             f"allowed: {sorted(FRONT_KEYS)}")
        meta[key] = _convert_front_value(key, km.group(2).strip())
    if end is None:
        raise ValueError(f"{path}: front matter never closed with a --- line")
    text = "\n".join(lines[end + 1:]).strip()
    if not text:
        raise ValueError(f"script is empty after front matter: {path}")
    return text, meta


def load_script(path: Path) -> str:
    return parse_script(path)[0]


def parse_dialogue(text: str, speakers: dict[str, str]) -> list[tuple[str, str]]:
    """Dialogue body -> [(speaker, line)]. Every line starts with a declared
    'name:' tag; a line that starts with an UNdeclared tag is an error (typo
    protection); a plain line with no tag continues the previous line."""
    lines: list[list[str]] = []
    for raw in text.splitlines():
        t = raw.strip()
        if not t:
            continue
        m = re.match(r"^(\w+)\s*:\s*(.+)$", t)
        if m:
            name = m.group(1).lower()
            if name not in speakers:
                raise ValueError(
                    f"line starts with unknown speaker '{m.group(1)}' "
                    f"(declared: {', '.join(speakers)})")
            lines.append([name, m.group(2).strip()])
        elif lines:
            lines[-1][1] += " " + t
        else:
            raise ValueError(f"dialogue must start with 'name: line', got {t!r}")
    if len({name for name, _ in lines}) < 2:
        raise ValueError("dialogue uses fewer than 2 of the declared speakers")
    return [(name, line) for name, line in lines]


def speaker_spans(durations: list[tuple[str, float]], gap: float = DIALOGUE_GAP,
                  ) -> list[tuple[float, float, str]]:
    """Per-line (speaker, seconds) -> [(start, end, speaker)] on the final
    audio timeline. Each span absorbs the silence gap after its line; the last
    span is open-ended so trailing whisper drift still maps to a speaker."""
    spans, t = [], 0.0
    for i, (name, dur) in enumerate(durations):
        end = float("inf") if i == len(durations) - 1 else t + dur + gap
        spans.append((t, end, name))
        t += dur + gap
    return spans


def speaker_at(spans: list[tuple[float, float, str]], time: float) -> str:
    for start, end, name in spans:
        if time < end:
            return name
    return spans[-1][2]


def speaker_color_tags(names: list[str],
                       speakers_cfg: dict | None = None) -> dict[str, str]:
    """Caption color per speaker. Characters bring their own color; raw
    speakers fall back to the palette in declaration order (first = plain
    white style, rest get \\c overrides)."""
    tags = {}
    for i, name in enumerate(names):
        color = (speakers_cfg or {}).get(name, {}).get("color") \
            or SPEAKER_PALETTE[i % len(SPEAKER_PALETTE)]
        tags[name] = "" if color == "white" else "{\\c" + COLORS[color] + "}"
    return tags


def dialogue_line_specs(
    lines: list[tuple[str, str]],
    speakers_cfg: dict,
    base_rate: str,
) -> list[tuple[str, str, str, str, str]]:
    """Per line: (text, voice, rate, pitch, fx). Characters bend the base
    rate with rate_bump and bring their own pitch and voice effect; raw
    speakers use defaults."""
    specs = []
    for name, text in lines:
        c = speakers_cfg[name]
        specs.append((
            text,
            c["voice"],
            bump_rate(base_rate, c.get("rate_bump", 0)),
            c.get("pitch", "+0Hz"),
            c.get("fx", "none"),
        ))
    return specs


def resolve_settings(
    styles: dict,
    cli: dict | None = None,
    weak: dict | None = None,
    front: dict | None = None,
) -> tuple[dict, str | None, str]:
    """Merge customization layers -> (settings, bg tag, style name).

    Precedence, weakest first: DEFAULTS < style preset < weak (batch voice
    rotation) < script front matter < explicit CLI flags.
    """
    cli, weak, front = dict(cli or {}), dict(weak or {}), dict(front or {})
    style_name = cli.get("style") or front.get("style") or "default"
    if style_name not in styles:
        raise ValueError(f"unknown style '{style_name}', "
                         f"available: {', '.join(sorted(styles))}")
    bg_tag = cli.get("bg_tag") or front.get("bg")

    settings = dict(DEFAULTS)
    settings.update(styles[style_name])
    for layer in (weak, front, cli):
        for k, v in layer.items():
            if k in STYLE_KEYS and v is not None:
                settings[k] = v
    validate_style("resolved settings", settings)
    return settings, bg_tag, style_name


# ---- caption building (gate-tested, no deps) -------------------------------

def ass_time(t: float) -> str:
    """Seconds -> ASS timestamp H:MM:SS.cc (centiseconds)."""
    cs = max(0, int(round(t * 100)))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def sanitize_word(word: str) -> str:
    """Caption-safe display form of a whisper word: uppercase, no ASS syntax,
    trailing commas/periods dropped (?, ! kept -- they read as emphasis)."""
    w = word.strip().upper().rstrip(".,")
    return w.replace("\\", "/").replace("{", "(").replace("}", ")")


def word_events(
    words: list[tuple[str, float, float]],
    max_hold: float = MAX_WORD_HOLD,
    min_dur: float = MIN_WORD_DUR,
    last_hold: float = LAST_WORD_HOLD,
) -> list[tuple[float, float, str]]:
    """(text, start, end) whisper words -> non-overlapping caption events.

    Each word holds on screen until the next word starts, capped at max_hold
    so long pauses go blank instead of freezing a stale word. Overlapping
    timings from sloppy transcription are clipped to the next word's start.
    """
    events = []
    for i, (text, start, end) in enumerate(words):
        start = max(0.0, start)
        end = max(end, start + min_dur)
        if i + 1 < len(words):
            next_start = words[i + 1][1]
            if next_start >= end:
                end = min(next_start, end + max_hold)
            else:
                end = max(start + 0.01, next_start)
        else:
            end += last_hold
        events.append((start, end, text))
    return events


def highlight_tag(index: int, word: str, colors: list[str], chance: float) -> str:
    """Deterministic per-word accent color. Same word at the same position
    always gets the same color, so renders are reproducible."""
    if not colors or chance <= 0:
        return ""
    h = zlib.crc32(f"{index}:{word}".encode("utf-8"))
    if (h % 1000) / 1000 >= chance:
        return ""
    return "{\\c" + COLORS[colors[(h // 1000) % len(colors)]] + "}"


def phrase_chunks(
    display: list[tuple[float, float, str, str]],
    spans: list[tuple[float, float, str]] | None = None,
    max_words: int = PHRASE_MAX_WORDS,
    gap_break: float = PHRASE_GAP_BREAK,
) -> list[list[tuple[float, float, str, str]]]:
    """Group display words [(start, end, shown, raw)] into caption phrases.
    A phrase breaks at max_words, at a long silence, after sentence
    punctuation, and never spans two dialogue speakers."""
    chunks: list[list] = []
    current: list = []
    for item in display:
        if current:
            prev = current[-1]
            speaker_changed = spans is not None and (
                speaker_at(spans, prev[0]) != speaker_at(spans, item[0]))
            if (len(current) >= max_words
                    or item[0] - prev[1] > gap_break
                    or prev[3].rstrip().endswith((".", "?", "!", ","))
                    or speaker_changed):
                chunks.append(current)
                current = []
        current.append(item)
    if current:
        chunks.append(current)
    return chunks


def _phrase_events(display, s, spans, speaker_tags) -> list[str]:
    """One Dialogue event per word, each showing the whole phrase with the
    spoken word lit. Identical text + centered layout means the phrase holds
    perfectly still while the light moves."""
    lit_color = s["highlight"][0] if s["highlight"] else "yellow"
    white = "{\\c&HFFFFFF&}"
    out = []
    for chunk in phrase_chunks(display, spans):
        lit = "{\\c" + COLORS[lit_color] + "}"
        if spans and speaker_tags:
            tag = speaker_tags.get(speaker_at(spans, chunk[0][0]), "")
            if tag:
                lit = tag
        for i in range(len(chunk)):
            seg_start = chunk[i][0]
            seg_end = chunk[i + 1][0] if i + 1 < len(chunk) else chunk[-1][1]
            text = " ".join(
                (lit + word[2] + white) if j == i else word[2]
                for j, word in enumerate(chunk))
            out.append(f"Dialogue: 0,{ass_time(seg_start)},"
                       f"{ass_time(seg_end)},Pop,,0,0,0,,{text}")
    return out


def build_ass(
    words: list[tuple[str, float, float]],
    style: dict | None = None,
    spans: list[tuple[float, float, str]] | None = None,
    speaker_tags: dict[str, str] | None = None,
) -> str:
    """Captions in two modes. phrase (default): a few words on screen with
    the spoken one lit, TikTok style. word: one popping word at a time.
    Centered, bottom-anchored margin_v px up from the 1080x1920 frame bottom.

    In dialogue mode (spans + speaker_tags given) the lit word (phrase mode)
    or every word (word mode) takes the speaker's color."""
    s = dict(DEFAULTS)
    s.update(style or {})
    style_line = (
        f"Style: Pop,{s['font']},{s['fontsize']},&H00FFFFFF,&H00FFFFFF,&H00000000,"
        f"&H00000000,-1,0,0,0,100,100,0,0,1,{s['outline']},0,2,60,60,{s['margin_v']},1"
    )
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {OUT_W}",
        f"PlayResY: {OUT_H}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        style_line,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text",
    ]
    display = []
    for start, end, raw in word_events(words):
        shown = sanitize_word(raw)
        if shown:
            display.append((start, end, shown, raw))

    if s["captions"] == "phrase":
        lines.extend(_phrase_events(display, s, spans, speaker_tags))
        return "\n".join(lines) + "\n"

    pop = f"{{\\fscx70\\fscy70\\t(0,{POP_MS},\\fscx100\\fscy100)}}"
    for i, (start, end, shown, _raw) in enumerate(display):
        if spans and speaker_tags:
            color = speaker_tags.get(speaker_at(spans, start), "")
        else:
            color = highlight_tag(i, shown, s["highlight"], s["highlight_chance"])
        lines.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Pop,,0,0,0,,{pop}{color}{shown}"
        )
    return "\n".join(lines) + "\n"


# ---- ffmpeg plumbing (gate-tested, no deps) --------------------------------

def ffmpeg_filter_path(path: Path) -> str:
    """Path -> form the ffmpeg filtergraph parser accepts on any OS
    (forward slashes, escaped drive colon)."""
    s = str(path).replace("\\", "/")
    if "'" in s:
        raise ValueError(f"path contains a quote, ffmpeg filter can't take it: {s}")
    return s.replace(":", "\\:")


def build_filter(ass_path: Path, with_overlay: bool = False) -> str:
    """Center-crop to 9:16, scale to 1080x1920, burn the captions.

    Lanczos + contrast-adaptive sharpening make low-res gameplay upscale as
    well as upscaling can: sharper edges, less mush. Captions are burned
    after sharpening so the text stays clean. With an overlay (input 2, e.g.
    a Reddit post card PNG) the chain becomes a filter_complex that pins it
    centered near the top."""
    chain = (
        f"crop=min(iw\\,ih*{OUT_W}/{OUT_H}):min(ih\\,iw*{OUT_H}/{OUT_W}),"
        f"scale={OUT_W}:{OUT_H}:flags=lanczos,cas=0.7,setsar=1,"
        f"ass='{ffmpeg_filter_path(ass_path)}'"
    )
    if with_overlay:
        return (f"[0:v]{chain}[v];"
                f"[v][2:v]overlay=(main_w-overlay_w)/2:{OVERLAY_Y}[vo]")
    return chain


def resolve_overlay(spec: str, script_path: Path) -> Path:
    """Overlay paths resolve next to the script first (generated cards live
    beside their scripts), then project root, then as given."""
    for base in (Path(script_path).parent, Path(__file__).resolve().parent):
        candidate = base / spec
        if candidate.is_file():
            return candidate
    p = Path(spec)
    if p.is_file():
        return p
    raise ValueError(f"overlay not found: {spec}")


def upscale_note(width: int, height: int) -> str:
    """A plain warning when background footage is too small to look good.
    The cropped 9:16 column is what actually gets scaled to 1080 wide."""
    cropped_w = min(width, height * OUT_W / OUT_H)
    factor = OUT_W / cropped_w
    if factor >= 2:
        return (f"warning: background is {width}x{height}, ~{factor:.1f}x "
                f"upscale - output will look soft, use 720p+ clips")
    return ""


def list_backgrounds(bg: Path, tag: str | None = None) -> list[Path]:
    """All videos under bg (recursive). A tag narrows to the bg/<tag>/
    subfolder, which is how `bg: minecraft` front matter picks its footage."""
    bg = Path(bg)
    if bg.is_file():
        if tag:
            raise ValueError(f"bg tag '{tag}' needs --bg to be a folder, "
                             f"got a file: {bg}")
        return [bg]
    if not bg.is_dir():
        raise ValueError(f"background not found: {bg}")
    root = bg / tag if tag else bg
    if not root.is_dir():
        subs = sorted(p.name for p in bg.iterdir() if p.is_dir()) or ["(none)"]
        raise ValueError(f"bg tag '{tag}': no folder {root} "
                         f"(existing tags: {', '.join(subs)})")
    vids = sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    )
    if not vids:
        raise ValueError(f"no video files ({', '.join(sorted(VIDEO_EXTS))}) in {root}")
    return vids


def choose_clip(bg_dur: float, need: float, rng: random.Random) -> tuple[float, bool]:
    """Random start offset into the background. If the background is shorter
    than the video we need, start at 0 and let ffmpeg loop it."""
    if bg_dur > need:
        return rng.uniform(0.0, bg_dur - need), False
    return 0.0, True


def installed_fonts() -> set[str] | None:
    """Installed font display names on Windows, None elsewhere (no check)."""
    if sys.platform != "win32":
        return None
    import winreg
    names: set[str] = set()
    key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, key_path) as key:
                for i in range(winreg.QueryInfoKey(key)[1]):
                    names.add(winreg.EnumValue(key, i)[0])
        except OSError:
            pass
    return names


def warn_if_font_missing(font: str) -> None:
    fonts = installed_fonts()
    if fonts is None:
        return
    if not any(n.lower().startswith(font.lower()) for n in fonts):
        print(f"warning: font '{font}' is not installed - "
              "ffmpeg will silently substitute another font", flush=True)


# ---- subprocess-backed pieces ----------------------------------------------

def run_checked(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}"
        )
    return proc.stdout


def apply_voice_fx(in_path: Path, fx: str, out_path: Path) -> Path:
    """Run one VOICE_FX chain over an audio file. 'none' is a no-op that
    returns the input path untouched."""
    chain = VOICE_FX[fx]
    if not chain:
        return in_path
    run_checked([
        ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(in_path), "-af", chain,
        "-ar", "24000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "48k",
        str(out_path),
    ])
    return out_path


def probe_duration(path: Path) -> float:
    out = run_checked([
        ffprobe_bin(), "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0", str(path),
    ])
    return float(out.strip())


def probe_size(path: Path) -> tuple[int, int]:
    out = run_checked([
        ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0", str(path),
    ])
    width, height = out.strip().splitlines()[0].split("x")[:2]
    return int(width), int(height)


def synth_voiceover(text: str, voice: str, rate: str, mp3_path: Path,
                    pitch: str = "+0Hz") -> None:
    import asyncio

    import edge_tts

    async def _run():
        await edge_tts.Communicate(
            text, voice, rate=rate, pitch=pitch).save(str(mp3_path))

    asyncio.run(_run())


def _concat_parts(paths: list[Path], silence: Path, list_file: Path,
                  out_wav: Path) -> None:
    entries = []
    for i, p in enumerate(paths):
        entries.append(f"file '{p.as_posix()}'")
        if i < len(paths) - 1:
            entries.append(f"file '{silence.as_posix()}'")
    list_file.write_text("\n".join(entries) + "\n", encoding="utf-8")
    run_checked([
        ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-ar", "24000", "-ac", "1", str(out_wav),
    ])


def synth_dialogue(
    lines: list[tuple[str, str]],
    speakers_cfg: dict,
    rate: str,
    workdir: Path,
    out_clean: Path,
    out_fx: Path,
) -> tuple[list[tuple[str, float]], bool]:
    """Synth each dialogue line with its speaker's voice/pitch/speed, join
    with short silence gaps. Builds TWO tracks: clean (whisper transcribes
    this, keeping captions accurate) and effected (what the video plays).
    Returns (per-line (speaker, seconds), whether any fx was applied)."""
    specs = dialogue_line_specs(lines, speakers_cfg, rate)
    parts, fx_parts = [], []
    for i, ((name, _line), (text, voice, line_rate, pitch, fx)) in enumerate(
            zip(lines, specs)):
        p = workdir / f"part_{i:03d}.mp3"
        synth_voiceover(text, voice, line_rate, p, pitch=pitch)
        parts.append((name, p, probe_duration(p)))
        fx_parts.append(apply_voice_fx(p, fx, workdir / f"part_{i:03d}_fx.mp3"))

    silence = workdir / "gap.mp3"
    run_checked([
        ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
        "-t", f"{DIALOGUE_GAP}", "-c:a", "libmp3lame", "-ar", "24000",
        "-b:a", "48k", str(silence),
    ])
    _concat_parts([p for _n, p, _d in parts], silence,
                  workdir / "concat.txt", out_clean)
    fx_used = any(f != p for f, (_n, p, _d) in zip(fx_parts, parts))
    if fx_used:
        _concat_parts(fx_parts, silence, workdir / "concat_fx.txt", out_fx)
    return [(name, dur) for name, _p, dur in parts], fx_used


def transcribe_words(mp3_path: Path) -> list[tuple[str, float, float]]:
    from faster_whisper import WhisperModel

    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(
        str(mp3_path), language=LANGUAGE, word_timestamps=True
    )
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append((text, float(w.start), float(w.end)))
    if not words:
        raise RuntimeError("whisper produced no words - is the voiceover silent?")
    return words


# ---- orchestration ---------------------------------------------------------

def render(
    script_path: Path,
    bg: Path,
    out: Path,
    *,
    seed: int | None = None,
    keep_temp: bool = False,
    cli: dict | None = None,
    weak: dict | None = None,
    styles: dict | None = None,
) -> Path:
    if shutil.which(ffmpeg_bin()) is None and not Path(ffmpeg_bin()).is_file():
        raise RuntimeError(
            "ffmpeg not found. Install it (winget install Gyan.FFmpeg) or set FFMPEG_BIN."
        )
    styles = styles if styles is not None else load_styles()
    text, front = parse_script(Path(script_path))
    s, bg_tag, style_name = resolve_settings(styles, cli=cli, weak=weak, front=front)

    # resolve the dialogue cast/speakers up front - fail fast, before slow steps
    speakers_cfg = None
    if front.get("cast"):
        characters = load_characters()
        unknown = [n for n in front["cast"] if n not in characters]
        if unknown:
            raise ValueError(f"unknown cast member(s) {unknown}, "
                             f"available: {', '.join(sorted(characters))}")
        speakers_cfg = {n: characters[n] for n in front["cast"]}
    elif front.get("speakers"):
        speakers_cfg = {n: {"voice": v} for n, v in front["speakers"].items()}
    dialogue = parse_dialogue(text, speakers_cfg) if speakers_cfg else None

    overlay_spec = (cli or {}).get("overlay") or front.get("overlay")
    overlay_path = resolve_overlay(overlay_spec, script_path) if overlay_spec else None

    backgrounds = list_backgrounds(Path(bg), tag=bg_tag)
    warn_if_font_missing(s["font"])
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if keep_temp:
        workdir = out.parent / f"{out.stem}_temp"
        workdir.mkdir(parents=True, exist_ok=True)
        cleanup = None
    else:
        cleanup = tempfile.TemporaryDirectory(prefix="brainrot_")
        workdir = Path(cleanup.name)

    try:
        if dialogue:
            voice_path = workdir / "voice.wav"          # clean: whisper hears this
            voice_fx_path = workdir / "voice_fx.wav"    # effected: video plays this
            durations, fx_used = synth_dialogue(
                dialogue, speakers_cfg, s["rate"], workdir,
                voice_path, voice_fx_path)
            mux_path = voice_fx_path if fx_used else voice_path
            spans = speaker_spans(durations)
            speaker_tags = speaker_color_tags(list(speakers_cfg), speakers_cfg)
            audio_dur = probe_duration(voice_path)
            who = ", ".join(
                f"{n}={c['voice']}" + (f"@{c['pitch']}" if c.get("pitch") else "")
                + (f"+{c['fx']}" if c.get("fx", "none") != "none" else "")
                for n, c in speakers_cfg.items())
            print(f"[1/4] voiceover  dialogue {len(dialogue)} lines, {who} "
                  f"{s['rate']} -> {audio_dur:.1f}s", flush=True)
        else:
            spans = speaker_tags = None
            voice_path = workdir / "voice.mp3"
            synth_voiceover(text, s["voice"], s["rate"], voice_path,
                            pitch=s["pitch"])
            mux_path = apply_voice_fx(voice_path, s["fx"], workdir / "voice_fx.mp3")
            audio_dur = probe_duration(voice_path)
            fx_note = f" fx {s['fx']}" if s["fx"] != "none" else ""
            print(f"[1/4] voiceover  {s['voice']} {s['rate']}{fx_note} "
                  f"(style {style_name}) -> {audio_dur:.1f}s", flush=True)

        words = transcribe_words(voice_path)
        ass_path = workdir / "captions.ass"
        ass_path.write_text(build_ass(words, s, spans, speaker_tags), encoding="utf-8")
        print(f"[2/4] captions   {len(words)} words, font {s['font']} "
              f"(whisper {WHISPER_MODEL})", flush=True)

        rng = random.Random(seed)
        bg_file = rng.choice(backgrounds)
        bg_dur = probe_duration(bg_file)
        bg_w, bg_h = probe_size(bg_file)
        need = audio_dur + TAIL_PAD
        offset, loop = choose_clip(bg_dur, need, rng)
        print(
            f"[3/4] background {bg_file.name}"
            f"{f' [{bg_tag}]' if bg_tag else ''} @ {offset:.1f}s"
            f"{' (looped)' if loop else ''}",
            flush=True,
        )
        note = upscale_note(bg_w, bg_h)
        if note:
            print(note, flush=True)

        cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y"]
        if loop:
            cmd += ["-stream_loop", "-1"]
        cmd += [
            "-ss", f"{offset:.3f}", "-i", str(bg_file),
            "-i", str(mux_path),
        ]
        if overlay_path:
            cmd += ["-loop", "1", "-i", str(overlay_path)]
        cmd += ["-t", f"{need:.3f}"]
        if overlay_path:
            cmd += ["-filter_complex", build_filter(ass_path, with_overlay=True),
                    "-map", "[vo]", "-map", "1:a:0"]
        else:
            cmd += ["-vf", build_filter(ass_path),
                    "-map", "0:v:0", "-map", "1:a:0"]
        cmd += [
            "-af", "apad",
            "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(out),
        ]
        run_checked(cmd)
        print(f"[4/4] ffmpeg     {out} ({need:.1f}s, {OUT_W}x{OUT_H})", flush=True)
        return out
    finally:
        if cleanup is not None:
            cleanup.cleanup()


def print_styles(styles: dict) -> None:
    print(f"{'style':<12} {'voice':<28} {'rate':<6} {'font':<10} highlights")
    for name in sorted(styles):
        s = dict(DEFAULTS)
        s.update(styles[name])
        hl = ",".join(s["highlight"]) if s["highlight"] else "none"
        print(f"{name:<12} {s['voice']:<28} {s['rate']:<6} {s['font']:<10} "
              f"{hl} @ {s['highlight_chance']:.0%}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--list-styles" in argv:
        print_styles(load_styles())
        return 0

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--script", required=True, type=Path, help="text file to narrate")
    ap.add_argument("--bg", required=True, type=Path,
                    help="background video file, or a folder of them")
    ap.add_argument("--out", required=True, type=Path, help="output .mp4 path")
    ap.add_argument("--style", default=None,
                    help="preset from styles.json (--list-styles to see them)")
    ap.add_argument("--voice", default=None,
                    help="edge-tts voice (python voices.py to browse)")
    ap.add_argument("--rate", default=None, help="speech speed, e.g. +18%%")
    ap.add_argument("--font", default=None, help="caption font name")
    ap.add_argument("--bg-tag", default=None,
                    help="pick backgrounds from a subfolder, e.g. minecraft")
    ap.add_argument("--overlay", default=None,
                    help="PNG pinned top-center (e.g. a Reddit post card)")
    ap.add_argument("--seed", type=int, default=None,
                    help="fix the random background/offset choice")
    ap.add_argument("--keep-temp", action="store_true",
                    help="keep the mp3 and .ass next to the output for inspection")
    args = ap.parse_args(argv)

    if args.rate is not None and not RATE_RE.match(args.rate):
        ap.error(f"--rate must look like +18%% or -5%%, got {args.rate}")

    cli = {k: v for k, v in {
        "style": args.style, "voice": args.voice, "rate": args.rate,
        "font": args.font, "bg_tag": args.bg_tag, "overlay": args.overlay,
    }.items() if v is not None}

    try:
        render(args.script, args.bg, args.out, seed=args.seed,
               keep_temp=args.keep_temp, cli=cli)
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
