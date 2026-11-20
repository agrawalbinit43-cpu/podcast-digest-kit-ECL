# SETUP

## 1. Install

Needs Python 3.9 or later.

```
git clone https://github.com/agrawalbinit43-cpu/podcast-digest-kit-ECL.git brave-new-law
cd brave-new-law
pip3 install -r requirements.txt
python3 tools/bnl.py feeds          # every curated feed should show OK
python3 tools/bnl.py search "legal ai"
```

## 2. Plug it into a bot

1. Give the bot read and write access to this folder and the ability to run shell commands.
2. Load `SKILL.md` as a skill, or paste it into the bot's standing instructions.
3. Set `BNL_LIBRARY` to a private folder for transcripts, records and digests. The default is `library/` here, which git ignores.
4. Smoke test: ask the bot "What's new in legal AI podcasts today? Brief the top one." Check that the brief passes `validate` and that the quotes sound right.

## 3. Optional keys

None are needed. Each adds coverage or depth.

| Key | Adds | Cost |
|---|---|---|
| `PODCASTINDEX_KEY`, `PODCASTINDEX_SECRET` | More shows in search | Free at podcastindex.org |
| `STT_OPENAI_BASE_URL`, `STT_OPENAI_KEY` | Whisper transcripts when no captions exist | About INR 30 per hour of audio on OpenAI. Needs `ffmpeg`. |

Keep keys in the bot's secret store or environment. Never put them in this repo.

## 4. Daily roundup on a schedule

Ask the bot to run the roundup every morning, for example "every day at 8 AM India time". The 24-hour window then tiles with the schedule and nothing is missed.
