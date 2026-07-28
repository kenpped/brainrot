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
# best video-only stream up to 1080p-equivalent in EITHER orientation
# (a vertical "1080p" is 1080x1920, so its height is 1920 - capping height
# alone rejects every vertical format). Audio gets replaced anyway.
FORMAT = ("bestvideo[height<=1080][ext=mp4]/bestvideo[width<=1080][ext=mp4]/"
          "bestvideo[height<=1080]/bestvideo[width<=1080]/"
          "best[height<=1080]/best[width<=1080]/best")
MAX_MINUTES = 45   # playlist entries longer than this get skipped
# Netscape-format cookie export (gitignored, NEVER commit: session tokens).
# Chrome's app-bound encryption blocks --cookies-from-browser on Windows, so
# an in-browser export ("Get cookies.txt LOCALLY" extension) is the reliable key.
COOKIES_FILE = ROOT / "cookies.txt"


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
        ytdlp_bin(), "-f", FORMAT, "--remux-video", "mp4",
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


def grade(path: Path) -> str:
    width, height = br.probe_size(path)
    minutes = br.probe_duration(path) / 60
    note = br.upscale_note(width, height)
    verdict = note if note else "quality OK, zero to mild upscale"
    return f"{width}x{height}, {minutes:.1f} min - {verdict}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("urls", nargs="+", help="YouTube video links")
    ap.add_argument("--tag", required=True,
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

    if not TAG_RE.match(args.tag):
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
