#!/usr/bin/env python3
"""post_card.py -- draw a Reddit-style post header card as a transparent PNG.

The card sits at the top of story videos (title, subreddit, author, vote and
comment row), the way every reddit-story channel frames the hook. Pure
Pillow, deterministic: same inputs, same pixels.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CARD_W = 940
PAD = 36
RADIUS = 28
BG = (26, 26, 27, 245)          # reddit dark card
WHITE = (235, 236, 239, 255)
GRAY = (129, 131, 132, 255)
ORANGE = (255, 69, 0, 255)
UPVOTE = (255, 139, 96, 255)

_FONT_DIR = Path("C:/Windows/Fonts")


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = _FONT_DIR / name
    if path.is_file():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


def wrap_title(draw: ImageDraw.ImageDraw, title: str,
               font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines, current = [], ""
    for word in title.split():
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_w or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:4]  # a four-line title is already a novel


def make_post_card(
    title: str,
    subreddit: str,
    author: str | None,
    out_png: Path,
    score: int | None = None,
    comments: int | None = None,
    width: int = CARD_W,
) -> Path:
    f_sub = _font("segoeuib.ttf", 30)
    f_meta = _font("segoeui.ttf", 26)
    f_title = _font("segoeuib.ttf", 42)

    probe = ImageDraw.Draw(Image.new("RGBA", (width, 10)))
    title_lines = wrap_title(probe, title.strip(), f_title, width - 2 * PAD)
    line_h = 54
    head_h = 46 + 34            # sub row + posted-by row
    meta_h = 44
    height = PAD * 2 + head_h + 14 + len(title_lines) * line_h + 16 + meta_h

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, width - 1, height - 1], RADIUS, fill=BG)

    x, y = PAD, PAD
    draw.ellipse([x, y, x + 40, y + 40], fill=ORANGE)
    draw.text((x + 12, y + 4), "r/", font=f_sub, fill=WHITE)
    draw.text((x + 54, y + 2), f"r/{subreddit}", font=f_sub, fill=WHITE)
    y += 46
    by = f"Posted by u/{author}" if author else "Posted from the front page"
    draw.text((x, y), by, font=f_meta, fill=GRAY)
    y += 34 + 14

    for line in title_lines:
        draw.text((x, y), line, font=f_title, fill=WHITE)
        y += line_h
    y += 16

    up = f"{score:,}" if score is not None else "top post"
    meta = f"\u25b2 {up}"
    if comments is not None:
        meta += f"      \U0001f4ac {comments:,}"
    meta += "      Share"
    draw.text((x, y), meta, font=f_meta, fill=UPVOTE)

    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    return out_png
