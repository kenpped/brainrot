# RESTORE - blank PC to working brainrot studio

Follow top to bottom. Everything is copy-paste. Works even if this folder,
the venv, or the whole machine is gone - the only prerequisites are Windows,
an internet connection, and a Claude Code subscription.

## 0. Get the code

Either unzip the backup zip somewhere OUTSIDE OneDrive-synced folders if
possible (or inside, it works, OneDrive just syncs more), or:

```
git clone https://github.com/kenpped/brainrot
cd brainrot
```

## 1. System tools (one time, ~5 min)

```
winget install Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
winget install DenoLand.Deno -e --accept-package-agreements --accept-source-agreements
winget install GitHub.cli -e --accept-package-agreements --accept-source-agreements
```

Then CLOSE and REOPEN the terminal (fresh PATH). ffmpeg renders, deno solves
YouTube's download challenge, gh is only needed if you redeploy the public
site.

## 2. Python env (one time, ~3 min)

Put the venv OUTSIDE OneDrive so it doesn't sync thousands of files:

```
python -m venv %USERPROFILE%\.venvs\brainrot
%USERPROFILE%\.venvs\brainrot\Scripts\pip install -r requirements.txt
%USERPROFILE%\.venvs\brainrot\Scripts\pip install -U "yt-dlp[default]"
```

The `yt-dlp[default]` extra matters: it bundles the challenge-solver scripts
the standalone exe lacks.

## 3. Claude Code login (for AI script writing)

```
claude login
```

Browser opens, log in. This powers write-with-AI, ideas, and reddit retells.
Everything else works without it.

## 4. Sanity check

```
%USERPROFILE%\.venvs\brainrot\Scripts\python -m pytest tests -q
```

All green = the machine works.

## 5. Backgrounds

The zip/repo ships no video files on purpose. Two ways to refill:

```
%USERPROFILE%\.venvs\brainrot\Scripts\python make_bg.py          # instant, copyright-free
%USERPROFILE%\.venvs\brainrot\Scripts\python get_bg.py --random  # no-copyright gameplay
```

YouTube downloads need a cookie file when the bot wall appears: install the
Chrome extension "Get cookies.txt LOCALLY", export on youtube.com, save as
`cookies.txt` in this folder. Re-export whenever downloads start failing
again (YouTube rotates sessions while you browse; takes 30 seconds).
NEVER commit cookies.txt - it is your logged-in session. It is gitignored
with a test standing guard.

## 6. Run it

```
%USERPROFILE%\.venvs\brainrot\Scripts\python webapp.py
```

Studio at http://127.0.0.1:8765 - ideas tab, reddit story button, random
clip button, uploads, gallery. Add `--lan` to use it from your phone on the
same wifi. CLI equivalents:

```
python reddit_stories.py --count 3 --bg-tag roblox   # daily reddit videos
python write_script.py "any topic" --cast grump,hype # AI dialogue script
python brainrot.py --script scripts/dopamine.txt --bg backgrounds/ --out out/x.mp4
python ideas.py                                      # pitch me topics
python voices.py --preview                           # hear the voices
```

## 7. Public site (optional, free)

```
gh auth login
powershell -ExecutionPolicy Bypass -File publish.ps1
```

Redeploys https://kenpped.github.io/brainrot/ (or your fork's URL).

## Working with Claude on this project

Point a Claude Code session at this folder and say what you want - "make me
a video about X", "3 reddit stories", "add this playlist". The README
documents every knob (styles.json presets, characters.json cast, phrase
captions, voice fx, front matter keys). Key facts a fresh session needs:
venv lives at %USERPROFILE%\.venvs\brainrot, ffmpeg/deno come from winget,
cookies rotate stale, and celebrity voice cloning is deliberately not a
feature of this project.
