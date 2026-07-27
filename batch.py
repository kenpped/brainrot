#!/usr/bin/env python3
"""batch.py -- render every scripts/*.txt that doesn't already have an out mp4.

Already-rendered files are skipped (same stem, non-empty mp4), so a killed
batch resumes where it left off. --rotate-voices assigns voices by the
script's sorted position, so a resume keeps the same voice per script.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple

from brainrot import DEFAULT_FONT, DEFAULT_RATE, DEFAULT_VOICE, RATE_RE, render

VOICE_ROTATION = [
    "en-US-ChristopherNeural",
    "en-US-GuyNeural",
    "en-US-AndrewNeural",
    "en-US-BrianNeural",
    "en-US-EricNeural",
]


class Job(NamedTuple):
    script: Path
    out: Path
    voice: str
    skip: bool


def plan_jobs(
    scripts_dir: Path,
    outdir: Path,
    voice: str = DEFAULT_VOICE,
    rotate: bool = False,
) -> list[Job]:
    scripts = sorted(Path(scripts_dir).glob("*.txt"))
    if not scripts:
        raise ValueError(f"no .txt scripts in {scripts_dir}")
    jobs = []
    for i, script in enumerate(scripts):
        out = Path(outdir) / f"{script.stem}.mp4"
        v = VOICE_ROTATION[i % len(VOICE_ROTATION)] if rotate else voice
        skip = out.exists() and out.stat().st_size > 0
        jobs.append(Job(script, out, v, skip))
    return jobs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scripts", required=True, type=Path, help="folder of .txt scripts")
    ap.add_argument("--bg", required=True, type=Path,
                    help="background video file, or a folder of them")
    ap.add_argument("--outdir", required=True, type=Path, help="output folder")
    ap.add_argument("--voice", default=DEFAULT_VOICE,
                    help="voice for every video (unless --rotate-voices)")
    ap.add_argument("--rotate-voices", action="store_true",
                    help=f"cycle through {len(VOICE_ROTATION)} voices by script order")
    ap.add_argument("--rate", default=DEFAULT_RATE, help="speech speed, e.g. +18%%")
    ap.add_argument("--font", default=DEFAULT_FONT, help="caption font name")
    ap.add_argument("--seed", type=int, default=None,
                    help="base seed; job i uses seed+i so runs are reproducible")
    args = ap.parse_args(argv)

    if not RATE_RE.match(args.rate):
        ap.error(f"--rate must look like +18%% or -5%%, got {args.rate}")

    try:
        jobs = plan_jobs(args.scripts, args.outdir, voice=args.voice,
                         rotate=args.rotate_voices)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    args.outdir.mkdir(parents=True, exist_ok=True)
    rendered, skipped, failed = 0, 0, []
    for i, job in enumerate(jobs):
        tag = f"[{i + 1}/{len(jobs)}] {job.script.name}"
        if job.skip:
            print(f"{tag} -> already rendered, skipping", flush=True)
            skipped += 1
            continue
        print(f"{tag} -> {job.out} ({job.voice})", flush=True)
        try:
            render(
                job.script, args.bg, job.out,
                voice=job.voice, rate=args.rate, font=args.font,
                seed=None if args.seed is None else args.seed + i,
            )
            rendered += 1
        except (ValueError, RuntimeError) as e:
            print(f"{tag} FAILED: {e}", file=sys.stderr, flush=True)
            failed.append(job.script.name)

    print(f"done: {rendered} rendered, {skipped} skipped, {len(failed)} failed", flush=True)
    if failed:
        print("failed: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
