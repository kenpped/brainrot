"""Gate tests for the public landing page and publish script."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


def test_landing_page_exists_with_placeholders():
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert "<title>" in html and "BRAINROT STUDIO" in html
    assert "{{GITHUB_USER}}" in html          # publish.ps1 fills this in
    assert "github.com/{{GITHUB_USER}}/brainrot" in html
    assert "no voice cloning" in html          # the public stance ships too


def test_landing_page_has_no_local_paths():
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert "C:\\" not in html and "KENqH" not in html
    assert "localhost:8765" in html            # quick start mentions the studio


def test_publish_script_is_idempotent_by_design():
    ps1 = (ROOT / "publish.ps1").read_text(encoding="utf-8")
    for guard in ("gh auth status", "release view", "remote get-url"):
        assert guard in ps1                    # every step checks before acting
    assert re.search(r"github\.io/brainrot", ps1)
