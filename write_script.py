#!/usr/bin/env python3
"""write_script.py -- topic in, ready-to-render script file out.

Uses the LOCAL Claude Code CLI (`claude -p`), not an API key, so it costs
nothing extra on a Claude subscription.

    python write_script.py "why school is a scam"
    python write_script.py "credit card traps" --dialogue
    python write_script.py "gym motivation" --style hype --bg minecraft

The output lands in scripts/<slug>.txt with front matter already filled in,
so the next step is just brainrot.py / batch.py.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from brainrot import parse_script

DEFAULT_VOICE_A = "en-US-BrianNeural"
DEFAULT_VOICE_B = "en-US-JennyNeural"

MONO_PROMPT = """\
Write a short script for a vertical brainrot-style video about: {topic}

Rules:
- 100 to 130 words, spoken prose only
- cold-open hook in the first sentence, no greeting
- short punchy sentences, one idea each
- a re-hook in the middle, something like "here is the wild part"
- end with a twist or a call to action
- no emojis, no hashtags, no stage directions, no markdown, no headings
- output ONLY the script text, nothing else"""

DIALOGUE_PROMPT = """\
Write a short two-speaker dialogue script for a vertical brainrot-style video
about: {topic}

Speakers: A (skeptic, casual, asks what everyone is thinking) and
B (knows the answer, confident, slightly smug).

Rules:
- 110 to 150 words total, 8 to 14 short lines
- every line starts with exactly "A: " or "B: "
- A opens with a hook question or a wild claim
- B explains in punchy plain language; A pushes back at least once
- no emojis, no stage directions, no markdown
- output ONLY the dialogue lines, nothing else"""


def slugify(topic: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "script"


def build_prompt(topic: str, dialogue: bool) -> str:
    template = DIALOGUE_PROMPT if dialogue else MONO_PROMPT
    return template.format(topic=topic)


def strip_fences(text: str) -> str:
    """Models sometimes wrap output in ``` fences despite instructions."""
    lines = [l for l in text.strip().splitlines() if not l.strip().startswith("```")]
    return "\n".join(lines).strip()


def build_front_matter(dialogue: bool, style: str | None, bg: str | None,
                       voice_a: str, voice_b: str) -> str:
    keys = []
    if dialogue:
        keys.append(f"speakers: A={voice_a}, B={voice_b}")
    if style:
        keys.append(f"style: {style}")
    if bg:
        keys.append(f"bg: {bg}")
    return ("\n".join(keys) + "\n---\n") if keys else ""


def ask_claude(prompt: str, timeout: int = 300) -> str:
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=timeout, shell=False,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "claude CLI not found - install Claude Code or write the script by hand"
        ) from None
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed ({proc.returncode}): {proc.stderr.strip()}")
    return strip_fences(proc.stdout)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("topic", help="what the video is about")
    ap.add_argument("--dialogue", action="store_true",
                    help="two-speaker back-and-forth instead of a monologue")
    ap.add_argument("--style", default=None, help="styles.json preset to bake in")
    ap.add_argument("--bg", default=None, help="background tag to bake in, e.g. minecraft")
    ap.add_argument("--voice-a", default=DEFAULT_VOICE_A, help="dialogue speaker A voice")
    ap.add_argument("--voice-b", default=DEFAULT_VOICE_B, help="dialogue speaker B voice")
    ap.add_argument("--outdir", type=Path,
                    default=Path(__file__).resolve().parent / "scripts")
    args = ap.parse_args(argv)

    print(f"asking local Claude for a {'dialogue' if args.dialogue else 'monologue'} "
          f"about: {args.topic}", flush=True)
    try:
        body = ask_claude(build_prompt(args.topic, args.dialogue))
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if len(body.split()) < 40:
        print(f"error: Claude returned suspiciously little text:\n{body}", file=sys.stderr)
        return 1

    front = build_front_matter(args.dialogue, args.style, args.bg,
                               args.voice_a, args.voice_b)
    args.outdir.mkdir(parents=True, exist_ok=True)
    dest = args.outdir / f"{slugify(args.topic)}.txt"
    dest.write_text(front + body + "\n", encoding="utf-8")

    try:
        parse_script(dest)  # same validation the renderer will run
    except ValueError as e:
        print(f"error: generated script failed validation: {e}", file=sys.stderr)
        print(f"raw output kept at {dest} - fix it by hand or rerun", file=sys.stderr)
        return 1
    print(f"wrote {dest} ({len(body.split())} words)")
    print(f"render it: python brainrot.py --script {dest} --bg backgrounds/ "
          f"--out out/{dest.stem}.mp4")
    return 0


if __name__ == "__main__":
    sys.exit(main())
