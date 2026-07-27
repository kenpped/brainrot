#!/usr/bin/env python3
"""brainrot.py -- script in, 9:16 narrated video with word-by-word captions out.

Pipeline:
  1. edge-tts reads the script aloud (free Microsoft voices, no API key)
  2. faster-whisper transcribes the voiceover to get word-level timestamps
  3. build_ass() turns those words into pop-in .ass captions
  4. ffmpeg center-crops a random slice of background gameplay to 1080x1920
     and burns captions + voiceover onto it

Only stdlib is imported at module level so the gate tests run with zero deps
installed; edge-tts and faster-whisper import lazily inside the functions
that need them.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
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


def ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG_BIN", "ffmpeg")


def ffprobe_bin() -> str:
    return os.environ.get("FFPROBE_BIN", "ffprobe")


# ---- pure helpers (gate-tested, no deps) -----------------------------------

def load_script(path: Path) -> str:
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"script is empty: {path}")
    return text


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


def build_ass(
    words: list[tuple[str, float, float]],
    font: str = DEFAULT_FONT,
    fontsize: int = 130,
    outline: int = 9,
    margin_v: int = 760,
) -> str:
    """Word-by-word pop captions. One Dialogue event per word, centered,
    bottom-anchored margin_v px up from the bottom of the 1080x1920 frame."""
    style = (
        f"Style: Pop,{font},{fontsize},&H00FFFFFF,&H00FFFFFF,&H00000000,"
        f"&H00000000,-1,0,0,0,100,100,0,0,1,{outline},0,2,60,60,{margin_v},1"
    )
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {OUT_W}",
        f"PlayResY: {OUT_H}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        style,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text",
    ]
    pop = f"{{\\fscx70\\fscy70\\t(0,{POP_MS},\\fscx100\\fscy100)}}"
    for start, end, text in word_events(words):
        shown = sanitize_word(text)
        if not shown:
            continue
        lines.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Pop,,0,0,0,,{pop}{shown}"
        )
    return "\n".join(lines) + "\n"


def ffmpeg_filter_path(path: Path) -> str:
    """Path -> form the ffmpeg filtergraph parser accepts on any OS
    (forward slashes, escaped drive colon)."""
    s = str(path).replace("\\", "/")
    if "'" in s:
        raise ValueError(f"path contains a quote, ffmpeg filter can't take it: {s}")
    return s.replace(":", "\\:")


def build_filter(ass_path: Path) -> str:
    """Center-crop to 9:16, scale to 1080x1920, burn the captions."""
    return (
        f"crop=min(iw\\,ih*{OUT_W}/{OUT_H}):min(ih\\,iw*{OUT_H}/{OUT_W}),"
        f"scale={OUT_W}:{OUT_H},setsar=1,"
        f"ass='{ffmpeg_filter_path(ass_path)}'"
    )


def list_backgrounds(bg: Path) -> list[Path]:
    bg = Path(bg)
    if bg.is_file():
        return [bg]
    if bg.is_dir():
        vids = sorted(
            p for p in bg.iterdir()
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS
        )
        if vids:
            return vids
        raise ValueError(f"no video files ({', '.join(sorted(VIDEO_EXTS))}) in {bg}")
    raise ValueError(f"background not found: {bg}")


def choose_clip(bg_dur: float, need: float, rng: random.Random) -> tuple[float, bool]:
    """Random start offset into the background. If the background is shorter
    than the video we need, start at 0 and let ffmpeg loop it."""
    if bg_dur > need:
        return rng.uniform(0.0, bg_dur - need), False
    return 0.0, True


# ---- subprocess-backed pieces ----------------------------------------------

def run_checked(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}"
        )
    return proc.stdout


def probe_duration(path: Path) -> float:
    out = run_checked([
        ffprobe_bin(), "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0", str(path),
    ])
    return float(out.strip())


def synth_voiceover(text: str, voice: str, rate: str, mp3_path: Path) -> None:
    import asyncio

    import edge_tts

    async def _run():
        await edge_tts.Communicate(text, voice, rate=rate).save(str(mp3_path))

    asyncio.run(_run())


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
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    font: str = DEFAULT_FONT,
    seed: int | None = None,
    keep_temp: bool = False,
) -> Path:
    if shutil.which(ffmpeg_bin()) is None and not Path(ffmpeg_bin()).is_file():
        raise RuntimeError(
            "ffmpeg not found. Install it (winget install Gyan.FFmpeg) or set FFMPEG_BIN."
        )
    text = load_script(Path(script_path))
    backgrounds = list_backgrounds(Path(bg))  # fail fast, before the slow TTS/whisper steps
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
        mp3 = workdir / "voice.mp3"
        synth_voiceover(text, voice, rate, mp3)
        audio_dur = probe_duration(mp3)
        print(f"[1/4] voiceover  {voice} {rate} -> {audio_dur:.1f}s", flush=True)

        words = transcribe_words(mp3)
        ass_path = workdir / "captions.ass"
        ass_path.write_text(build_ass(words, font=font), encoding="utf-8")
        print(f"[2/4] captions   {len(words)} words (whisper {WHISPER_MODEL})", flush=True)

        rng = random.Random(seed)
        bg_file = rng.choice(backgrounds)
        bg_dur = probe_duration(bg_file)
        need = audio_dur + TAIL_PAD
        offset, loop = choose_clip(bg_dur, need, rng)
        print(
            f"[3/4] background {bg_file.name} @ {offset:.1f}s"
            f"{' (looped)' if loop else ''}",
            flush=True,
        )

        cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y"]
        if loop:
            cmd += ["-stream_loop", "-1"]
        cmd += [
            "-ss", f"{offset:.3f}", "-i", str(bg_file),
            "-i", str(mp3),
            "-t", f"{need:.3f}",
            "-vf", build_filter(ass_path),
            "-map", "0:v:0", "-map", "1:a:0",
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--script", required=True, type=Path, help="text file to narrate")
    ap.add_argument("--bg", required=True, type=Path,
                    help="background video file, or a folder of them")
    ap.add_argument("--out", required=True, type=Path, help="output .mp4 path")
    ap.add_argument("--voice", default=DEFAULT_VOICE,
                    help="edge-tts voice (edge-tts --list-voices)")
    ap.add_argument("--rate", default=DEFAULT_RATE, help="speech speed, e.g. +18%%")
    ap.add_argument("--font", default=DEFAULT_FONT, help="caption font name")
    ap.add_argument("--seed", type=int, default=None,
                    help="fix the random background/offset choice")
    ap.add_argument("--keep-temp", action="store_true",
                    help="keep the mp3 and .ass next to the output for inspection")
    args = ap.parse_args(argv)

    if not RATE_RE.match(args.rate):
        ap.error(f"--rate must look like +18%% or -5%%, got {args.rate}")

    try:
        render(
            args.script, args.bg, args.out,
            voice=args.voice, rate=args.rate, font=args.font,
            seed=args.seed, keep_temp=args.keep_temp,
        )
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
