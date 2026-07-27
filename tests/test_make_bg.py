"""Gate tests for make_bg.py generator math -- no ffmpeg, no encoding."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import make_bg

W, H = 64, 96  # tiny frames keep these tests instant


def test_step_balls_stays_in_bounds():
    rng = np.random.default_rng(1)
    n = 8
    radii = rng.uniform(3, 6, n).astype(np.float32)
    pos = np.column_stack([
        rng.uniform(radii, W - radii), rng.uniform(radii, H - radii),
    ]).astype(np.float32)
    vel = rng.uniform(-9, 9, (n, 2)).astype(np.float32)
    for _ in range(300):
        pos, vel = make_bg.step_balls(pos, vel, radii, W, H)
    assert (pos[:, 0] >= radii - 1e-3).all() and (pos[:, 0] <= W - radii + 1e-3).all()
    assert (pos[:, 1] >= radii - 1e-3).all() and (pos[:, 1] <= H - radii + 1e-3).all()


def test_step_balls_caps_speed():
    radii = np.array([3.0], dtype=np.float32)
    pos = np.array([[30.0, 30.0]], dtype=np.float32)
    vel = np.array([[500.0, -500.0]], dtype=np.float32)
    _, vel = make_bg.step_balls(pos, vel, radii, W, H, max_speed=18.0)
    assert (np.abs(vel) <= 18.0).all()


def test_gen_balls_frame_count_shape_and_determinism():
    frames_a = list(make_bg.gen_balls(1.0, 10, W, H, seed=3))
    frames_b = list(make_bg.gen_balls(1.0, 10, W, H, seed=3))
    assert len(frames_a) == 10
    assert frames_a[0].shape == (H, W, 3)
    assert frames_a[0].dtype == np.float32
    np.testing.assert_array_equal(frames_a[-1], frames_b[-1])
    assert (frames_a[-1] >= 0).all()


def test_gen_balls_actually_draws_something():
    last = list(make_bg.gen_balls(0.5, 10, W, H, seed=3))[-1]
    assert last.max() > 100  # neon balls on black, not an empty frame


def test_tunnel_frame_shape_and_range():
    frames = list(make_bg.gen_tunnel(0.3, 10, W, H, seed=7))
    assert len(frames) == 3
    f = frames[-1]
    assert f.shape == (H, W, 3)
    assert f.min() >= 0.0 and f.max() <= 255.0
    assert f.std() > 10  # visibly patterned, not flat


def test_tunnel_is_animated():
    frames = list(make_bg.gen_tunnel(0.3, 10, W, H, seed=7))
    assert not np.array_equal(frames[0], frames[-1])
