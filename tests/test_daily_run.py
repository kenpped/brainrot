"""Gate tests for daily_run.py -- pure planning logic, no subprocesses."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import daily_run
import write_script as ws


def test_split_counts():
    assert daily_run.split_counts(5) == (3, 2)
    assert daily_run.split_counts(3) == (2, 1)
    assert daily_run.split_counts(2) == (1, 1)
    assert daily_run.split_counts(1) == (1, 0)
    stories, edu = daily_run.split_counts(10)
    assert stories + edu == 10 and stories >= 1 and edu >= 1


def test_list_avatars_sorted_pngs(tmp_path):
    for name in ("zeta.png", "alpha.png", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    assert daily_run.list_avatars(tmp_path) == ["alpha.png", "zeta.png"]


def test_list_avatars_makes_fallback_blob(tmp_path):
    got = daily_run.list_avatars(tmp_path)
    assert got == ["blob-blue.png"]
    assert (tmp_path / "blob-blue.png").is_file()   # real PNG drawn on demand


def test_fresh_ideas_skips_already_made(tmp_path):
    ideas = [{"title": "Why casinos removed clocks"},
             {"title": "The free coffee trap"},
             {"title": "Poverty and IQ"}]
    made = tmp_path / f"{ws.slugify('Why casinos removed clocks')}.txt"
    made.write_text("done", encoding="utf-8")
    picks = daily_run.fresh_ideas(ideas, tmp_path, 2)
    assert [p["title"] for p in picks] == ["The free coffee trap",
                                           "Poverty and IQ"]


def test_solo_avatar_front_matter():
    fm = ws.build_front_matter(False, "deep", None, "V-A", "V-B",
                               avatar="peter.png")
    assert "avatar: peter.png" in fm and "speakers" not in fm
    # dialogue never gets the solo avatar key
    fm = ws.build_front_matter(True, None, None, "V-A", "V-B",
                               avatar="peter.png")
    assert "avatar:" not in fm
