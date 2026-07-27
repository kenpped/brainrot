"""Gate tests for webapp.py -- no worker thread, no subprocesses, no network.

The worker only starts in webapp.main(), so POSTs here just enqueue.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import webapp


@pytest.fixture()
def client(tmp_path, monkeypatch):
    bg = tmp_path / "backgrounds"
    (bg / "synthetic").mkdir(parents=True)
    (bg / "synthetic" / "clip.mp4").write_bytes(b"x")
    monkeypatch.setattr(webapp, "BG_DIR", bg)
    monkeypatch.setattr(webapp, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(webapp, "WEB_SCRIPTS", tmp_path / "scripts_web")
    (tmp_path / "out").mkdir()
    yield webapp.app.test_client()
    while not webapp.QUEUE.empty():  # never leak jobs into other tests
        webapp.QUEUE.get_nowait()
    webapp.JOBS.clear()


# ---- pure helpers ----------------------------------------------------------

def test_build_render_cmd_minimal():
    cmd = webapp.build_render_cmd(Path("s.txt"), Path("o.mp4"), {})
    assert cmd[0] == sys.executable
    assert "--script" in cmd and "--out" in cmd and "--bg" in cmd
    assert "--style" not in cmd and "--voice" not in cmd


def test_build_render_cmd_full():
    cmd = webapp.build_render_cmd(Path("s.txt"), Path("o.mp4"), {
        "style": "hype", "voice": "en-US-GuyNeural", "rate": "+22%",
        "font": "Georgia", "bg_tag": "minecraft",
    })
    for flag, val in (("--style", "hype"), ("--voice", "en-US-GuyNeural"),
                      ("--rate", "+22%"), ("--font", "Georgia"),
                      ("--bg-tag", "minecraft")):
        assert cmd[cmd.index(flag) + 1] == val


def test_build_write_cmd():
    cmd = webapp.build_write_cmd("why sleep matters", True, "hype", "synthetic")
    assert cmd[2] == "why sleep matters"
    assert "--dialogue" in cmd
    assert cmd[cmd.index("--style") + 1] == "hype"
    assert cmd[cmd.index("--bg") + 1] == "synthetic"


def test_name_for_script_strips_front_matter():
    text = "voice: x\nstyle: hype\n---\nYour brain is running a scam today"
    assert webapp.name_for_script(text) == "your-brain-is-running-a-scam"


def test_uniquify(tmp_path):
    p = tmp_path / "a.mp4"
    assert webapp.uniquify(p) == p
    p.write_bytes(b"x")
    assert webapp.uniquify(p) == tmp_path / "a-2.mp4"


def test_scan_bg_tags(tmp_path, monkeypatch):
    (tmp_path / "minecraft").mkdir()
    (tmp_path / "minecraft" / "a.mp4").write_bytes(b"x")
    (tmp_path / "empty").mkdir()
    (tmp_path / "loose.mp4").write_bytes(b"x")
    monkeypatch.setattr(webapp, "BG_DIR", tmp_path)
    assert webapp.scan_bg_tags() == ["minecraft"]  # empty dirs and loose files skipped


# ---- routes ----------------------------------------------------------------

def test_config_lists_styles_voices_tags(client):
    data = client.get("/api/config").get_json()
    assert "default" in data["styles"]
    assert any(v["voice"].startswith("en-") for v in data["voices"])
    assert data["bg_tags"] == ["synthetic"]


def test_render_rejects_empty_script(client):
    res = client.post("/api/render", json={"script": "  "})
    assert res.status_code == 400


def test_render_rejects_bad_rate_style_and_front_matter(client):
    assert client.post("/api/render", json={"script": "hi", "rate": "fast"}).status_code == 400
    assert client.post("/api/render", json={"script": "hi", "style": "nope"}).status_code == 400
    res = client.post("/api/render", json={"script": "voice: x\nspeed: +9%\n---\nhi"})
    assert res.status_code == 400
    assert "unknown front matter key" in res.get_json()["error"]


def test_render_rejects_missing_bg_tag(client):
    res = client.post("/api/render", json={"script": "hi", "bg_tag": "gta"})
    assert res.status_code == 400
    assert "bg tag" in res.get_json()["error"]


def test_render_enqueues_and_writes_script(client):
    res = client.post("/api/render", json={"script": "hello there world", "style": "hype"})
    assert res.status_code == 202
    jid = res.get_json()["id"]
    job = webapp.JOBS[jid]
    assert job.state == "queued" and job.kind == "render"
    assert job.out == "hello-there-world.mp4"
    assert (webapp.WEB_SCRIPTS / "hello-there-world.txt").read_text(encoding="utf-8").strip() \
        == "hello there world"
    assert webapp.QUEUE.qsize() == 1
    listed = client.get("/api/jobs").get_json()
    assert listed[0]["id"] == jid and listed[0]["state"] == "queued"


def test_write_enqueues(client):
    res = client.post("/api/write", json={"topic": "test topic", "dialogue": True})
    assert res.status_code == 202
    job = webapp.JOBS[res.get_json()["id"]]
    assert job.kind == "script" and webapp.QUEUE.qsize() == 1


def test_write_rejects_empty_topic(client):
    assert client.post("/api/write", json={}).status_code == 400


def test_video_404_and_job_404(client):
    assert client.get("/video/nope.mp4").status_code == 404
    assert client.get("/api/jobs/nope").status_code == 404


def test_index_serves_page(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"BRAINROT STUDIO" in res.data
