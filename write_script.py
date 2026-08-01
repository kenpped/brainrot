#!/usr/bin/env python3
"""write_script.py -- topic in, ready-to-render script file out.

Uses the LOCAL Claude Code CLI (`claude -p`), not an API key, so it costs
nothing extra on a Claude subscription.

    python write_script.py "why school is a scam"
    python write_script.py "credit card traps" --dialogue
    python write_script.py "gym motivation" --style hype --bg minecraft

The output lands in scripts/<slug>.txt with front matter already filled in,
so the next step is just brainrot.py / batch.py.

Hardening (learned the hard way): `claude -p` run inside a repo behaves like
an agent -- it reads files, tries to write them, adds commentary, and can
wander off topic. So the call runs from an empty temp directory, the script
must come back between BEGIN SCRIPT / END SCRIPT sentinel lines, the body
must mention the topic, and one retry happens before giving up.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from brainrot import load_characters, parse_dialogue, parse_script

DEFAULT_VOICE_A = "en-US-BrianNeural"
DEFAULT_VOICE_B = "en-US-JennyNeural"
SENTINEL_BEGIN = "BEGIN SCRIPT"
SENTINEL_END = "END SCRIPT"
STOPWORDS = {
    "why", "your", "you", "the", "a", "an", "is", "are", "was", "in", "on",
    "of", "to", "for", "and", "or", "it", "at", "my", "our", "their", "his",
    "her", "its", "do", "does", "how", "what", "when", "so", "that", "this",
    "with", "from", "about", "than", "into", "some", "more",
}

GUARD = """\
You are a text generator. Do not read or write files, do not run tools, do
not explain yourself, and do not add anything before or after the block.

"""

MONO_PROMPT = GUARD + """\
Write a short script for a vertical brainrot-style video about: {topic}

Rules:
- 100 to 130 words, spoken prose only
- cold-open hook in the first sentence, no greeting
- short punchy sentences, one idea each
- a re-hook in the middle, something like "here is the wild part"
- end with a twist or a call to action
- stay strictly on the topic: {topic}
- no emojis, no hashtags, no stage directions, no markdown

Output EXACTLY this shape and nothing else:
BEGIN SCRIPT
<the script text>
END SCRIPT"""

DIALOGUE_PROMPT = GUARD + """\
Write a short two-speaker dialogue script for a vertical brainrot-style video
about: {topic}

Speakers: A (skeptic, casual, asks what everyone is thinking) and
B (knows the answer, confident, slightly smug).

Rules:
- 110 to 150 words total, 8 to 14 short lines
- every line starts with exactly "A: " or "B: "
- A opens with a hook question or a wild claim
- B explains in punchy plain language; A pushes back at least once
- stay strictly on the topic: {topic}
- no emojis, no stage directions, no markdown

Output EXACTLY this shape and nothing else:
BEGIN SCRIPT
A: first line
B: second line
END SCRIPT"""

CAST_PROMPT = GUARD + """\
Write a short two-character dialogue script for a vertical brainrot-style
video about: {topic}

The characters (keep them strongly in personality the whole time):
- {a}: {persona_a}
- {b}: {persona_b}

Rules:
- 110 to 150 words total, 8 to 14 short lines
- every line starts with exactly "{a}: " or "{b}: "
- {a} opens with a hook question or a wild claim
- the personality clash IS the comedy; let them bicker while the facts land
- stay strictly on the topic: {topic}
- no emojis, no stage directions, no markdown

Output EXACTLY this shape and nothing else:
BEGIN SCRIPT
{a}: first line
{b}: second line
END SCRIPT"""

EDU_PROMPT = GUARD + """\
Write a short EDUCATIONAL script for a vertical brainrot-style video about:
{topic}

Rules:
- 130 to 160 words, spoken prose only
- cold-open with a question or claim that sounds fake but is true
- one line on why the viewer should care
- exactly three concrete facts, each with a real number, date, or comparison
- close with a twist that reframes everything, then one-line takeaway
- accuracy matters: no invented statistics, no urban legends
- stay strictly on the topic: {topic}
- no emojis, no hashtags, no stage directions, no markdown

Output EXACTLY this shape and nothing else:
BEGIN SCRIPT
<the script text>
END SCRIPT"""

RETRY_SUFFIX = (
    "\n\nREMINDER: your previous attempt failed validation. Output ONLY the "
    "BEGIN SCRIPT / END SCRIPT block, on topic, with no commentary, no file "
    "talk, and no markdown."
)


def slugify(topic: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "script"


def build_prompt(topic: str, dialogue: bool,
                 cast: list[str] | None = None,
                 characters: dict | None = None,
                 edu: bool = False) -> str:
    if cast and characters:
        a, b = cast[0], cast[1]
        return CAST_PROMPT.format(
            topic=topic, a=a, b=b,
            persona_a=characters[a]["persona"],
            persona_b=characters[b]["persona"],
        )
    if edu and not dialogue:
        return EDU_PROMPT.format(topic=topic)
    template = DIALOGUE_PROMPT if dialogue else MONO_PROMPT
    return template.format(topic=topic)


def strip_fences(text: str) -> str:
    """Models sometimes wrap output in ``` fences despite instructions."""
    lines = [l for l in text.strip().splitlines() if not l.strip().startswith("```")]
    return "\n".join(lines).strip()


def extract_script(raw: str) -> str | None:
    """The text between the sentinel lines, or None. Everything outside the
    sentinels (agentic commentary, notes, apologies) is discarded."""
    m = re.search(
        rf"^{SENTINEL_BEGIN}\s*$(.*?)^{SENTINEL_END}\s*$",
        raw, re.DOTALL | re.MULTILINE,
    )
    return strip_fences(m.group(1)) if m else None


def topic_words(topic: str) -> list[str]:
    return [w for w in re.findall(r"[a-z']+", topic.lower())
            if len(w) >= 4 and w not in STOPWORDS]


def on_topic(body: str, topic: str) -> bool:
    """Cheap drift detector: at least one content word of the topic must
    appear in the script. Caught a real case of Claude writing about
    radioactive bananas when asked about phone batteries."""
    words = topic_words(topic)
    return not words or any(w in body.lower() for w in words)


def build_front_matter(dialogue: bool, style: str | None, bg: str | None,
                       voice_a: str, voice_b: str,
                       cast: list[str] | None = None,
                       avatar: str | None = None) -> str:
    keys = []
    if cast:
        keys.append(f"cast: {', '.join(cast)}")
    elif dialogue:
        keys.append(f"speakers: A={voice_a}, B={voice_b}")
    if style:
        keys.append(f"style: {style}")
    if bg:
        keys.append(f"bg: {bg}")
    if avatar and not (cast or dialogue):
        keys.append(f"avatar: {avatar}")   # solo narrator PNG
    return ("\n".join(keys) + "\n---\n") if keys else ""


def ask_claude(prompt: str, timeout: int = 300) -> str:
    # shutil.which resolves Windows shims like claude.cmd (npm installs);
    # a bare ["claude", ...] subprocess can't.
    exe = shutil.which("claude")
    if exe is None:
        raise RuntimeError(
            "claude CLI not found on PATH - install Claude Code or write the script by hand"
        )
    # empty temp cwd = no CLAUDE.md, no repo to explore, nothing to "help" with.
    # Prompt goes via STDIN: the npm claude.cmd shim re-parses argv through
    # cmd.exe, which truncates the argument at the first newline -- the model
    # then sees one line of the prompt and no topic at all.
    workdir = tempfile.mkdtemp(prefix="brainrot_ws_")
    proc = subprocess.run(
        [exe, "-p"], input=prompt, cwd=workdir,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, shell=False,
    )
    if proc.returncode != 0:
        # auth errors land on stdout, so fall back to it when stderr is empty
        detail = proc.stderr.strip() or proc.stdout.strip()[:300]
        raise RuntimeError(f"claude -p failed ({proc.returncode}): {detail}")
    return proc.stdout


def generate(topic: str, dialogue: bool, attempts: int = 2,
             cast: list[str] | None = None,
             characters: dict | None = None,
             edu: bool = False) -> str:
    """Ask, validate, retry once, or fail loudly with the raw reply."""
    prompt = build_prompt(topic, dialogue, cast=cast, characters=characters,
                          edu=edu)
    last_raw, last_reason = "", "no attempt made"
    for i in range(attempts):
        raw = ask_claude(prompt if i == 0 else prompt + RETRY_SUFFIX)
        last_raw = raw
        body = extract_script(raw)
        if body is None:
            last_reason = "no BEGIN SCRIPT / END SCRIPT block"
            continue
        if len(body.split()) < 40:
            last_reason = f"only {len(body.split())} words"
            continue
        if not on_topic(body, topic):
            last_reason = "script ignores the topic"
            continue
        return body
    preview = " ".join(last_raw.split())[:200]
    raise RuntimeError(
        f"Claude returned invalid script output twice ({last_reason}). "
        f"Reply started: {preview!r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("topic", help="what the video is about")
    ap.add_argument("--dialogue", action="store_true",
                    help="two-speaker back-and-forth instead of a monologue")
    ap.add_argument("--cast", default=None,
                    help="two characters.json names, e.g. grump,hype "
                         "(implies --dialogue, personas steer the writing)")
    ap.add_argument("--edu", action="store_true",
                    help="educational structure: hook, why care, three real "
                         "facts with numbers, twist close")
    ap.add_argument("--avatar", default=None,
                    help="solo narrator PNG (from avatars/), e.g. peter.png")
    ap.add_argument("--style", default=None, help="styles.json preset to bake in")
    ap.add_argument("--bg", default=None, help="background tag to bake in, e.g. minecraft")
    ap.add_argument("--voice-a", default=DEFAULT_VOICE_A, help="dialogue speaker A voice")
    ap.add_argument("--voice-b", default=DEFAULT_VOICE_B, help="dialogue speaker B voice")
    ap.add_argument("--outdir", type=Path,
                    default=Path(__file__).resolve().parent / "scripts")
    args = ap.parse_args(argv)

    cast = None
    characters = None
    if args.cast:
        cast = [n.strip().lower() for n in args.cast.split(",") if n.strip()]
        characters = load_characters()
        unknown = [n for n in cast if n not in characters]
        if len(cast) != 2 or unknown:
            print(f"error: --cast needs exactly 2 names from "
                  f"{', '.join(sorted(characters))}"
                  + (f" (unknown: {', '.join(unknown)})" if unknown else ""),
                  file=sys.stderr)
            return 1
        args.dialogue = True

    mode = f"{cast[0]} vs {cast[1]}" if cast else \
        ("dialogue" if args.dialogue else "monologue")
    print(f"asking local Claude for a {mode} about: {args.topic}", flush=True)
    try:
        body = generate(args.topic, args.dialogue, cast=cast,
                        characters=characters, edu=args.edu)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    front = build_front_matter(args.dialogue, args.style, args.bg,
                               args.voice_a, args.voice_b, cast=cast,
                               avatar=args.avatar)
    args.outdir.mkdir(parents=True, exist_ok=True)
    dest = args.outdir / f"{slugify(args.topic)}.txt"
    dest.write_text(front + body + "\n", encoding="utf-8")

    try:
        parse_script(dest)  # same validation the renderer will run
        if cast:             # and the cast lines must actually parse as dialogue
            parse_dialogue(body, {n: characters[n] for n in cast})
    except ValueError as e:
        print(f"error: generated script failed validation: {e}", file=sys.stderr)
        print(f"raw output kept at {dest} - fix it by hand or rerun", file=sys.stderr)
        return 1
    print(f"wrote {dest} ({len(body.split())} words)")
    print(f"render it: python brainrot.py --script {dest} --bg backgrounds/ "
          f"--out out/{dest.stem}.mp4")
    return 0


if __name__ == "__main__":
    sys.exit(main())
