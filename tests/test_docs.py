"""Gate tests for the public landing page and publish script."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


def test_landing_page_valid_before_and_after_personalization():
    """publish.ps1 swaps {{GITHUB_USER}} for the real username, so the page
    is valid in either state (a stricter placeholder-only assert once made
    the pre-commit hook block the publish commit)."""
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert "<title>" in html and "BRAINROT STUDIO" in html
    assert not html.startswith("﻿"), "BOM would break the placeholder swap"
    assert re.search(r"github\.com/(\{\{GITHUB_USER\}\}|[A-Za-z0-9-]+)/brainrot", html)
    assert re.search(r"releases/download/v0\.1/robot-demon\.mp4", html)
    assert "no voice cloning" in html          # the public stance ships too


def test_landing_page_has_no_local_paths():
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert "C:\\" not in html and "KENqH" not in html
    assert "localhost:8765" in html            # quick start mentions the studio


def test_publish_script_is_idempotent_by_design():
    ps1 = (ROOT / "publish.ps1").read_text(encoding="utf-8")
    for guard in ("auth status", "release view", '-contains "origin"'):
        assert guard in ps1                    # every step checks before acting
    assert "UTF8Encoding" in ps1               # BOM-free writes, PS 5.1 safe
    code = [l for l in ps1.splitlines() if not l.strip().startswith("#")]
    assert all("Set-Content" not in l for l in code)  # writes BOM under PS 5.1
    assert re.search(r"github\.io/brainrot", ps1)
