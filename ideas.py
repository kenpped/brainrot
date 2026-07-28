#!/usr/bin/env python3
"""ideas.py -- pitch short-video ideas via local Claude Code.

    python ideas.py
    python ideas.py --theme "psychology and money traps" --count 8 --json

Feeds the studio's Ideas tab: each idea is a title plus a one-line hook,
ready to hand to write_script / the site's write-with-AI button.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from write_script import GUARD, RETRY_SUFFIX, ask_claude

DEFAULT_THEME = ("educational facts, psychology tricks, money traps, wild "
                 "history, internet culture")

PROMPT = GUARD + """\
Pitch {count} ideas for short vertical brainrot-style videos. Theme mix:
{theme}.

Rules:
- each idea must make someone stop scrolling: a hook, a twist, or a "wait,
  what?" fact
- favor ideas that teach something real in under 60 seconds
- one idea per line, EXACTLY this shape:  TITLE | one-line hook
- titles 4 to 10 words, no numbering, no emojis, no markdown

Output EXACTLY this shape and nothing else:
BEGIN IDEAS
title one | its hook
title two | its hook
END IDEAS"""


def parse_ideas(raw: str) -> list[dict]:
    m = re.search(r"^BEGIN IDEAS\s*$(.*?)^END IDEAS\s*$", raw,
                  re.DOTALL | re.MULTILINE)
    if not m:
        return []
    ideas = []
    for line in m.group(1).strip().splitlines():
        if "|" not in line:
            continue
        title, _, hook = line.partition("|")
        title, hook = title.strip(), hook.strip()
        if 3 <= len(title) <= 90 and hook:
            ideas.append({"title": title, "hook": hook})
    return ideas


def generate_ideas(theme: str, count: int, attempts: int = 2) -> list[dict]:
    prompt = PROMPT.format(count=count, theme=theme)
    for attempt in range(attempts):
        raw = ask_claude(prompt if attempt == 0 else prompt + RETRY_SUFFIX)
        ideas = parse_ideas(raw)
        if len(ideas) >= min(count, 3):
            return ideas[:count]
    raise RuntimeError("Claude kept returning unparseable idea lists")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--theme", default=DEFAULT_THEME)
    ap.add_argument("--count", type=int, default=8)
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output (the studio uses this)")
    args = ap.parse_args(argv)
    try:
        ideas = generate_ideas(args.theme, args.count)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ideas": ideas}))
    else:
        for i, idea in enumerate(ideas, 1):
            print(f"{i}. {idea['title']}\n   {idea['hook']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
