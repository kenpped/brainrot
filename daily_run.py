#!/usr/bin/env python3
"""daily_run.py -- the morning batch: N solo-narrator videos, mixed recipe.

    python daily_run.py --count 5

Roughly 60% reddit stories, the rest educational (fresh ideas pitched by
local Claude, deduped against everything already made). Every video gets ONE
narrator PNG, rotating through avatars/ so mornings don't all look the same.
This is exactly what the 8am scheduled task runs.
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from post_card import make_demo_avatar
from write_script import slugify

AVATAR_DIR = ROOT / "avatars"
EDU_DIR = ROOT / "scripts" / "edu"
PY = sys.executable


def split_counts(total: int) -> tuple[int, int]:
    """(stories, educational). 60/40-ish, both at least 1 when total >= 2."""
    if total <= 1:
        return total, 0
    stories = min(total - 1, max(1, round(total * 0.6)))
    return stories, total - stories


def list_avatars(avatar_dir: Path = AVATAR_DIR) -> list[str]:
    pngs = sorted(p.name for p in avatar_dir.glob("*.png"))
    if not pngs:
        make_demo_avatar(avatar_dir / "blob-blue.png")
        pngs = ["blob-blue.png"]
    return pngs


def fresh_ideas(ideas: list[dict], edu_dir: Path, n: int) -> list[dict]:
    """Ideas whose script slug doesn't already exist = never made before."""
    fresh = [i for i in ideas
             if not (edu_dir / f"{slugify(i['title'])}.txt").is_file()]
    return fresh[:n]


def run(cmd: list[str], timeout: int = 900) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    output = (proc.stdout or "") + (proc.stderr or "")
    print(output.strip(), flush=True)
    return proc.returncode, proc.stdout or ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--bg-tag", default=None,
                    help="background folder; default = any folder")
    args = ap.parse_args(argv)

    n_stories, n_edu = split_counts(args.count)
    rotation = itertools.cycle(list_avatars())
    made, failed = 0, 0
    print(f"daily batch: {n_stories} stories + {n_edu} educational", flush=True)

    for i in range(n_stories):
        avatar = next(rotation)
        print(f"--- story {i + 1}/{n_stories} (narrator: {avatar}) ---", flush=True)
        cmd = [PY, str(ROOT / "reddit_stories.py"), "--count", "1",
               "--avatar", avatar]
        if args.bg_tag:
            cmd += ["--bg-tag", args.bg_tag]
        code, _ = run(cmd)
        made += (code == 0)
        failed += (code != 0)
        time.sleep(10)  # let reddit breathe between fetches

    if n_edu:
        print("--- pitching educational topics ---", flush=True)
        code, out = run([PY, str(ROOT / "ideas.py"), "--json"])
        ideas = []
        if code == 0:
            line = next((l for l in out.splitlines()
                         if l.strip().startswith('{"ideas"')), None)
            ideas = json.loads(line)["ideas"] if line else []
        picks = fresh_ideas(ideas, EDU_DIR, n_edu)
        if len(picks) < n_edu:
            print(f"only {len(picks)} fresh ideas available", flush=True)
        for j, idea in enumerate(picks):
            avatar = next(rotation)
            print(f"--- edu {j + 1}/{n_edu}: {idea['title']} "
                  f"(narrator: {avatar}) ---", flush=True)
            code, out = run([PY, str(ROOT / "write_script.py"), idea["title"],
                             "--edu", "--style", "story", "--avatar", avatar,
                             "--outdir", str(EDU_DIR)])
            if code != 0:
                failed += 1
                continue
            wrote = next((l for l in out.splitlines()
                          if l.startswith("wrote ")), None)
            if not wrote:
                failed += 1
                continue
            script = wrote.removeprefix("wrote ").split(" (")[0].strip()
            out_mp4 = ROOT / "out" / f"edu-{Path(script).stem}.mp4"
            cmd = [PY, str(ROOT / "brainrot.py"), "--script", script,
                   "--bg", str(ROOT / "backgrounds"), "--out", str(out_mp4)]
            if args.bg_tag:
                cmd += ["--bg-tag", args.bg_tag]
            code, _ = run(cmd)
            made += (code == 0)
            failed += (code != 0)

    print(f"daily done: {made} made, {failed} failed", flush=True)
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())
