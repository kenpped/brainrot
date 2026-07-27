#!/usr/bin/env python3
"""End-to-end eval for the web studio: a real render driven through the
Flask app with the real worker + subprocess, verified over HTTP.

Slow lane, needs network (edge-tts) and ffmpeg:

    python eval/eval_webapp.py

Checks: job runs to done, the mp4 lands in out/, /api/videos lists it,
/video/<name> serves it, and Range requests work (video seeking).
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import make_bg
import webapp

SCRIPT = "Web studio check. This video was queued from the browser page."
TIMEOUT_S = 300

checks = []


def check(name, ok, detail):
    checks.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:<14} {detail}")


def main() -> int:
    # self-sufficient background: tiny synthetic clip if the folder is empty
    synth = ROOT / "backgrounds" / "synthetic"
    synth.mkdir(parents=True, exist_ok=True)
    tiny = synth / "eval_tiny.mp4"
    if not tiny.exists():
        make_bg.write_video(tiny, make_bg.gen_balls(8.0, 24, 360, 640, seed=5),
                            24, 360, 640, int(8 * 24), "eval bg")

    webapp.start_worker()
    client = webapp.app.test_client()

    res = client.post("/api/render", json={"script": SCRIPT, "bg_tag": "synthetic"})
    check("queued", res.status_code == 202, f"POST /api/render -> {res.status_code}")
    jid = (res.get_json() or {}).get("id")

    state, job = "queued", {}
    deadline = time.monotonic() + TIMEOUT_S
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{jid}").get_json()
        state = job["state"]
        if state in ("done", "failed"):
            break
        time.sleep(2)
    check("job done", state == "done",
          f"state={state}" + (f" error={job.get('error')}" if state == "failed" else
                              f" ({len(job.get('log', []))} log lines)"))

    name = job.get("out", "")
    vids = client.get("/api/videos").get_json()
    check("listed", any(v["name"] == name for v in vids),
          f"{name} in /api/videos ({len(vids)} total)")

    res = client.get(f"/video/{name}")
    check("served", res.status_code == 200 and len(res.data) > 100_000,
          f"GET /video/{name} -> {res.status_code}, {len(res.data) / 1e6:.1f} MB")

    res = client.get(f"/video/{name}", headers={"Range": "bytes=0-99"})
    check("range", res.status_code == 206 and len(res.data) == 100,
          f"Range request -> {res.status_code} ({len(res.data)} bytes)")

    verdict = all(checks)
    print(f"\nverdict: {'PASS' if verdict else 'FAIL'}  ({sum(checks)}/{len(checks)} checks)")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
