"""Gate tests for get_bg.py -- no network, no yt-dlp runs."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import get_bg


def test_youtube_url_regex():
    ok = ["https://www.youtube.com/watch?v=abc123",
          "https://youtu.be/abc123",
          "https://m.youtube.com/watch?v=abc123",
          "https://www.youtube.com/shorts/abc123",
          "https://youtube.com/playlist?list=PLxyz&si=abc"]
    for u in ok:
        assert get_bg.YT_RE.match(u), u
    bad = ["https://vimeo.com/123", "https://example.com/watch?v=1",
           "notaurl", "https://youtube.evil.com/watch?v=1"]
    for u in bad:
        assert not get_bg.YT_RE.match(u), u


def test_ytdlp_prefers_venv_binary(tmp_path, monkeypatch):
    """pip yt-dlp[default] bundles the EJS solver the standalone exe lacks."""
    fake_python = tmp_path / "Scripts" / "python.exe"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_bytes(b"x")
    (tmp_path / "Scripts" / "yt-dlp.exe").write_bytes(b"x")
    monkeypatch.setattr(get_bg.sys, "executable", str(fake_python))
    assert get_bg.ytdlp_bin() == str(tmp_path / "Scripts" / "yt-dlp.exe")


def test_runtime_env_prepends_js_runtime(tmp_path, monkeypatch):
    """No JS runtime on PATH = storyboards only; deno dir must be prepended."""
    deno = tmp_path / "Microsoft" / "WinGet" / "Packages" / "DenoLand.Deno_x" / "deno.exe"
    deno.parent.mkdir(parents=True)
    deno.write_bytes(b"x")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(get_bg.shutil, "which", lambda _: None)
    env = get_bg.runtime_env()
    assert env["PATH"].startswith(str(deno.parent))


def test_playlist_mode_flags(tmp_path):
    single = get_bg.build_download_cmd("https://youtu.be/x", tmp_path)
    assert "--no-playlist" in single and "--playlist-items" not in single
    pl = get_bg.build_download_cmd(
        "https://youtube.com/playlist?list=PLxyz", tmp_path, max_items=7)
    assert "--no-playlist" not in pl
    assert pl[pl.index("--playlist-items") + 1] == "1:7"
    assert "--ignore-errors" in pl        # one dead video must not kill the batch
    assert pl[pl.index("--match-filters") + 1] == f"duration<{get_bg.MAX_MINUTES * 60}"


def test_resolution_cap_is_orientation_proof():
    """res: sorts by the SMALLER dimension - a height<=1080 FILTER once
    fetched five 360x640 files off vertical videos."""
    assert "res:1080" in get_bg.SORT
    assert "height<=" not in get_bg.FORMAT and "width<=" not in get_bg.FORMAT
    assert get_bg.FORMAT.endswith("/b")             # never zero-format


def test_download_cmd_carries_sort(tmp_path):
    cmd = get_bg.build_download_cmd("https://youtu.be/x", tmp_path)
    assert cmd[cmd.index("-S") + 1] == get_bg.SORT


def test_build_download_cmd(tmp_path):
    cmd = get_bg.build_download_cmd("https://youtu.be/x", tmp_path)
    assert cmd[cmd.index("-f") + 1] == get_bg.FORMAT
    assert "--no-playlist" in cmd            # a playlist link must not fan out
    assert "--restrict-filenames" in cmd     # ffmpeg-safe names
    assert "--remux-video" in cmd
    assert cmd[-1] == "https://youtu.be/x"
    out = cmd[cmd.index("-o") + 1]
    assert str(tmp_path) in out and "%(id)s" in out


def test_fetch_parses_filepath_and_license(tmp_path, monkeypatch):
    clip = tmp_path / "minecraft" / "Cool_Video [abc].mp4"

    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = f"Cool Video | license: Creative Commons Attribution\n{clip}\n"

    def fake_run(cmd, **kwargs):
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip.write_bytes(b"video")
        return FakeProc()

    monkeypatch.setattr(get_bg.subprocess, "run", fake_run)
    got = get_bg.fetch("https://youtu.be/abc", "minecraft", tmp_path)
    assert got == [clip]


def test_fetch_playlist_returns_all_paths(tmp_path, monkeypatch):
    clips = [tmp_path / "gta" / f"Part_{i} [i{i}].mp4" for i in range(3)]

    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = "\n".join(
            line for i, c in enumerate(clips)
            for line in (f"Part {i} | license: Standard YouTube License", str(c)))

    def fake_run(cmd, **kwargs):
        for c in clips:
            c.parent.mkdir(parents=True, exist_ok=True)
            c.write_bytes(b"v")
        return FakeProc()

    monkeypatch.setattr(get_bg.subprocess, "run", fake_run)
    got = get_bg.fetch("https://youtube.com/playlist?list=PLx", "gta", tmp_path)
    assert got == clips


def test_fetch_raises_with_yt_dlp_error(tmp_path, monkeypatch):
    class FakeProc:
        returncode = 1
        stderr = "ERROR: Video unavailable"
        stdout = ""

    monkeypatch.setattr(get_bg.subprocess, "run", lambda *a, **k: FakeProc())
    with pytest.raises(RuntimeError, match="Video unavailable"):
        get_bg.fetch("https://youtu.be/abc", "minecraft", tmp_path)


def test_fetch_retries_bot_check_with_tv_client(tmp_path, monkeypatch):
    """YouTube's 'Sign in to confirm' challenge -> automatic tv-client retry."""
    clip = tmp_path / "gta" / "V [x].mp4"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class P:
            pass
        p = P()
        if len(calls) == 1:
            p.returncode, p.stderr, p.stdout = 1, "Sign in to confirm you're not a bot", ""
        else:
            clip.parent.mkdir(parents=True, exist_ok=True)
            clip.write_bytes(b"v")
            p.returncode, p.stderr = 0, ""
            p.stdout = f"V | license: Standard YouTube License\n{clip}\n"
        return p

    monkeypatch.setattr(get_bg.subprocess, "run", fake_run)
    assert get_bg.fetch("https://youtu.be/x", "gta", tmp_path) == [clip]
    assert len(calls) == 2
    assert "youtube:player_client=tv" in calls[1]
    assert not any("player_client" in c for c in calls[0])


def test_auto_tag_sorts_by_game():
    assert get_bg.auto_tag("Subway Surfers Gameplay No Copyright") == "subway"
    assert get_bg.auto_tag("ROBLOX Parkour (Vertical)") == "roblox"
    assert get_bg.auto_tag("GTA 5 Mega Ramp") == "gta"
    assert get_bg.auto_tag("Satisfying slime video") == "vertical"


def test_existing_ids_reads_bracketed_filenames(tmp_path):
    (tmp_path / "roblox").mkdir()
    (tmp_path / "roblox" / "Cool_Clip [abc123XY].mp4").write_bytes(b"x")
    (tmp_path / "loose_no_id.mp4").write_bytes(b"x")
    assert get_bg.existing_ids(tmp_path) == {"abc123XY"}


def test_pick_random_entry_never_repeats_library():
    import random
    entries = [{"id": "aaa111"}, {"id": "bbb222"}]
    pick = get_bg.pick_random_entry(entries, {"aaa111"}, random.Random(1))
    assert pick["id"] == "bbb222"
    assert get_bg.pick_random_entry(entries, {"aaa111", "bbb222"},
                                    random.Random(1)) is None


def test_probe_playlist_parses_and_filters_long(monkeypatch):
    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = ("vid1\tSubway fun\t840\n"
                  "vid2\tMarathon stream\t5400\n"     # > 45 min, dropped
                  "malformed line without tabs\n"
                  "vid3\tRoblox run\tNA\n")

    monkeypatch.setattr(get_bg, "_run_ytdlp", lambda cmd: FakeProc())
    entries = get_bg.probe_playlist("https://youtube.com/playlist?list=x")
    assert [e["id"] for e in entries] == ["vid1", "vid3"]


def test_cookies_env_default_is_machine_local(monkeypatch, capsys):
    """BRAINROT_YT_COOKIES makes cookies the default on THIS machine only;
    without it the flag stays off (public repo default)."""
    monkeypatch.setenv("BRAINROT_YT_COOKIES", "chrome")
    seen = {}

    def fake_fetch(url, tag, bg, cookies_browser=None, max_items=10):
        seen["cookies"] = cookies_browser
        return []

    monkeypatch.setattr(get_bg, "fetch", fake_fetch)
    get_bg.main(["https://youtu.be/x", "--tag", "gta"])
    assert seen["cookies"] == "chrome"


def test_cookies_flag_is_optin_passthrough(tmp_path, monkeypatch):
    monkeypatch.setattr(get_bg, "COOKIES_FILE", tmp_path / "absent.txt")
    plain = get_bg.build_download_cmd("https://youtu.be/x", tmp_path)
    assert "--cookies-from-browser" not in plain      # never on by default
    with_cookies = get_bg.build_download_cmd(
        "https://youtu.be/x", tmp_path, cookies_browser="edge")
    assert with_cookies[with_cookies.index("--cookies-from-browser") + 1] == "edge"


def test_cookies_file_beats_browser(tmp_path, monkeypatch):
    """The in-browser export works where DPAPI decryption cannot."""
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setattr(get_bg, "COOKIES_FILE", jar)
    cmd = get_bg.build_download_cmd("https://youtu.be/x", tmp_path,
                                    cookies_browser="chrome")
    assert cmd[cmd.index("--cookies") + 1] == str(jar)
    assert "--cookies-from-browser" not in cmd


def test_stale_cookies_get_a_plain_english_error(tmp_path, monkeypatch):
    """Bot check WITH a cookie file present = the export rotated out;
    the error must say how to fix it, not dump yt-dlp's novel."""
    jar = tmp_path / "cookies.txt"
    jar.write_text("# jar", encoding="utf-8")
    monkeypatch.setattr(get_bg, "COOKIES_FILE", jar)

    class Bot:
        returncode = 1
        stderr = "Sign in to confirm you're not a bot"
        stdout = ""

    monkeypatch.setattr(get_bg.subprocess, "run", lambda *a, **k: Bot())
    with pytest.raises(RuntimeError, match="stale"):
        get_bg.fetch("https://youtu.be/x", "gta", tmp_path)


def test_cookies_file_is_gitignored():
    """The repo is PUBLIC; the session-token file must never be committable."""
    gitignore = (Path(__file__).resolve().parents[1] / ".gitignore") \
        .read_text(encoding="utf-8")
    assert "cookies.txt" in gitignore.splitlines()
