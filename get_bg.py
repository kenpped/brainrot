#!/usr/bin/env python3
"""get_bg.py -- download background gameplay from YouTube links, graded on arrival.

    python get_bg.py "https://youtu.be/..." --tag minecraft
    python get_bg.py URL URL URL --tag subway

Pulls the best quality up to 1080p (backgrounds never need more: the output
frame is 1080x1920), drops the file into backgrounds/<tag>/, then grades it
with the same soft-output check as uploads, so a low-res source is flagged
the moment it lands.

Whose footage it is stays your call: videos marked Creative Commons or from
"free to use gameplay" channels are the clean lane; standard-license videos
carry the usual reuse exposure. The license is printed per video.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import brainrot as br

YT_RE = re.compile(
    r"^https?://(www\.|m\.)?(youtube\.com/(watch\?|shorts/|playlist\?)|youtu\.be/)",
    re.I)
PLAYLIST_RE = re.compile(r"youtube\.com/playlist\?", re.I)
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
# Orientation-proof 1080p cap via SORT, not filters: yt-dlp's `res` sort
# field is the SMALLER dimension, so vertical 1080x1920 and landscape
# 1920x1080 both count as 1080. (A height<=1080 FILTER on a vertical video
# happily matches the 360x640 tier - learned by downloading five of them.)
FORMAT = "bv/b"
SORT = "res:1080,fps,vcodec:h264"
MAX_MINUTES = 45   # playlist entries longer than this get skipped
# Netscape-format cookie export (gitignored, NEVER commit: session tokens).
# Chrome's app-bound encryption blocks --cookies-from-browser on Windows, so
# an in-browser export ("Get cookies.txt LOCALLY" extension) is the reliable key.
COOKIES_FILE = ROOT / "cookies.txt"
# where "random clip" pulls from: known no-copyright vertical gameplay
BG_SOURCES = [
    "https://youtube.com/playlist?list=PLlyn0LTB-nHBO6y-9DD3LqF9FhndmLbg2",
]
GAME_TAGS = ["subway", "roblox", "minecraft", "gta", "forza"]


def ytdlp_bin() -> str:
    """Venv's yt-dlp first (pip `yt-dlp[default]` bundles the EJS challenge
    solver scripts the standalone exe lacks), then PATH, then winget."""
    venv_exe = Path(sys.executable).with_name("yt-dlp.exe")
    if venv_exe.is_file():
        return str(venv_exe)
    found = shutil.which("yt-dlp")
    if found:
        return found
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet"
    for candidate in [base / "Links" / "yt-dlp.exe",
                      *sorted(base.glob("Packages/yt-dlp*/yt-dlp.exe"))]:
        if candidate.is_file():
            return str(candidate)
    return "yt-dlp"


def runtime_env() -> dict:
    """yt-dlp needs a JS runtime on PATH to solve YouTube's n-challenge
    (deno recommended; without one you get storyboards only). Prepend deno
    and node locations so the subprocess always finds them."""
    env = os.environ.copy()
    extra = []
    deno = shutil.which("deno")
    if deno:
        extra.append(str(Path(deno).parent))
    else:
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        hits = sorted(base.glob("DenoLand.Deno*/deno.exe")) if base.exists() else []
        if hits:
            extra.append(str(hits[-1].parent))
    node_dir = Path(r"C:\Program Files\nodejs")
    if node_dir.is_dir():
        extra.append(str(node_dir))
    if extra:
        env["PATH"] = os.pathsep.join(extra) + os.pathsep + env.get("PATH", "")
    return env


def build_download_cmd(url: str, dest_dir: Path, client: str | None = None,
                       cookies_browser: str | None = None,
                       max_items: int = 10) -> list[str]:
    """cookies.txt (if present) beats --cookies-from-browser: the export
    from inside the browser works where DPAPI decryption cannot."""
    cmd = [
        ytdlp_bin(), "-f", FORMAT, "-S", SORT, "--remux-video", "mp4",
        "--restrict-filenames",
        "-o", str(dest_dir / "%(title).60s [%(id)s].%(ext)s"),
        "--no-simulate", "--print", "after_move:filepath",
        "--print", "%(title)s | license: %(license,default=Standard YouTube License)s",
    ]
    if PLAYLIST_RE.search(url):
        cmd += ["--playlist-items", f"1:{max_items}", "--ignore-errors",
                # hour-long entries are multi-GB; backgrounds don't need them
                "--match-filters", f"duration<{MAX_MINUTES * 60}"]
    else:
        cmd += ["--no-playlist"]
    if client:
        cmd += ["--extractor-args", f"youtube:player_client={client}"]
    if COOKIES_FILE.is_file():
        cmd += ["--cookies", str(COOKIES_FILE)]
    elif cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    return cmd + [url]


def _run_ytdlp(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=1800,
                              env=runtime_env())
    except FileNotFoundError:
        raise RuntimeError(
            "yt-dlp not found - winget install yt-dlp.yt-dlp, or reopen the "
            "terminal for a fresh PATH") from None


def fetch(url: str, tag: str, bg_dir: Path,
          cookies_browser: str | None = None, max_items: int = 10) -> list[Path]:
    """Download a video (or the first max_items of a playlist) into
    bg_dir/tag/ and return the file paths. YouTube sometimes throws a
    sign-in bot check at the default client; the tv client usually passes
    it, so that retry is automatic."""
    dest_dir = bg_dir / tag
    dest_dir.mkdir(parents=True, exist_ok=True)
    proc = _run_ytdlp(build_download_cmd(url, dest_dir, max_items=max_items,
                                         cookies_browser=cookies_browser))
    if proc.returncode != 0 and "confirm you" in proc.stderr:
        print("  bot check hit, retrying with the tv client...", flush=True)
        proc = _run_ytdlp(build_download_cmd(url, dest_dir, client="tv",
                                             max_items=max_items,
                                             cookies_browser=cookies_browser))
    if proc.returncode != 0 and "confirm you" in proc.stderr \
            and COOKIES_FILE.is_file():
        raise RuntimeError(
            "YouTube bot check even with cookies.txt - the export has gone "
            "stale (YouTube rotates session tokens while you browse). "
            "Re-export with the 'Get cookies.txt LOCALLY' extension to the "
            "same path and retry; takes 30 seconds.")
    lines = [l for l in proc.stdout.strip().splitlines() if l.strip()]
    paths = [Path(l) for l in lines
             if l.lower().endswith((".mp4", ".mkv", ".webm"))]
    paths = [p for p in paths if p.is_file()]
    for info in (l for l in lines if "license:" in l):
        print(f"  {info}", flush=True)
    # playlists run with --ignore-errors: partial success beats nothing
    if not paths:
        detail = proc.stderr.strip().splitlines()[-1:] or ["unknown error"]
        raise RuntimeError(f"yt-dlp failed: {detail[0]}")
    return paths


def auto_tag(title: str) -> str:
    """Folder from the clip's title, so random pulls sort themselves."""
    low = title.lower()
    for tag in GAME_TAGS:
        if tag in low:
            return tag
    return "vertical"


def existing_ids(bg_dir: Path) -> set[str]:
    """Video ids already in the library (filenames carry [id])."""
    ids = set()
    for p in bg_dir.rglob("*.mp4"):
        m = re.search(r"\[([A-Za-z0-9_-]{6,})\]", p.name)
        if m:
            ids.add(m.group(1))
    return ids


def pick_random_entry(entries: list[dict], skip_ids: set[str], rng) -> dict | None:
    fresh = [e for e in entries if e["id"] not in skip_ids]
    return rng.choice(fresh) if fresh else None


def probe_playlist(url: str) -> list[dict]:
    """Flat listing: [{id, title, seconds}] without downloading anything."""
    proc = _run_ytdlp([
        ytdlp_bin(), "--flat-playlist",
        "--print", "%(id)s\t%(title)s\t%(duration)s", url,
    ] + (["--cookies", str(COOKIES_FILE)] if COOKIES_FILE.is_file() else []))
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[-1:] or ["unknown error"]
        raise RuntimeError(f"playlist probe failed: {detail[0]}")
    entries = []
    for line in proc.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        vid, title, dur = parts
        try:
            seconds = float(dur)
        except ValueError:
            seconds = 0.0
        if seconds and seconds > MAX_MINUTES * 60:
            continue
        entries.append({"id": vid, "title": title, "seconds": seconds})
    return entries


def fetch_random(bg_dir: Path, rng=None) -> Path:
    """One surprise clip from BG_SOURCES: never a repeat of what's already
    in the library, auto-tagged into the right game folder."""
    import random as _random
    rng = rng or _random
    source = rng.choice(BG_SOURCES)
    entry = pick_random_entry(probe_playlist(source), existing_ids(bg_dir), rng)
    if entry is None:
        raise RuntimeError("every clip in the source playlists is already "
                           "in your library")
    tag = auto_tag(entry["title"])
    print(f"random pick: {entry['title'][:70]} -> {tag}/", flush=True)
    paths = fetch(f"https://www.youtube.com/watch?v={entry['id']}", tag, bg_dir)
    return paths[0]


def grade(path: Path) -> str:
    width, height = br.probe_size(path)
    minutes = br.probe_duration(path) / 60
    note = br.upscale_note(width, height)
    verdict = note if note else "quality OK, zero to mild upscale"
    return f"{width}x{height}, {minutes:.1f} min - {verdict}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("urls", nargs="*", help="YouTube video links")
    ap.add_argument("--random", action="store_true",
                    help="pull one surprise clip from the known no-copyright "
                         "playlists, auto-tagged, never a library repeat")
    ap.add_argument("--tag", default=None,
                    help="backgrounds/ subfolder to drop them in, e.g. minecraft")
    ap.add_argument("--bg", type=Path, default=ROOT / "backgrounds")
    import os
    ap.add_argument("--cookies-from-browser",
                    default=os.environ.get("BRAINROT_YT_COOKIES") or None,
                    metavar="BROWSER",
                    help="OPT-IN: pass your browser's YouTube session to "
                         "yt-dlp (e.g. chrome, edge) if the bot check blocks "
                         "both clients; ties downloads to your account. "
                         "Set BRAINROT_YT_COOKIES to make it this machine's "
                         "default (the repo default stays off).")
    ap.add_argument("--max", type=int, default=10, dest="max_items",
                    help="cap per playlist link (disk + OneDrive sync sanity)")
    args = ap.parse_args(argv)

    if args.random:
        try:
            path = fetch_random(args.bg)
            print(f"  {path.name}: {grade(path)}", flush=True)
            print("done: 1 downloaded, 0 failed", flush=True)
            return 0
        except (RuntimeError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    if not args.urls:
        ap.error("give YouTube links, or use --random")
    if not args.tag or not TAG_RE.match(args.tag):
        ap.error("tag must be lowercase letters/numbers/dashes, e.g. minecraft")
    bad = [u for u in args.urls if not YT_RE.match(u)]
    if bad:
        ap.error(f"not YouTube links: {', '.join(bad)}")

    got, failed = [], []
    for i, url in enumerate(args.urls):
        print(f"[{i + 1}/{len(args.urls)}] {url}", flush=True)
        try:
            paths = fetch(url, args.tag, args.bg,
                          cookies_browser=args.cookies_from_browser,
                          max_items=args.max_items)
            for path in paths:
                print(f"  {path.name}: {grade(path)}", flush=True)
            got.extend(paths)
        except (RuntimeError, ValueError) as e:
            print(f"  FAILED: {e}", file=sys.stderr, flush=True)
            failed.append(url)

    print(f"done: {len(got)} downloaded, {len(failed)} failed", flush=True)
    return 0 if got and not failed else (0 if got else 1)


if __name__ == "__main__":
    sys.exit(main())
