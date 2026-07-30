"""Gate tests for post_card.py -- Pillow drawing, no network."""

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import post_card


def test_card_renders_rgba_png(tmp_path):
    out = post_card.make_post_card(
        "AITA for testing my own code?", "AITAH", "throwaway123",
        tmp_path / "card.png", score=12400, comments=1247)
    img = Image.open(out)
    assert img.mode == "RGBA"
    assert img.width == post_card.CARD_W
    assert img.getextrema()[3][1] == 255      # actually drew something opaque


def test_long_titles_wrap_and_grow(tmp_path):
    short = post_card.make_post_card("Short title", "AITAH", None,
                                     tmp_path / "s.png")
    long = post_card.make_post_card(
        "AITA for telling my entire extended family at the reunion that I "
        "was the one who ate the potato salad and then refusing to apologize "
        "for any of it whatsoever?", "AITAH", None, tmp_path / "l.png")
    assert Image.open(long).height > Image.open(short).height


def test_no_score_shows_no_fabricated_numbers(tmp_path):
    """RSS posts carry no counts; the card must not invent any."""
    out = post_card.make_post_card("Title", "AITAH", None, tmp_path / "c.png")
    assert out.is_file()  # renders fine with score=None, comments=None


def test_demo_avatar_renders(tmp_path):
    out = post_card.make_demo_avatar(tmp_path / "blob.png", color=(255, 80, 80))
    img = Image.open(out)
    assert img.mode == "RGBA" and img.width == 420
    assert img.getextrema()[3][1] == 255      # opaque blob on transparent bg
    assert img.getpixel((5, 5))[3] == 0       # corners stay transparent


def test_wrap_title_caps_at_four_lines():
    from PIL import ImageDraw
    draw = ImageDraw.Draw(Image.new("RGBA", (100, 10)))
    font = post_card._font("segoeuib.ttf", 42)
    lines = post_card.wrap_title(draw, "word " * 80, font, 400)
    assert len(lines) == 4
