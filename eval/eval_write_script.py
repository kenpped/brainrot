#!/usr/bin/env python3
"""Eval: real script generation through the local Claude Code CLI.

Slow lane, costs one local claude -p call. Needs `claude login` done and
network up:

    python eval/eval_write_script.py

Checks the hardening that the 2026-07-27 banana incident motivated: the
generated file exists, parses, is a real dialogue, has enough words, and
actually talks about the topic it was asked for.
"""

import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brainrot as br
import write_script as ws

TOPIC = "why credit card minimum payments are a trap"

checks = []


def check(name, ok, detail):
    checks.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:<12} {detail}")


def main() -> int:
    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(ROOT / "write_script.py"), TOPIC, "--dialogue"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=600,
    )
    wall = time.monotonic() - t0
    check("exit code", proc.returncode == 0,
          f"{proc.returncode} ({wall:.0f}s)" +
          ("" if proc.returncode == 0 else f" stderr: {proc.stderr.strip()[:200]}"))
    if proc.returncode != 0:
        print("\nverdict: FAIL")
        return 1

    dest = ROOT / "scripts" / f"{ws.slugify(TOPIC)}.txt"
    check("file", dest.is_file(), str(dest))

    text, meta = br.parse_script(dest)
    check("parses", True, f"front matter keys: {sorted(meta)}")
    check("dialogue", "speakers" in meta and len(meta["speakers"]) == 2,
          f"speakers: {meta.get('speakers')}")
    lines = br.parse_dialogue(text, meta["speakers"])
    check("lines", len(lines) >= 6, f"{len(lines)} dialogue lines (>= 6)")
    words = len(text.split())
    check("words", words >= 40, f"{words} words (>= 40)")
    check("on topic", ws.on_topic(text, TOPIC),
          f"mentions one of {ws.topic_words(TOPIC)}")
    clean = not re.search(r"```|BEGIN SCRIPT|permission|scripts/", text)
    check("no meta", clean, "no fences, sentinels, or file-talk in the body")

    verdict = all(checks)
    print(f"\nverdict: {'PASS' if verdict else 'FAIL'}  ({sum(checks)}/{len(checks)} checks)")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
