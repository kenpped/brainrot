#!/usr/bin/env python3
"""voices.py -- browse and preview edge-tts voices without rendering a video.

    python voices.py                 # curated list with vibes
    python voices.py --preview      # synth a sample mp3 per curated voice
    python voices.py --preview --text "custom line to hear"

Full catalog (hundreds of voices, all languages):
    edge-tts --list-voices
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from brainrot import synth_voiceover

# (voice, vibe) -- curated for narration; edge-tts has hundreds more
CURATED = [
    ("en-US-ChristopherNeural", "deep, calm authority - the classic reddit-stories voice"),
    ("en-US-GuyNeural",         "newsy, energetic, slightly nasal"),
    ("en-US-AndrewNeural",      "warm podcast bro, very natural"),
    ("en-US-BrianNeural",       "young, casual, sits well at fast rates"),
    ("en-US-EricNeural",        "mid-depth, neutral narrator"),
    ("en-US-RogerNeural",       "older, gravelly documentary tone"),
    ("en-US-SteffanNeural",     "flat and dry, good for deadpan humor"),
    ("en-US-JennyNeural",       "friendly female all-rounder"),
    ("en-US-AriaNeural",        "bright female, upbeat"),
    ("en-US-AvaNeural",         "smooth female, storytime energy"),
    ("en-US-MichelleNeural",    "mature female, measured"),
    ("en-GB-RyanNeural",        "British male, instant class"),
    ("en-GB-SoniaNeural",       "British female, crisp"),
    ("en-AU-WilliamNeural",     "Australian male, laid back"),
]

DEFAULT_SAMPLE = "Your brain is running a scam on you right now, and nobody talks about it."


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--preview", action="store_true",
                    help="synth a sample mp3 per curated voice into out/voice_previews/")
    ap.add_argument("--text", default=DEFAULT_SAMPLE, help="sample line to synthesize")
    ap.add_argument("--rate", default="+18%", help="speech speed for previews")
    args = ap.parse_args(argv)

    if not args.preview:
        width = max(len(v) for v, _ in CURATED)
        for voice, vibe in CURATED:
            print(f"{voice:<{width}}  {vibe}")
        print(f"\nfull catalog: edge-tts --list-voices")
        print(f"hear them:    python voices.py --preview")
        return 0

    outdir = Path(__file__).resolve().parent / "out" / "voice_previews"
    outdir.mkdir(parents=True, exist_ok=True)
    failed = []
    for i, (voice, _vibe) in enumerate(CURATED):
        dest = outdir / f"{voice}.mp3"
        print(f"[{i + 1}/{len(CURATED)}] {voice}", flush=True)
        try:
            synth_voiceover(args.text, voice, args.rate, dest)
        except Exception as e:  # one bad voice must not kill the tour
            print(f"  FAILED: {e}", file=sys.stderr, flush=True)
            failed.append(voice)
    print(f"\nsamples in {outdir}")
    if failed:
        print("failed voices: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
