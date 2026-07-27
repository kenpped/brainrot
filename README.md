# brainrot pipeline

Script in, 9:16 narrated video with word-by-word captions out. No subscriptions.

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
  brainrot.py
  batch.py
  scripts/          <- one .txt per video
  backgrounds/      <- your gameplay loops (mp4)
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

## Knobs

| Flag | What it does |
|---|---|
| `--voice` | Any edge-tts voice. `edge-tts --list-voices` to see them all. |
| `--rate` | Speech speed. Default `+18%`. Brainrot pacing is usually `+15%` to `+30%`. |
| `--font` | Caption font. Impact is the default look; Anton is the cleaner TikTok one. |
| `--seed` | Fix the random background pick + offset for reproducible renders. |
| `--keep-temp` | Keeps the mp3 and .ass file so you can inspect timing problems. |

Caption size, outline thickness, and vertical position live in `build_ass()` —
the `Style: Pop,...` line. Fontsize 130 and Outline 9 is the loud default.

## Tests + evals

Two lanes:

```
# gate: pure logic, no network, no ffmpeg, < 2s -- run on every change
python -m pytest tests -q

# eval: real edge-tts + whisper + ffmpeg render, checked with ffprobe --
# run before ship or after touching the render path (needs network)
python eval/eval_render.py
```

The eval renders `out/eval_sample.mp4` against a synthetic background and
fails unless resolution, audio, duration, caption count, and word-match
ratio all clear their thresholds.

## First run is slow

Whisper (via faster-whisper — same models, no torch install, quicker on CPU)
downloads its model the first time (~150MB for `base`). After that it's
cached. If transcription is too slow on your machine, change `WHISPER_MODEL`
to `"tiny"` in brainrot.py — word timings get slightly sloppier but it's
noticeably faster.

## Known rough edges

- **Font not found**: ffmpeg needs the font installed system-wide, not just
  downloaded. On Linux you may need to install Impact or switch to a font you
  have.
- **Whisper mishears a word**: it transcribes the generated audio, so unusual
  names or slang can come out wrong in the captions even though the audio is
  fine. Spell things phonetically in the script if this bites.
- **Background footage is copyrighted.** Gameplay capture belongs to whoever
  recorded it, and the game to its publisher. Same exposure any brainrot
  channel has — just yours to manage now.
