# brainrot pipeline

Script in, 9:16 narrated video with word-by-word captions out. No subscriptions.
Monologues or two-voice dialogues, style presets, per-script overrides, a
topic-to-script generator that runs on your local Claude Code, and a local
web studio so you never have to touch the terminal.

## The website (easiest way to use all of it)

```
python webapp.py
```

Open http://127.0.0.1:8765 — type a topic (AI writes the script) or paste
your own, pick style / voice / speed / background, hit generate. Jobs run one
at a time with live progress; finished videos play in the gallery on the
right. Local only: binds 127.0.0.1, nothing is uploaded anywhere.

## Setup

**1. Install ffmpeg**

- Windows: `winget install Gyan.FFmpeg` (then reopen your terminal)
- Mac: `brew install ffmpeg`
- Linux: `sudo apt install ffmpeg`

Verify with `ffmpeg -version`. If that fails, nothing else will work.
ffprobe ships in the same install and is also required.

**2. Install the Python packages**

```
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

If the project lives inside OneDrive (like this machine), put the venv
outside it so OneDrive doesn't sync thousands of package files:

```
python -m venv C:\Users\KENqH\.venvs\brainrot
C:\Users\KENqH\.venvs\brainrot\Scripts\activate
pip install -r requirements.txt
```

**3. Set up folders**

```
project/
  webapp.py         <- the website (python webapp.py -> localhost:8765)
  web/index.html    <- its page
  brainrot.py       <- render one video
  batch.py          <- render a folder of scripts
  write_script.py   <- topic -> script, via local Claude Code
  voices.py         <- browse + preview voices
  make_bg.py        <- generate copyright-free backgrounds
  styles.json       <- style presets
  scripts/          <- one .txt per video (web/ subfolder = site-made)
  backgrounds/      <- gameplay loops, organized by tag subfolder
    minecraft/
    subway/
    synthetic/      <- make_bg.py output
  out/
```

Backgrounds should be at least as long as your videos, ideally 10+ minutes so
the random offset actually varies. Vertical or horizontal both work — the
script crops to 1080x1920 either way. Too-short backgrounds get looped.

## Run one

```
python brainrot.py --script scripts/dopamine.txt --bg backgrounds/ --out out/dopamine.mp4
```

## Run a batch

```
python batch.py --scripts scripts/ --bg backgrounds/ --outdir out/ --rotate-voices
```

Already-rendered files are skipped, so you can kill it and resume.

## Write a script from a topic

Uses the local `claude` CLI (your Claude Code install), not an API key:

```
python write_script.py "why school is a scam"
python write_script.py "credit card traps" --dialogue --style hype --bg minecraft
```

Output lands in `scripts/<slug>.txt` with front matter filled in, ready to render.

## Styles

`styles.json` bundles voice + rate + font + caption look into named presets:
`default`, `hype`, `deep`, `chill`, `uk`, `storytime`. See them with
`python brainrot.py --list-styles`, use one with `--style hype`, add your own
by editing the json (gate tests validate it).

## Per-script customization (front matter)

Any script can carry its own settings at the top, closed by `---`:

```
voice: en-US-BrianNeural
rate: +25%
font: Georgia
style: hype
bg: minecraft
---
The actual script text starts here.
```

Precedence, weakest to strongest: defaults < style preset < batch voice
rotation < front matter < explicit CLI flags.

## Dialogue mode

Declare `speakers`, then write `name: line` dialogue. Each speaker gets their
own voice, a short pause between lines, and their own caption color (first
speaker white, second yellow, then cyan, lime, pink):

```
speakers: A=en-US-BrianNeural, B=en-US-JennyNeural
---
A: Chat, why is nobody talking about this?
B: Because nobody wants to admit it works.
```

See `scripts/phone-bowl-speaker.txt` for a full example.

## Characters (the Peter-and-Stewie format, minus the lawsuit)

`characters.json` defines named personas: voice + pitch shift + speed bump +
caption color + a personality the AI writes dialogue in. Shipped cast:
`grump`, `hype`, `posh`, `deadpan`, `brat`. Use them in a script:

```
cast: grump, hype
---
grump: Wait, this thing does characters now?
hype: Two personalities, two pitches, and my words show up in yellow!
```

Or let the AI write in-character:

```
python write_script.py "why pigeons fear nobody" --cast grump,hype
```

On the site, tick "2 characters" and pick the matchup. Add your own
characters by editing the json (gate tests validate it); pitch is what makes
stock voices sound like different people (`-18Hz` old man, `+14Hz` hyper kid).

Deliberately NOT included: cloned celebrity or cartoon voices (Peter
Griffin, Obama, etc.). Those are a real person's voice used without consent,
and they're the number one thing that gets brainrot channels struck and
banned. Original characters are yours forever.

## Voices

```
python voices.py                # curated list with vibe notes
python voices.py --preview      # hear them: one sample mp3 per voice
edge-tts --list-voices          # the full catalog (hundreds)
```

## Backgrounds

Organize `backgrounds/` into tag subfolders (`minecraft/`, `gta/`, `subway/`,
`roblox/`, ...). Pick one per video with `bg: minecraft` front matter or
`--bg-tag minecraft`; with no tag, any video anywhere under `backgrounds/`
can be picked.

No footage yet? Generate copyright-free satisfying loops:

```
python make_bg.py --minutes 5        # neon bouncing balls + warp tunnel
```

For real gameplay, record it yourself: Win+G (Game Bar) is built into
Windows, OBS for longer sessions. Your own Roblox games are 100% yours to
record. Publisher rules differ per game (Mojang and Rockstar publish video
content policies) — footage downloaded from someone else's channel is theirs,
not yours.

## Knobs

| Flag | What it does |
|---|---|
| `--style` | Preset from styles.json. `--list-styles` to see them. |
| `--voice` | Any edge-tts voice. `python voices.py` to browse. |
| `--rate` | Speech speed. Default `+18%`. Brainrot pacing is usually `+15%` to `+30%`. |
| `--font` | Caption font. Impact is the default look. Must be installed system-wide. |
| `--bg-tag` | Only use backgrounds from that subfolder. |
| `--seed` | Fix the random background pick + offset for reproducible renders. |
| `--keep-temp` | Keeps the mp3/wav and .ass file so you can inspect timing problems. |

Caption size, outline thickness, vertical position, and highlight colors are
style fields (`fontsize`, `outline`, `margin_v`, `highlight`,
`highlight_chance`) — set them in styles.json or per script in front matter.
Fontsize 130 and Outline 9 is the loud default. Random accent-colored words
are on by default; `highlight: none` turns them off.

## Tests + evals

Two lanes:

```
# gate: pure logic, no network, no ffmpeg, < 2s -- run on every change
python -m pytest tests -q

# eval: real edge-tts + whisper + ffmpeg renders, checked with ffprobe --
# run before ship or after touching the render path (needs network)
python eval/eval_render.py

# eval: a real render driven through the web app (worker, HTTP, Range serving)
python eval/eval_webapp.py
```

The eval renders four things (default style, front-matter overrides,
generated background, dialogue) and fails unless resolution, audio, duration,
caption count, word-match ratio, speaker colors, and audio joining all clear
their thresholds. Outputs land in `out/` so you can watch them.

## First run is slow

Whisper (via faster-whisper — same models, no torch install, quicker on CPU)
downloads its model the first time (~150MB for `base`). After that it's
cached. If transcription is too slow on your machine, change `WHISPER_MODEL`
to `"tiny"` in brainrot.py — word timings get slightly sloppier but it's
noticeably faster.

## Known rough edges

- **Font not found**: ffmpeg needs the font installed system-wide, not just
  downloaded. The renderer warns if the font isn't in the Windows font
  registry, then ffmpeg substitutes something else.
- **Whisper mishears a word**: it transcribes the generated audio, so unusual
  names or slang can come out wrong in the captions even though the audio is
  fine. Spell things phonetically in the script if this bites.
- **Dialogue color bleed**: caption colors follow computed line boundaries;
  whisper timing drift can tint the first/last word of a line with the
  neighbor's color. Cosmetic, rare, self-corrects next word.
- **Background footage is copyrighted.** Gameplay capture belongs to whoever
  recorded it, and the game to its publisher. Same exposure any brainrot
  channel has — just yours to manage now. `make_bg.py` output and your own
  gameplay are the zero-exposure options.
