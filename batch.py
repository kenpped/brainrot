#!/usr/bin/env python3
"""batch.py -- render every scripts/*.txt that doesn't already have an out mp4.

Already-rendered files are skipped (same stem, non-empty mp4), so a killed
batch resumes where it left off. --rotate-voices assigns voices by the
script's sorted position, so a resume keeps the same voice per script.
Rotation is a weak layer: a script whose front matter pins its own voice
keeps that voice.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple

from brainrot import RATE_RE, load_styles, render

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
    voice: str | None
    skip: bool


def plan_jobs(
    scripts_dir: Path,
    outdir: Path,
    voice: str | None = None,
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
    ap.add_argument("--style", default=None,
                    help="styles.json preset for the whole batch")
    ap.add_argument("--voice", default=None,
                    help="force one voice for every video")
    ap.add_argument("--rotate-voices", action="store_true",
                    help=f"cycle through {len(VOICE_ROTATION)} voices by script order")
    ap.add_argument("--rate", default=None, help="speech speed, e.g. +18%%")
    ap.add_argument("--font", default=None, help="caption font name")
    ap.add_argument("--bg-tag", default=None,
                    help="pick backgrounds from a subfolder, e.g. minecraft")
    ap.add_argument("--seed", type=int, default=None,
                    help="base seed; job i uses seed+i so runs are reproducible")
    args = ap.parse_args(argv)

    if args.rate is not None and not RATE_RE.match(args.rate):
        ap.error(f"--rate must look like +18%% or -5%%, got {args.rate}")

    try:
        styles = load_styles()
        if args.style is not None and args.style not in styles:
            raise ValueError(f"unknown style '{args.style}', "
                             f"available: {', '.join(sorted(styles))}")
        jobs = plan_jobs(args.scripts, args.outdir, voice=args.voice,
                         rotate=args.rotate_voices)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    cli = {k: v for k, v in {
        "style": args.style, "voice": args.voice, "rate": args.rate,
        "font": args.font, "bg_tag": args.bg_tag,
    }.items() if v is not None}

    args.outdir.mkdir(parents=True, exist_ok=True)
    rendered, skipped, failed = 0, 0, []
    for i, job in enumerate(jobs):
        tag = f"[{i + 1}/{len(jobs)}] {job.script.name}"
        if job.skip:
            print(f"{tag} -> already rendered, skipping", flush=True)
            skipped += 1
            continue
        # rotation is weaker than front matter; an explicit --voice is CLI-strong
        weak = {"voice": job.voice} if args.rotate_voices and job.voice else {}
        print(f"{tag} -> {job.out}", flush=True)
        try:
            render(
                job.script, args.bg, job.out,
                seed=None if args.seed is None else args.seed + i,
                cli=cli, weak=weak, styles=styles,
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
