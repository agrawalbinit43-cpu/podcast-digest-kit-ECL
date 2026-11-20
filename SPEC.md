# SPEC: how the Brave New Law kit works

This file explains what `tools/bnl.py` does at each step and the traps it guards against. `SKILL.md` tells the bot when to run each step.

## 1. Rules that win every conflict

| # | Rule | Why |
|---|---|---|
| R1 | The transcript is the source of truth. | Show notes say what an episode is about. The transcript says what people said. |
| R2 | Every claim traces to the transcript. Quotes are word for word or left out. | A lawyer may repeat it in a meeting. A false quote is worse than none. |
| R3 | Transcripts stay on disk and enter the bot's context once. | Long transcripts in chat cause failed runs and big token bills. |
| R4 | A record is valid only after `validate` passes. | It catches word limits, dashes, bad quotes and stray numbers. |

## 2. Search (`search`, `trending`)

The window is the last 24 hours by default (`--hours`).

Sources, all free:

| Source | What it gives | Notes |
|---|---|---|
| Curated RSS (`sources/feeds.json`) | Every new episode from 21 legal shows | Exact dates. A dead feed logs a warning and the run goes on. |
| Apple podcast search | Episodes from any show matching the topic terms | `itunes.apple.com/search`, `entity=podcastEpisode`. Exact dates. |
| YouTube search | Long talks and video podcasts, with view counts | YouTube's own "today" filter sets the window. Needs `yt-dlp`. |
| Podcast Index | Extra shows by term | Only when `PODCASTINDEX_KEY` and `PODCASTINDEX_SECRET` are set. Free sign-up. |

Topic expansion: `sources/topics.json` maps a topic name or alias to phrases (`terms`) and single words (`keywords`). A keyword counts only when no phrase matched, so "ai" can match legal AI talks without "public" matching everything.

Ranking score = topic match (3 per phrase) + law words (up to 3) + views (0.6 x log10, capped) + freshness (up to 2) + curated show (3) + podcast format (1.5) minus 3 for non-English titles from uncurated channels.

Filters drop:
- items under 15 minutes, and YouTube items with no length (shorts, live streams);
- exam coaching, live court streams, reaction clips (`NOISE` in the code);
- uncurated items with no topic match or no law word;
- curated items with no topic match;
- duplicate titles across sources.

Every search is saved to `$BNL_LIBRARY/searches/` and every episode to `$BNL_LIBRARY/episodes.json`, so `transcript <id>` can find it later.

**Trap: popularity is not relevance.** Court news and exam coaching get far more views than legal AI talks. Views count for little in the score, and the noise filter removes most of it. Review the list before briefing.

## 3. Transcripts (`transcript <id>`)

Chain, best first. The first that works wins.

| Method | How | Cost |
|---|---|---|
| `youtube-captions` | The episode is a YouTube video. Fetch its captions (English first, then Hindi). | Free |
| `youtube-match` | An audio episode. Search YouTube for the same title and show, check the length is within 25%, fetch captions, then check the opening matches the title. | Free |
| `whisper` | Download the audio and transcribe it through `reference-impl/get_transcript.py`. Needs `STT_OPENAI_BASE_URL` and `STT_OPENAI_KEY`. | About USD 0.006 a minute on OpenAI, so about INR 30 for a one-hour episode |
| `fallback` | Show notes only. The record must say SHALLOW and carry no quotes. | Free |

**Trap: the right show, the wrong episode.** A match can be the same show on the same subject from another year. The opening check rejects it.

**Trap: clips.** A 90-second clip often ranks first. The length check rejects it.

**Trap: mangled names.** Auto captions garble names ("CLOC" for Clio). Check every name against the show notes.

**Trap: caption timestamps.** Caption lines carry `[mm:ss]` marks mid-sentence. The validator strips them before matching quotes.

## 4. Breakdown and validation (`validate`)

The bot writes one JSON record per episode following `prompts/breakdown.md`. `validate` checks:

- every required key is present;
- word limits per section and 250 words in total;
- 3 to 4 guest points and 2 to 3 practice points, unless SHALLOW;
- no em or en dashes, no "not X but Y";
- every quote appears word for word in the transcript;
- every number in a guest point appears in the transcript;
- a SHALLOW record carries no quotes.

Fix and re-run until it passes.

## 5. Roundup (`render`)

`render` builds phone-friendly markdown from records, an optional roundup JSON (headline, themes, pick) and an optional search file for the "Also new" list. See `prompts/roundup.md`.

## 6. Feeds (`feeds`)

Checks every curated feed and shows its latest episode date. Mark a feed `dormant` when it has been silent for a year. Dormant feeds stay listed and are skipped in search.

## 7. Failure modes

| Failure | Symptom | Fix |
|---|---|---|
| Transcript pasted into chat | Slow or failed run | R3. Read from disk once. |
| Wrong episode matched | Fluent brief on the wrong talk | Opening check; try `--no-match` and fall back |
| No captions | `fallback` method | Set STT keys for Whisper, or brief as SHALLOW |
| YouTube blocks requests | Empty YouTube results, warnings | Wait, update `yt-dlp`, rely on RSS and Apple |
| Quote fails validation | Captions differ from the speaker's words | Use the caption text as it stands, or drop the quote |
| A feed times out | Warning in stderr | Normal. The run continues. |

## 8. Extending

- Add a show: one entry in `sources/feeds.json`.
- Add a topic: one entry in `sources/topics.json`.
- Change the format: edit `prompts/` and the limits in `LIMITS` in `tools/bnl.py` together.
