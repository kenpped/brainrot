"""Gate tests for batch.py planning logic -- no rendering, no network."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import batch


def make_scripts(tmp_path, names):
    sdir = tmp_path / "scripts"
    sdir.mkdir()
    for n in names:
        (sdir / n).write_text("hello", encoding="utf-8")
    return sdir


def test_plan_jobs_sorted_and_named(tmp_path):
    sdir = make_scripts(tmp_path, ["b.txt", "a.txt", "notes.md"])
    jobs = batch.plan_jobs(sdir, tmp_path / "out")
    assert [j.script.name for j in jobs] == ["a.txt", "b.txt"]  # .md ignored
    assert [j.out.name for j in jobs] == ["a.mp4", "b.mp4"]


def test_plan_jobs_skips_existing_nonempty(tmp_path):
    sdir = make_scripts(tmp_path, ["a.txt", "b.txt"])
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "a.mp4").write_bytes(b"rendered")
    (outdir / "b.mp4").write_bytes(b"")  # empty = failed render, redo it
    jobs = batch.plan_jobs(sdir, outdir)
    assert [j.skip for j in jobs] == [True, False]


def test_plan_jobs_fixed_voice_by_default(tmp_path):
    sdir = make_scripts(tmp_path, ["a.txt", "b.txt"])
    jobs = batch.plan_jobs(sdir, tmp_path / "out", voice="en-US-GuyNeural")
    assert {j.voice for j in jobs} == {"en-US-GuyNeural"}


def test_plan_jobs_rotation_cycles_and_is_stable(tmp_path):
    n = len(batch.VOICE_ROTATION)
    names = [f"s{i:02d}.txt" for i in range(n + 2)]
    sdir = make_scripts(tmp_path, names)
    jobs = batch.plan_jobs(sdir, tmp_path / "out", rotate=True)
    assert [j.voice for j in jobs[:n]] == batch.VOICE_ROTATION
    assert jobs[n].voice == batch.VOICE_ROTATION[0]      # wraps around
    assert jobs[n + 1].voice == batch.VOICE_ROTATION[1]
    # skipping a rendered script must not shift later voices (resume-stable)
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "s00.mp4").write_bytes(b"rendered")
    again = batch.plan_jobs(sdir, outdir, rotate=True)
    assert [j.voice for j in again] == [j.voice for j in jobs]


def test_plan_jobs_empty_dir_raises(tmp_path):
    sdir = tmp_path / "scripts"
    sdir.mkdir()
    with pytest.raises(ValueError):
        batch.plan_jobs(sdir, tmp_path / "out")
