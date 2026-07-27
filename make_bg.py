#!/usr/bin/env python3
"""make_bg.py -- generate copyright-free satisfying background loops.

Two generators, both deterministic per seed:
  balls   neon bouncing balls with motion trails (physics sim)
  tunnel  hypnotic warping color tunnel flying inward

    python make_bg.py                          # both, 2 min each, 720x1280
    python make_bg.py --what balls --minutes 5 --seed 3

Output lands in backgrounds/synthetic/ so renders can use --bg backgrounds/
(or bg: synthetic front matter) with zero copyright exposure. Real gameplay
you capture yourself still looks better - this is the works-today option.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from brainrot import ffmpeg_bin

PALETTE = np.array([
    [255, 60, 80], [255, 200, 0], [0, 220, 130], [60, 160, 255],
    [200, 90, 255], [255, 120, 0], [0, 230, 230],
], dtype=np.float32)


# ---- balls (gate-tested physics) -------------------------------------------

def step_balls(pos, vel, radii, w, h, gravity=0.5, max_speed=18.0):
    """One physics frame: gravity, move, elastic wall bounce, stay in bounds."""
    vel[:, 1] += gravity
    np.clip(vel, -max_speed, max_speed, out=vel)
    pos += vel
    for axis, limit in ((0, w), (1, h)):
        low = pos[:, axis] < radii
        high = pos[:, axis] > limit - radii
        vel[low | high, axis] *= -1
        np.clip(pos[:, axis], radii, limit - radii, out=pos[:, axis])
    return pos, vel


def draw_ball(frame, x, y, r, color):
    h, w, _ = frame.shape
    x0, x1 = max(int(x - r), 0), min(int(x + r) + 1, w)
    y0, y1 = max(int(y - r), 0), min(int(y + r) + 1, h)
    if x0 >= x1 or y0 >= y1:
        return
    yy, xx = np.ogrid[y0:y1, x0:x1]
    mask = (xx - x) ** 2 + (yy - y) ** 2 <= r * r
    sub = frame[y0:y1, x0:x1]
    sub[mask] = np.maximum(sub[mask], color)


def gen_balls(seconds, fps, w, h, seed=7, n=14):
    rng = np.random.default_rng(seed)
    radii = rng.uniform(min(w, h) * 0.04, min(w, h) * 0.09, n).astype(np.float32)
    pos = np.column_stack([
        rng.uniform(radii, w - radii), rng.uniform(radii, h - radii),
    ]).astype(np.float32)
    vel = rng.uniform(-9, 9, (n, 2)).astype(np.float32)
    colors = PALETTE[rng.integers(0, len(PALETTE), n)]
    frame = np.zeros((h, w, 3), dtype=np.float32)
    for _ in range(int(seconds * fps)):
        frame *= 0.86  # trails
        pos, vel = step_balls(pos, vel, radii, w, h)
        for (x, y), r, c in zip(pos, radii, colors):
            draw_ball(frame, x, y, r, c)
        yield frame


# ---- tunnel (gate-tested frame math) ---------------------------------------

def tunnel_frame(t, r, a, scale):
    """One tunnel frame from precomputed polar coords. Rings race inward."""
    v = np.sin(r * 0.045 - t * 9.0 + 2.5 * np.sin(a * 3 + t * 0.9))
    depth = np.clip(1.6 - r / scale, 0.15, 1.0)
    rgb = np.stack([
        np.sin(v) * 0.5 + 0.5,
        np.sin(v + 2.1) * 0.5 + 0.5,
        np.sin(v + 4.2) * 0.5 + 0.5,
    ], axis=-1)
    return rgb * 255.0 * depth[..., None]


def gen_tunnel(seconds, fps, w, h, seed=7):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx = w / 2 + (seed % 7 - 3) * w * 0.02   # seed nudges the center a bit
    cy = h / 2 + (seed % 5 - 2) * h * 0.02
    r = np.hypot(xx - cx, yy - cy) + 1e-3
    a = np.arctan2(yy - cy, xx - cx)
    scale = 0.9 * max(w, h)
    for i in range(int(seconds * fps)):
        yield tunnel_frame(i / fps, r, a, scale)


GENERATORS = {"balls": gen_balls, "tunnel": gen_tunnel}


# ---- encoding --------------------------------------------------------------

def write_video(path, frames, fps, w, h, total, label):
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i, frame in enumerate(frames):
            proc.stdin.write(np.clip(frame, 0, 255).astype(np.uint8).tobytes())
            if total >= 10 and (i + 1) % (total // 10) == 0:
                print(f"  {label}: {100 * (i + 1) // total}%", flush=True)
    finally:
        proc.stdin.close()
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg exited {rc} while writing {path}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--what", choices=[*GENERATORS, "both"], default="both")
    ap.add_argument("--minutes", type=float, default=2.0, help="length per clip")
    ap.add_argument("--size", default="720x1280", help="WxH, gets cropped to 9:16 anyway")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--outdir", type=Path,
                    default=Path(__file__).resolve().parent / "backgrounds" / "synthetic")
    args = ap.parse_args(argv)

    try:
        w, h = (int(x) for x in args.size.lower().split("x"))
    except ValueError:
        ap.error(f"--size must be WxH like 720x1280, got {args.size}")

    args.outdir.mkdir(parents=True, exist_ok=True)
    names = list(GENERATORS) if args.what == "both" else [args.what]
    seconds = args.minutes * 60
    total = int(seconds * args.fps)
    for name in names:
        dest = args.outdir / f"{name}_{args.seed}.mp4"
        print(f"{name} -> {dest} ({args.minutes:g} min, {w}x{h}@{args.fps})", flush=True)
        frames = GENERATORS[name](seconds, args.fps, w, h, seed=args.seed)
        write_video(dest, frames, args.fps, w, h, total, name)
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
