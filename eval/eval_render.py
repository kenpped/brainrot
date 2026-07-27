#!/usr/bin/env python3
"""End-to-end eval: real edge-tts + whisper + ffmpeg render, checked with ffprobe.

Slow lane. Needs network (edge-tts, first-run whisper model download) and
ffmpeg. Not part of the pre-commit gate -- run before ship and after touching
the render path:

    python eval/eval_render.py

Renders a short script over a synthetic horizontal background (exercises the
9:16 center-crop and the random offset), then checks:
  - output exists, is 1080x1920, has an audio stream
  - duration matches voiceover + tail pad within 1.0s
  - captions.ass has at least MIN_EVENTS word events
  - at least WORD_MATCH_MIN of the script's words appear in the captions
    (whisper transcription quality threshold)

Exit 0 = PASS, 1 = FAIL. Output lands in out/eval_sample.mp4 so you can watch it.
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

SCRIPT_TEXT = (
    "Your brain is not broken. It is just full of dopamine from tiny videos "
    "like this one. Thirty seconds ago you were doing something else."
)
BG_SECONDS = 90
MIN_EVENTS = 10
WORD_MATCH_MIN = 0.60
DURATION_TOL = 1.0
SEED = 7


def tokens(text: str) -> list[str]:
    return re.findall(r"[A-Z']+", text.upper())


def probe_streams(path: Path) -> dict:
    out = br.run_checked([
        br.ffprobe_bin(), "-v", "error",
        "-show_streams", "-show_format", "-of", "json", str(path),
    ])
    return json.loads(out)


def main() -> int:
    if shutil.which(br.ffmpeg_bin()) is None and not Path(br.ffmpeg_bin()).is_file():
        print("FAIL: ffmpeg not found (install it or set FFMPEG_BIN)")
        return 1

    outdir = ROOT / "out"
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
        "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30",
        "-t", str(BG_SECONDS), "-pix_fmt", "yuv420p", str(bg),
    ])

    t0 = time.monotonic()
    br.render(script_file, bg, out, seed=SEED, keep_temp=True)
    wall = time.monotonic() - t0

    checks = []

    def check(name, ok, detail):
        checks.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name:<12} {detail}")

    info = probe_streams(out)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)

    check("exists", out.exists() and out.stat().st_size > 0,
          f"{out} ({out.stat().st_size / 1e6:.1f} MB)")
    check("resolution",
          video is not None and (video["width"], video["height"]) == (br.OUT_W, br.OUT_H),
          f"{video['width']}x{video['height']} (want {br.OUT_W}x{br.OUT_H})")
    check("audio", audio is not None,
          f"codec {audio['codec_name'] if audio else 'none'}")

    voice_dur = br.probe_duration(temp / "voice.mp3")
    out_dur = float(info["format"]["duration"])
    want = voice_dur + br.TAIL_PAD
    check("duration", abs(out_dur - want) <= DURATION_TOL,
          f"{out_dur:.2f}s vs voice {voice_dur:.2f}+{br.TAIL_PAD} "
          f"(delta {abs(out_dur - want):.2f} <= {DURATION_TOL})")

    ass_text = (temp / "captions.ass").read_text(encoding="utf-8")
    dialogues = [l for l in ass_text.splitlines() if l.startswith("Dialogue:")]
    check("captions", len(dialogues) >= MIN_EVENTS,
          f"{len(dialogues)} word events (>= {MIN_EVENTS})")

    caption_words = set()
    for line in dialogues:
        caption_words.update(tokens(re.sub(r"\{[^}]*\}", "", line.split(",", 9)[9])))
    script_words = tokens(SCRIPT_TEXT)
    matched = sum(1 for w in script_words if w in caption_words)
    ratio = matched / len(script_words)
    check("words match", ratio >= WORD_MATCH_MIN,
          f"{ratio:.0%} of script words in captions (>= {WORD_MATCH_MIN:.0%})")

    verdict = all(checks)
    print(f"\nverdict: {'PASS' if verdict else 'FAIL'}  "
          f"({sum(checks)}/{len(checks)} checks, render {wall:.0f}s wall)")
    print(f"watch it: {out}")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
