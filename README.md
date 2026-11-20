# Brave New Law kit

A podcast researcher for lawyers, built to run inside a chat bot. Ask it what people in law said on podcasts in the last 24 hours. It searches live, reads the transcripts and sends you a short brief. There is no app or website.

It covers the future of law, the practice of law, legal services, legal AI, legal tech, access to justice, digital justice and Indian courts.

## What you can ask the bot

- "What's trending in legal podcasts today?"
- "Find podcasts on legal AI from the last 24 hours."
- "Give me the takeaways from the top three."
- "Send me today's roundup."
- "Add this show to the list: [name]."

## What you get

**A ranked list** of new episodes: title, show, length, why it fits.

**A breakdown per episode**, under 250 words: a verdict, what the guest argued, the host's view, points for your practice, an India angle where real, two checked quotes and the source.

**A daily roundup**, under 350 words: a headline, themes, one line per episode and the one worth a full listen.

## How it works

1. **Search.** It checks 21 curated legal podcasts, the Apple podcast directory and YouTube for the last 24 hours. It ranks by topic, freshness, views and source, and drops clips, live streams and exam coaching.
2. **Transcribe.** It pulls free YouTube captions, or finds the episode's YouTube upload. Paid transcription is optional.
3. **Brief.** The bot writes from the transcript only. A checker rejects quotes or numbers that are missing from the transcript.

Everything runs free. No API key is needed.

## Files

| Path | What it is |
|---|---|
| `SKILL.md` | The bot's operating manual |
| `tools/bnl.py` | The tool: `search`, `trending`, `transcript`, `validate`, `render`, `feeds` |
| `sources/feeds.json` | Curated legal podcasts (India and global) and a watch list of other sources |
| `sources/topics.json` | Topic sets and their search terms |
| `prompts/` | Breakdown and roundup formats |
| `SPEC.md` | How each step works, and the traps |
| `SETUP.md` | Install and bot setup |
| `reference-impl/get_transcript.py` | Optional paid Whisper transcription |

## Credits

Built on [podcast-digest-kit](https://github.com/RjMathur/podcast-digest-kit) by Rahul Mathur (MIT licence). Refocused on law for Emerging Cities Lawyers in November 2026. Source list seeded by the ECL Legal Research Counsel and Regulatory Watch bots. Brief format by the Writer Guy bot.
