#!/usr/bin/env python3
"""reddit_stories.py -- top Reddit stories in, rendered story videos out.

The daily loop that used to end at a paid generator, now fully local:
fetch top posts from storytelling subreddits, filter with the selection
bar from reddit-stories-daily-prompt.md, retell each through local Claude
as a ~150 word first-person voiceover, render with the brainrot pipeline.

    python reddit_stories.py                       # 3 videos from today's top
    python reddit_stories.py --count 5 --t week --bg-tag minecraft
    python reddit_stories.py --dry-run             # just show what it would pick

A sent-log (scripts/reddit/.used.json) makes reruns skip already-made
stories, same resume behavior as batch.py.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from brainrot import render
from post_card import make_post_card
from write_script import (RETRY_SUFFIX, SENTINEL_BEGIN, SENTINEL_END,
                          ask_claude, extract_script, slugify)

SUBREDDITS = ["AITAH", "AmItheAsshole", "pettyrevenge", "ProRevenge",
              "MaliciousCompliance", "TrueOffMyChest", "EntitledPeople",
              "nuclearrevenge"]
UA = "brainrot-pipeline/1.0 (personal daily story scout)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MIN_WORDS, MAX_WORDS = 400, 900       # candidate story length (his doc's bar)
MIN_SCORE = 1000
RETELL_MIN, RETELL_MAX = 100, 220     # voiceover length after retell
BLOCKLIST = ["suicide", "self-harm", "self harm", "rape", "molest", "abuse",
             "overdose", "incest", "kill myself"]
REDDIT_DIR = ROOT / "scripts" / "reddit"
STATE_FILE = REDDIT_DIR / ".used.json"

RETELL_PROMPT = """\
You are a text generator. Do not read or write files, do not run tools, do
not explain yourself, and do not add anything before or after the block.

Retell this Reddit story as a first-person voiceover script for a vertical
video.

Rules:
- 120 to 170 words
- keep the storyteller's first-person voice and the actual events
- open with the most dramatic moment as the first line, no greeting
- clear arc: setup fast, conflict escalates, land the payoff
- anonymize: no usernames, no subreddit names; ages and relationships fine
- keep it PG-13: soften profanity, skip graphic detail
- end by asking the audience whose side they are on
- no emojis, no markdown, no commentary about the story

THE STORY
Title: {title}

{body}

Output EXACTLY this shape and nothing else:
BEGIN SCRIPT
<the script text>
END SCRIPT"""


# similarity thresholds for repost detection: reworded reposts keep most of
# their 5-word chunks; unrelated stories share almost none
TITLE_SIM = 0.6
BODY_SIM = 0.35
TITLE_STOP = {"aita", "aitah", "wibta", "wibtah", "would", "that", "this",
              "with", "from", "when", "after", "about", "being", "them",
              "they", "have", "because", "refusing", "telling"}


# ---- pure helpers (gate-tested, no network) --------------------------------

def title_tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", title.lower())
            if len(w) > 3 and w not in TITLE_STOP}


def shingles(text: str, n: int = 5, cap: int = 200) -> set[int]:
    """Hashes of overlapping n-word chunks: a reworded repost still shares
    most of them, an unrelated story shares nearly none."""
    words = re.findall(r"[a-z']+", text.lower())
    out: set[int] = set()
    for i in range(len(words) - n + 1):
        out.add(zlib.crc32(" ".join(words[i:i + n]).encode("utf-8")))
        if len(out) >= cap:
            break
    return out


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def signature(post: dict) -> dict:
    return {"t": sorted(title_tokens(post.get("title", ""))),
            "s": sorted(shingles(post.get("selftext", ""))),
            "title": post.get("title", "")[:80]}


def dupe_of(post: dict, entries: dict) -> tuple[str, str] | None:
    """(matched id, matched title) when the post retells a story we already
    made a video of, else None. Catches reposts under new post ids."""
    pt = title_tokens(post.get("title", ""))
    ps = shingles(post.get("selftext", ""))
    for pid, e in entries.items():
        if jaccard(pt, set(e.get("t", []))) >= TITLE_SIM \
                or jaccard(ps, set(e.get("s", []))) >= BODY_SIM:
            return pid, e.get("title", "")
    return None


def load_history(path: Path = STATE_FILE) -> dict:
    """{post_id: signature}. Migrates the v1 plain id list transparently
    (old entries block by id only; new ones also block reworded reposts)."""
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {pid: {"t": [], "s": [], "title": ""} for pid in data}
    return data.get("made", {})


def save_history(entries: dict, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 2, "made": entries}),
                    encoding="utf-8")


def load_used(path: Path = STATE_FILE) -> set[str]:
    return set(load_history(path))


def save_used(used: set[str], path: Path = STATE_FILE) -> None:
    save_history({pid: {"t": [], "s": [], "title": ""} for pid in used}, path)


def eligible(post: dict, used: set[str], min_score: int = MIN_SCORE) -> bool:
    """The selection bar: real self-post, right length, traction,
    platform-safe, not already used. Posts from the RSS fallback have no
    score (score=None); the feed is already top-sorted, so Reddit's own
    ranking stands in for the upvote bar."""
    text = post.get("selftext") or ""
    words = len(text.split())
    if post.get("stickied") or post.get("over_18"):
        return False
    if not MIN_WORDS <= words <= MAX_WORDS:
        return False
    score = post.get("score")
    if score is not None and score < min_score:
        return False
    if post.get("id") in used:
        return False
    haystack = (post.get("title", "") + " " + text).lower()
    return not any(term in haystack for term in BLOCKLIST)


def select_stories(posts: list[dict], history: dict | set, count: int,
                   min_score: int = MIN_SCORE) -> list[dict]:
    """history: {id: signature} (or a bare id set for tests/back-compat).
    Rejects already-made ids, reworded reposts of past videos, AND two
    tellings of the same story inside one run."""
    entries = history if isinstance(history, dict) else \
        {pid: {"t": [], "s": [], "title": ""} for pid in history}
    candidates = [p for p in posts if eligible(p, set(entries), min_score)]
    candidates.sort(key=lambda p: p.get("score") or 0, reverse=True)
    picks: list[dict] = []
    seen_this_run: dict = {}
    for post in candidates:
        if len(picks) >= count:
            break
        match = dupe_of(post, entries) or dupe_of(post, seen_this_run)
        if match:
            print(f"  skipped (retells {match[0]} \"{match[1][:50]}\"): "
                  f"{post.get('title', '')[:60]}", flush=True)
            continue
        picks.append(post)
        seen_this_run[post["id"]] = signature(post)
    return picks


def _strip_html(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_rss(xml_text: str, sub: str) -> list[dict]:
    """Reddit Atom feed -> the same post dicts the json endpoint gives us
    (minus scores). Entries carry the full self-post as html content."""
    ns = {"a": "http://www.w3.org/2005/Atom"}
    posts = []
    for entry in ET.fromstring(xml_text).findall("a:entry", ns):
        raw_id = (entry.findtext("a:id", "", ns) or "").strip()
        content = entry.findtext("a:content", "", ns) or ""
        title = (entry.findtext("a:title", "", ns) or "").strip()
        author = (entry.findtext("a:author/a:name", "", ns) or "").strip()
        if not raw_id or not content:
            continue
        posts.append({
            "id": raw_id.removeprefix("t3_"),
            "title": html.unescape(title),
            "selftext": _strip_html(content),
            "author": author.lstrip("/").removeprefix("u/") or None,
            "score": None,
            "stickied": False,
            "over_18": "nsfw" in title.lower(),
            "subreddit": sub,
        })
    return posts


def story_slug(post: dict) -> str:
    return f"{post['id']}-{slugify(post.get('title', ''), max_len=32)}"


def build_retell_prompt(title: str, body: str) -> str:
    return RETELL_PROMPT.format(title=title.strip(), body=body.strip()[:3800])


def valid_retell(script: str | None) -> bool:
    if not script:
        return False
    words = len(script.split())
    low = script.lower()
    return (RETELL_MIN <= words <= RETELL_MAX
            and "http" not in low and "reddit" not in low)


# ---- network + orchestration ------------------------------------------------

def _get(url: str, agent: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": agent})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def fetch_top(sub: str, t: str = "day", limit: int = 25) -> list[dict]:
    """RSS first: Reddit hard-blocks the .json endpoints for scripts (403
    regardless of headers, verified 2026-07-28) but serves the same top
    listing as .rss. One request per sub also keeps 429s away. The json
    path remains as a fallback in case RSS ever goes away."""
    last_error: Exception = RuntimeError("no fetch attempted")
    try:
        xml_text = _get(
            f"https://www.reddit.com/r/{sub}/top.rss?t={t}&limit={limit}",
            BROWSER_UA).decode("utf-8", errors="replace")
        return parse_rss(xml_text, sub)
    except Exception as e:
        last_error = e
    for url, agent in [
        (f"https://www.reddit.com/r/{sub}/top.json?t={t}&limit={limit}&raw_json=1", UA),
        (f"https://old.reddit.com/r/{sub}/top.json?t={t}&limit={limit}&raw_json=1", BROWSER_UA),
    ]:
        try:
            data = json.loads(_get(url, agent))
            return [child["data"] for child in data["data"]["children"]]
        except Exception as e:
            last_error = e
    raise last_error


def fetch_with_retry(sub: str, t: str) -> list[dict]:
    """One 429 means slow down, not give up: cool off once and retry."""
    try:
        return fetch_top(sub, t)
    except Exception as e:
        if "429" not in str(e):
            raise
        time.sleep(30)
        return fetch_top(sub, t)


def retell(post: dict) -> str | None:
    prompt = build_retell_prompt(post.get("title", ""), post.get("selftext", ""))
    for attempt in range(2):
        raw = ask_claude(prompt if attempt == 0 else prompt + RETRY_SUFFIX)
        script = extract_script(raw)
        if valid_retell(script):
            return script
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--subs", default=",".join(SUBREDDITS),
                    help="comma list of subreddits to scan")
    ap.add_argument("--t", default="day", choices=["day", "week"],
                    help="top of the day, or week when pickings are thin")
    ap.add_argument("--count", type=int, default=3, help="videos to make")
    ap.add_argument("--min-score", type=int, default=MIN_SCORE)
    ap.add_argument("--style", default="deep",
                    help="styles.json preset (deep = the reddit-stories voice)")
    ap.add_argument("--bg-tag", default=None, help="background folder to use")
    ap.add_argument("--bg", type=Path, default=ROOT / "backgrounds")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch + pick only; no Claude, no rendering")
    ap.add_argument("--no-render", action="store_true",
                    help="write the scripts but skip rendering")
    ap.add_argument("--no-card", action="store_true",
                    help="skip the Reddit post header card overlay")
    ap.add_argument("--avatar", default=None,
                    help="solo narrator PNG (from avatars/), e.g. peter.png")
    args = ap.parse_args(argv)

    history = load_history()
    posts, fetch_errors = [], []
    subs = [s.strip() for s in args.subs.split(",") if s.strip()]
    for i, sub in enumerate(subs):
        if i:
            time.sleep(8)  # 429s arrive fast without generous spacing
        try:
            posts.extend(fetch_with_retry(sub, args.t))
        except Exception as e:  # one dead subreddit must not kill the day
            fetch_errors.append(f"{sub}: {e}")
    print(f"fetched {len(posts)} posts"
          + (f" ({len(fetch_errors)} subs failed)" if fetch_errors else ""),
          flush=True)
    for line in fetch_errors:
        print(f"  fetch failed - {line}", file=sys.stderr, flush=True)

    candidates = select_stories(posts, history, args.count * 3, args.min_score)
    if not candidates:
        print("no stories passed the bar - try --t week or --min-score 500",
              file=sys.stderr)
        return 1
    if args.dry_run:
        for p in candidates[:args.count]:
            score = p.get("score") or "top"
            print(f"  would make: [{score:>5}] r/{p.get('subreddit')} - "
                  f"{p.get('title', '')[:80]}")
        return 0

    REDDIT_DIR.mkdir(parents=True, exist_ok=True)
    made, failed = [], 0
    for post in candidates:
        if len(made) >= args.count:
            break
        title = post.get("title", "")[:80]
        score = post.get("score")
        print(f"[{len(made) + 1}/{args.count}] r/{post.get('subreddit')} "
              f"({f'{score} upvotes' if score else 'top of feed'}): {title}",
              flush=True)
        script = retell(post)
        if script is None:
            print("  retell failed twice, skipping", file=sys.stderr, flush=True)
            failed += 1
            continue
        front = [f"style: {args.style}"]
        if args.bg_tag:
            front.append(f"bg: {args.bg_tag}")
        if args.avatar:
            front.append(f"avatar: {args.avatar}")
        if not args.no_card:
            card = make_post_card(
                title=post.get("title", ""),
                subreddit=post.get("subreddit", "AITAH"),
                author=post.get("author"),
                score=post.get("score"),
                comments=post.get("num_comments"),
                out_png=REDDIT_DIR / f"{story_slug(post)}-card.png",
            )
            front.append(f"overlay: {card.name}")  # resolves next to the script
        dest = REDDIT_DIR / f"{story_slug(post)}.txt"
        dest.write_text("\n".join(front) + "\n---\n" + script + "\n",
                        encoding="utf-8")
        history[post["id"]] = signature(post)
        save_history(history)
        if args.no_render:
            made.append(dest)
            print(f"  script: {dest}", flush=True)
            continue
        out = ROOT / "out" / f"reddit-{story_slug(post)}.mp4"
        try:
            render(dest, args.bg, out)
            made.append(out)
        except (ValueError, RuntimeError) as e:
            print(f"  render failed: {e}", file=sys.stderr, flush=True)
            failed += 1

    print(f"done: {len(made)} made, {failed} failed", flush=True)
    for p in made:
        print(f"  {p}")
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())
