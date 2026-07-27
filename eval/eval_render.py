#!/usr/bin/env python3
"""End-to-end eval: real edge-tts + whisper + ffmpeg renders, checked with ffprobe.

Slow lane. Needs network (edge-tts, first-run whisper model download) and
ffmpeg. Not part of the pre-commit gate -- run before ship and after touching
the render path:

    python eval/eval_render.py

Three phases:
  A. default render over a synthetic horizontal background (exercises the
     9:16 center-crop + random offset): resolution, audio, duration,
     caption count, and whisper word-match thresholds
  B. front-matter render: voice/rate/font/highlight overrides written in the
     script file itself must reach the .ass output
  C. make_bg generator: an encoded balls clip must probe at the right
     size and duration
  D. dialogue render: two voices concatenated with gaps, captions colored
     per speaker
  E. every styles.json voice actually exists on edge-tts (a typo'd voice
     name would otherwise only fail the first time that preset is used)

Exit 0 = PASS, 1 = FAIL. Outputs land in out/ so you can watch them.
"""

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brainrot as br
import make_bg

SCRIPT_TEXT = (
    "Your brain is not broken. It is just full of dopamine from tiny videos "
    "like this one. Thirty seconds ago you were doing something else."
)
STYLED_SCRIPT = (
    "voice: en-US-JennyNeural\n"
    "rate: +24%\n"
    "font: Georgia\n"
    "highlight: cyan,pink\n"
    "highlight_chance: 0.5\n"
    "---\n"
    "Styles work. This voice, this font, and this speed all came from the "
    "script file itself.\n"
)
DIALOGUE_SCRIPT = (
    "cast: grump, hype\n"
    "---\n"
    "grump: Wait, this thing does characters now?\n"
    "hype: Two personalities, two pitches, and my words show up in yellow!\n"
    "grump: Prove it, kid.\n"
    "hype: You are watching the proof right now!\n"
)
BG_SECONDS = 90
MIN_EVENTS = 10
WORD_MATCH_MIN = 0.60
DURATION_TOL = 1.0
SEED = 7

checks = []


def check(name, ok, detail):
    checks.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:<16} {detail}")


def tokens(text: str) -> list[str]:
    return re.findall(r"[A-Z']+", text.upper())


def probe_streams(path: Path) -> dict:
    out = br.run_checked([
        br.ffprobe_bin(), "-v", "error",
        "-show_streams", "-show_format", "-of", "json", str(path),
    ])
    return json.loads(out)


def video_stream(info):
    return next((s for s in info["streams"] if s["codec_type"] == "video"), None)


def main() -> int:
    if shutil.which(br.ffmpeg_bin()) is None and not Path(br.ffmpeg_bin()).is_file():
        print("FAIL: ffmpeg not found (install it or set FFMPEG_BIN)")
        return 1

    outdir = ROOT / "out"
    t0 = time.monotonic()

    # ---- phase A: default render over synthetic horizontal background ------
    out = outdir / "eval_sample.mp4"
    temp = outdir / "eval_sample_temp"   # render(keep_temp=True) uses this name
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True, exist_ok=True)
    script_file = temp / "eval_script.txt"
    script_file.write_text(SCRIPT_TEXT, encoding="utf-8")
    bg = temp / "bg.mp4"
    br.run_checked([
        br.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
        "-t", str(BG_SECONDS), "-pix_fmt", "yuv420p", str(bg),
    ])
    br.render(script_file, bg, out, seed=SEED, keep_temp=True)

    info = probe_streams(out)
    video = video_stream(info)
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    check("A exists", out.exists() and out.stat().st_size > 0,
          f"{out} ({out.stat().st_size / 1e6:.1f} MB)")
    check("A resolution",
          video is not None and (video["width"], video["height"]) == (br.OUT_W, br.OUT_H),
          f"{video['width']}x{video['height']} (want {br.OUT_W}x{br.OUT_H})")
    check("A audio", audio is not None,
          f"codec {audio['codec_name'] if audio else 'none'}")
    voice_dur = br.probe_duration(temp / "voice.mp3")
    out_dur = float(info["format"]["duration"])
    want = voice_dur + br.TAIL_PAD
    check("A duration", abs(out_dur - want) <= DURATION_TOL,
          f"{out_dur:.2f}s vs voice {voice_dur:.2f}+{br.TAIL_PAD} "
          f"(delta {abs(out_dur - want):.2f} <= {DURATION_TOL})")
    ass_text = (temp / "captions.ass").read_text(encoding="utf-8")
    dialogues = [l for l in ass_text.splitlines() if l.startswith("Dialogue:")]
    check("A captions", len(dialogues) >= MIN_EVENTS,
          f"{len(dialogues)} word events (>= {MIN_EVENTS})")
    caption_words = set()
    for line in dialogues:
        caption_words.update(tokens(re.sub(r"\{[^}]*\}", "", line.split(",", 9)[9])))
    script_words = tokens(SCRIPT_TEXT)
    matched = sum(1 for w in script_words if w in caption_words)
    ratio = matched / len(script_words)
    check("A words match", ratio >= WORD_MATCH_MIN,
          f"{ratio:.0%} of script words in captions (>= {WORD_MATCH_MIN:.0%})")

    # ---- phase B: front matter overrides reach the output ------------------
    out_b = outdir / "eval_style.mp4"
    temp_b = outdir / "eval_style_temp"
    if temp_b.exists():
        shutil.rmtree(temp_b)
    temp_b.mkdir(parents=True, exist_ok=True)
    styled_file = temp_b / "styled_script.txt"
    styled_file.write_text(STYLED_SCRIPT, encoding="utf-8")
    br.render(styled_file, bg, out_b, seed=SEED, keep_temp=True)

    ass_b = (temp_b / "captions.ass").read_text(encoding="utf-8")
    check("B font override", "Style: Pop,Georgia," in ass_b,
          "front matter font Georgia is in the .ass style line")
    check("B highlights", "\\c&H" in ass_b,
          "highlight color tags present in captions")
    info_b = probe_streams(out_b)
    vb = video_stream(info_b)
    check("B resolution",
          vb is not None and (vb["width"], vb["height"]) == (br.OUT_W, br.OUT_H),
          f"{vb['width']}x{vb['height']}")

    # ---- phase C: make_bg generator encodes a valid clip -------------------
    bg_c = temp_b / "gen_balls.mp4"
    frames = make_bg.gen_balls(6.0, 24, 360, 640, seed=3)
    make_bg.write_video(bg_c, frames, 24, 360, 640, int(6 * 24), "eval balls")
    info_c = probe_streams(bg_c)
    vc = video_stream(info_c)
    dur_c = float(info_c["format"]["duration"])
    check("C make_bg", vc is not None and (vc["width"], vc["height"]) == (360, 640)
          and abs(dur_c - 6.0) <= 0.5,
          f"{vc['width']}x{vc['height']}, {dur_c:.2f}s (want 360x640, ~6s)")

    # ---- phase D: dialogue mode --------------------------------------------
    out_d = outdir / "eval_dialogue.mp4"
    temp_d = outdir / "eval_dialogue_temp"
    if temp_d.exists():
        shutil.rmtree(temp_d)
    temp_d.mkdir(parents=True, exist_ok=True)
    dialogue_file = temp_d / "dialogue_script.txt"
    dialogue_file.write_text(DIALOGUE_SCRIPT, encoding="utf-8")
    br.render(dialogue_file, bg, out_d, seed=SEED, keep_temp=True)

    ass_d = (temp_d / "captions.ass").read_text(encoding="utf-8")
    yellow = "{\\c" + br.COLORS["yellow"] + "}"
    orange = "{\\c" + br.COLORS["orange"] + "}"
    check("D speaker colors", yellow in ass_d and orange in ass_d,
          "hype yellow + grump orange override tags both present")
    wav = temp_d / "voice.wav"
    parts = sorted(temp_d.glob("part_*.mp3"))
    joined = br.probe_duration(wav)
    part_sum = sum(br.probe_duration(p) for p in parts) + br.DIALOGUE_GAP * (len(parts) - 1)
    check("D audio joined", len(parts) == 4 and abs(joined - part_sum) <= 0.3,
          f"{len(parts)} lines -> {joined:.2f}s wav (parts+gaps {part_sum:.2f}s)")
    info_d = probe_streams(out_d)
    vd = video_stream(info_d)
    check("D resolution",
          vd is not None and (vd["width"], vd["height"]) == (br.OUT_W, br.OUT_H),
          f"{vd['width']}x{vd['height']}")

    # ---- phase E: every preset and character voice synthesizes ---------------
    styles = br.load_styles()
    voices = sorted(
        {dict(br.DEFAULTS, **s)["voice"] for s in styles.values()}
        | {c["voice"] for c in br.load_characters().values()})
    bad = []
    for v in voices:
        try:
            br.synth_voiceover("test", v, "+18%", temp_b / "voice_check.mp3")
        except Exception as e:
            bad.append(f"{v} ({type(e).__name__})")
    check("E preset voices", not bad,
          f"{len(voices)} unique voices synthesized"
          + (f", FAILED: {', '.join(bad)}" if bad else ""))

    wall = time.monotonic() - t0
    verdict = all(checks)
    print(f"\nverdict: {'PASS' if verdict else 'FAIL'}  "
          f"({sum(checks)}/{len(checks)} checks, {wall:.0f}s wall)")
    print(f"watch them: {out}, {out_b}, {out_d}")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
