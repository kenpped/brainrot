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


def test_build_write_cmd_cast_replaces_dialogue_flag():
    cmd = webapp.build_write_cmd("t", True, None, None, cast=["grump", "hype"])
    assert cmd[cmd.index("--cast") + 1] == "grump,hype"
    assert "--dialogue" not in cmd


def test_write_validates_cast(client):
    bad = client.post("/api/write", json={"topic": "t", "cast": ["grump"]})
    assert bad.status_code == 400
    same = client.post("/api/write", json={"topic": "t", "cast": ["grump", "grump"]})
    assert same.status_code == 400
    unknown = client.post("/api/write", json={"topic": "t", "cast": ["grump", "peter"]})
    assert unknown.status_code == 400
    ok = client.post("/api/write", json={"topic": "t", "cast": ["grump", "hype"]})
    assert ok.status_code == 202


def test_config_lists_characters(client):
    data = client.get("/api/config").get_json()
    names = [c["name"] for c in data["characters"]]
    assert "grump" in names and "hype" in names
    assert all(c["persona"] for c in data["characters"])


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

def test_config_lists_styles_voices_tags_fonts(client):
    data = client.get("/api/config").get_json()
    assert "default" in data["styles"]
    assert any(v["voice"].startswith("en-") for v in data["voices"])
    assert data["bg_tags"] == ["synthetic"]
    assert data["fonts"], "font list must not be empty"
    assert all(f in webapp.POPULAR_FONTS for f in data["fonts"])


def test_font_choices_filters_to_installed(monkeypatch):
    monkeypatch.setattr(webapp.br, "installed_fonts",
                        lambda: {"Impact (TrueType)", "Georgia (TrueType)"})
    assert webapp.font_choices() == ["Impact", "Georgia"]


def test_font_choices_all_when_no_registry(monkeypatch):
    monkeypatch.setattr(webapp.br, "installed_fonts", lambda: None)
    assert webapp.font_choices() == webapp.POPULAR_FONTS


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


# ---- uploads ---------------------------------------------------------------

def upload(client, filename="clip.mp4", tag="minecraft", data=b"fake video"):
    import io
    return client.post("/api/upload", data={
        "file": (io.BytesIO(data), filename), "tag": tag,
    }, content_type="multipart/form-data")


def test_upload_happy_path(client, monkeypatch):
    monkeypatch.setattr(webapp.br, "probe_duration", lambda p: 600.0)
    monkeypatch.setattr(webapp.br, "probe_size", lambda p: (1080, 1920))
    res = upload(client)
    assert res.status_code == 200
    body = res.get_json()
    assert body == {"tag": "minecraft", "name": "clip.mp4", "minutes": 10.0,
                    "warn": ""}
    assert (webapp.BG_DIR / "minecraft" / "clip.mp4").read_bytes() == b"fake video"


def test_upload_warns_on_low_res(client, monkeypatch):
    monkeypatch.setattr(webapp.br, "probe_duration", lambda p: 600.0)
    monkeypatch.setattr(webapp.br, "probe_size", lambda p: (202, 360))
    body = upload(client, filename="tiny.mp4").get_json()
    assert "soft" in body["warn"] and "720p+" in body["warn"]


def test_upload_rejects_bad_tag_and_ext(client):
    assert upload(client, tag="My Clips!").status_code == 400
    assert upload(client, filename="clip.exe").status_code == 400
    assert client.post("/api/upload", data={"tag": "minecraft"},
                       content_type="multipart/form-data").status_code == 400


def test_upload_removes_undecodable_file(client, monkeypatch):
    def boom(p):
        raise RuntimeError("not a video")
    monkeypatch.setattr(webapp.br, "probe_duration", boom)
    res = upload(client, filename="junk.mp4", tag="gta")
    assert res.status_code == 400
    assert not (webapp.BG_DIR / "gta" / "junk.mp4").exists()


def test_upload_uniquifies_collisions(client, monkeypatch):
    monkeypatch.setattr(webapp.br, "probe_duration", lambda p: 60.0)
    monkeypatch.setattr(webapp.br, "probe_size", lambda p: (1080, 1920))
    assert upload(client).get_json()["name"] == "clip.mp4"
    assert upload(client).get_json()["name"] == "clip-2.mp4"


def test_open_endpoint_returns_path(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(webapp.subprocess, "Popen",
                        lambda cmd, **k: seen.setdefault("cmd", cmd))
    res = client.post("/api/open")
    assert res.status_code == 200
    assert res.get_json()["path"] == str(webapp.BG_DIR)
    assert "explorer.exe" in seen["cmd"][0] or seen["cmd"][0] in ("open", "xdg-open")


# ---- youtube fetch ---------------------------------------------------------

def test_fetch_bg_validates_and_enqueues(client):
    bad_url = client.post("/api/fetch_bg", json={"url": "https://vimeo.com/1",
                                                 "tag": "minecraft"})
    assert bad_url.status_code == 400
    bad_tag = client.post("/api/fetch_bg", json={
        "url": "https://youtu.be/abc", "tag": "My Clips!"})
    assert bad_tag.status_code == 400
    ok = client.post("/api/fetch_bg", json={
        "url": "https://youtu.be/abc", "tag": "minecraft"})
    assert ok.status_code == 202
    job = webapp.JOBS[ok.get_json()["id"]]
    assert job.kind == "fetch" and webapp.QUEUE.qsize() == 1


def test_build_fetch_cmd():
    cmd = webapp.build_fetch_cmd("https://youtu.be/x", "gta")
    assert cmd[0] == sys.executable and cmd[1].endswith("get_bg.py")
    assert cmd[2] == "https://youtu.be/x"
    assert cmd[cmd.index("--tag") + 1] == "gta"


def test_studio_buttons_build_right_commands():
    assert webapp.build_random_cmd()[-1] == "--random"
    reddit = webapp.build_reddit_cmd(2, "roblox")
    assert reddit[1].endswith("reddit_stories.py")
    assert reddit[reddit.index("--count") + 1] == "2"
    assert reddit[reddit.index("--bg-tag") + 1] == "roblox"
    ideas_cmd = webapp.build_ideas_cmd("money traps")
    assert ideas_cmd[1].endswith("ideas.py") and "--json" in ideas_cmd
    assert ideas_cmd[ideas_cmd.index("--theme") + 1] == "money traps"


def test_studio_button_routes_enqueue(client):
    for route, body in (("/api/fetch_random", {}),
                        ("/api/reddit", {"count": 1}),
                        ("/api/ideas", {"theme": "history"})):
        res = client.post(route, json=body)
        assert res.status_code == 202, route
    assert webapp.QUEUE.qsize() == 3
    assert client.post("/api/reddit", json={"bg_tag": "Bad Tag!"}).status_code == 400


# ---- LAN token gate --------------------------------------------------------

def test_lan_gate_blocks_without_token(client):
    webapp.app.config["LAN_TOKEN"] = "sekrit"
    try:
        assert client.get("/api/config").status_code == 401
        ok = client.get("/api/config?token=sekrit")
        assert ok.status_code == 200
        assert "brainrot_token=sekrit" in ok.headers.get("Set-Cookie", "")
        assert client.get("/api/config").status_code == 200  # cookie persists
    finally:
        webapp.app.config.pop("LAN_TOKEN", None)


def test_no_token_configured_means_open(client):
    assert client.get("/api/config").status_code == 200
