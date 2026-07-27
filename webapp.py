#!/usr/bin/env python3
"""webapp.py -- local web studio for the brainrot pipeline.

    python webapp.py    ->    http://127.0.0.1:8765

Type a topic (AI writes the script via local Claude Code) or paste a script,
pick style / voice / speed / background, hit generate. Jobs run one at a time
in a worker; the page polls progress and shows finished videos in a gallery.
Local only -- binds 127.0.0.1, nothing is uploaded anywhere.
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import brainrot as br
from voices import CURATED
from write_script import slugify

OUT_DIR = ROOT / "out"
BG_DIR = ROOT / "backgrounds"
WEB_SCRIPTS = ROOT / "scripts" / "web"
PAGE = ROOT / "web" / "index.html"
PORT = 8765

app = Flask(__name__)


@dataclass
class Job:
    id: str
    kind: str              # "render" | "script"
    name: str
    state: str = "queued"  # queued | running | done | failed
    log: list = field(default_factory=list)
    error: str = ""
    out: str = ""          # render: mp4 filename / script: generated text
    created: float = field(default_factory=time.time)


JOBS: dict[str, Job] = {}
QUEUE: queue.Queue = queue.Queue()   # (Job, cmd list, result_file or None)


def worker() -> None:
    """Single lane on purpose: whisper + ffmpeg saturate the CPU per render,
    so parallel jobs would just fight each other."""
    while True:
        job, cmd, result_file = QUEUE.get()
        job.state = "running"
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(ROOT), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace",
            )
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    job.log.append(line)
            rc = proc.wait()
            if rc != 0:
                job.state = "failed"
                job.error = "\n".join(job.log[-4:]) or f"exit code {rc}"
            else:
                if result_file is not None:
                    job.out = result_file.read_text(encoding="utf-8")
                job.state = "done"
        except Exception as e:
            job.state = "failed"
            job.error = str(e)


def start_worker() -> None:
    threading.Thread(target=worker, daemon=True).start()


# ---- pure helpers (gate-tested) --------------------------------------------

def build_render_cmd(script_path: Path, out_path: Path, opts: dict) -> list[str]:
    cmd = [sys.executable, str(ROOT / "brainrot.py"),
           "--script", str(script_path), "--bg", str(BG_DIR), "--out", str(out_path)]
    for key, flag in (("style", "--style"), ("voice", "--voice"), ("rate", "--rate"),
                      ("font", "--font"), ("bg_tag", "--bg-tag")):
        value = (opts.get(key) or "").strip()
        if value:
            cmd += [flag, value]
    return cmd


def build_write_cmd(topic: str, dialogue: bool, style: str | None,
                    bg: str | None) -> list[str]:
    cmd = [sys.executable, str(ROOT / "write_script.py"), topic]
    if dialogue:
        cmd.append("--dialogue")
    if style:
        cmd += ["--style", style]
    if bg:
        cmd += ["--bg", bg]
    return cmd


def name_for_script(text: str) -> str:
    """Output name from the first words of the script body (front matter
    stripped), so out/ stays human-readable."""
    body = text.split("---")[-1].strip()
    return slugify(" ".join(body.split()[:6])) or "video"


def uniquify(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        candidate = path.with_stem(f"{path.stem}-{i}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"1000 name collisions for {path}")


# caption-suitable fonts that commonly ship with Windows, loudest first;
# the dropdown only offers the ones actually installed on this machine
POPULAR_FONTS = [
    "Impact", "Arial Black", "Bahnschrift", "Franklin Gothic Medium",
    "Segoe UI Black", "Verdana", "Tahoma", "Trebuchet MS", "Georgia",
    "Comic Sans MS", "Arial", "Consolas", "Courier New", "Times New Roman",
]


def font_choices() -> list[str]:
    installed = br.installed_fonts()
    if installed is None:  # non-Windows: no registry to check, offer them all
        return POPULAR_FONTS
    lowered = [n.lower() for n in installed]
    return [f for f in POPULAR_FONTS
            if any(n.startswith(f.lower()) for n in lowered)]


def scan_bg_tags() -> list[str]:
    if not BG_DIR.is_dir():
        return []
    return sorted(
        d.name for d in BG_DIR.iterdir()
        if d.is_dir() and any(p.suffix.lower() in br.VIDEO_EXTS for p in d.rglob("*"))
    )


# ---- routes ----------------------------------------------------------------

@app.get("/")
def index():
    return PAGE.read_text(encoding="utf-8")


@app.get("/api/config")
def api_config():
    styles = br.load_styles()
    return jsonify({
        "styles": {name: dict(br.DEFAULTS, **s) for name, s in styles.items()},
        "voices": [{"voice": v, "vibe": d} for v, d in CURATED],
        "bg_tags": scan_bg_tags(),
        "fonts": font_choices(),
    })


@app.post("/api/render")
def api_render():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("script") or "").strip()
    if not text:
        return jsonify({"error": "script is empty"}), 400
    rate = (data.get("rate") or "").strip()
    if rate and not br.RATE_RE.match(rate):
        return jsonify({"error": f"speed must look like +18%, got {rate!r}"}), 400
    style = (data.get("style") or "").strip()
    if style and style not in br.load_styles():
        return jsonify({"error": f"unknown style {style!r}"}), 400
    try:
        br.list_backgrounds(BG_DIR, tag=(data.get("bg_tag") or "").strip() or None)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    WEB_SCRIPTS.mkdir(parents=True, exist_ok=True)
    name = name_for_script(text)
    script_path = uniquify(WEB_SCRIPTS / f"{name}.txt")
    script_path.write_text(text + "\n", encoding="utf-8")
    try:
        br.parse_script(script_path)  # typo'd front matter fails now, not after TTS
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    out_path = uniquify(OUT_DIR / f"{name}.mp4")
    job = Job(id=uuid.uuid4().hex[:10], kind="render",
              name=out_path.stem, out=out_path.name)
    JOBS[job.id] = job
    QUEUE.put((job, build_render_cmd(script_path, out_path, data), None))
    return jsonify({"id": job.id}), 202


@app.post("/api/write")
def api_write():
    data = request.get_json(force=True, silent=True) or {}
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "topic is empty"}), 400
    style = (data.get("style") or "").strip() or None
    bg = (data.get("bg_tag") or "").strip() or None
    dest = ROOT / "scripts" / f"{slugify(topic)}.txt"
    job = Job(id=uuid.uuid4().hex[:10], kind="script", name=f"write: {topic[:48]}")
    JOBS[job.id] = job
    QUEUE.put((job, build_write_cmd(topic, bool(data.get("dialogue")), style, bg), dest))
    return jsonify({"id": job.id}), 202


@app.get("/api/jobs")
def api_jobs():
    items = sorted(JOBS.values(), key=lambda j: j.created, reverse=True)
    return jsonify([{
        "id": j.id, "kind": j.kind, "name": j.name, "state": j.state,
        "last": j.log[-1] if j.log else "", "error": j.error,
        "out": j.out if j.kind == "render" else "",
    } for j in items[:30]])


@app.get("/api/jobs/<jid>")
def api_job(jid):
    job = JOBS.get(jid)
    if job is None:
        return jsonify({"error": "no such job"}), 404
    return jsonify({"id": job.id, "kind": job.kind, "name": job.name,
                    "state": job.state, "log": job.log, "error": job.error,
                    "out": job.out})


@app.get("/api/videos")
def api_videos():
    if not OUT_DIR.is_dir():
        return jsonify([])
    vids = sorted(OUT_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonify([{"name": p.name, "mb": round(p.stat().st_size / 1e6, 1)}
                    for p in vids])


@app.get("/video/<path:name>")
def video(name):
    return send_from_directory(OUT_DIR, name, conditional=True)


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    start_worker()
    print(f"brainrot studio -> http://127.0.0.1:{PORT}", flush=True)
    app.run(host="127.0.0.1", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
