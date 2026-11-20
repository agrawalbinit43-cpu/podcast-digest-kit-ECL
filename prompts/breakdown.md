# Episode breakdown

Format by Writer Guy, 21 Nov 2026. One breakdown runs to 250 words at most, about 75 seconds on a phone.

## Prompt

You brief a lawyer on one podcast episode. They will use it in meetings without listening.

Rules:
- Use only the transcript. Show notes help with names and nothing else.
- Quotes are word for word. If you cannot find the exact words, leave the quote out.
- Every number must appear in the transcript.
- Start with substance. Never write "In this episode".
- Leave out any section with nothing real in it.
- Sentences of 20 words or fewer, active voice, no dashes, no "not X but Y", at most one group of three.
- Do not use the same word twice in one section.

## Sections and limits

| Field | Section | Limit |
|---|---|---|
| `verdict` | Verdict | 1 sentence, 20 words. Why it matters to a lawyer. |
| `guest_points` | What the guest argued | 3 to 4 items. `claim` up to 8 words, a claim you could argue with. `support` up to 40 words, keep the numbers. |
| `host_point` | Host's point | Optional. 1 point, 30 words. The host's own view, never their questions. |
| `for_practice` | For your practice | 2 to 3 bullets, 20 words each, each starts with a verb. |
| `india_angle` | India angle | Optional, 40 words. Leave it out if thin. Never invent Indian law; flag anything unsure. |
| `quotes` | Quotes | 2 quotes, 30 words each, with speaker and mm:ss. |
| `method`, `shallow` | Source | Link, date, transcript method, SHALLOW if show notes only. |

## Record schema

```json
{
  "id": "from the search result",
  "title": "", "podcast": "", "date": "YYYY-MM-DD", "url": "",
  "method": "youtube-captions | youtube-match | whisper | fallback",
  "shallow": false,
  "transcript_file": "path printed by the transcript command",
  "host": {"name": ""},
  "guest": {"name": "", "role": ""},
  "summary": "one line for the roundup list",
  "verdict": "",
  "guest_points": [{"claim": "", "support": ""}],
  "host_point": "",
  "for_practice": [""],
  "india_angle": "",
  "quotes": [{"text": "", "speaker": "", "time": "mm:ss"}]
}
```

Solo show: put the host's arguments in `guest_points` and set `guest.name` to the host. Panel: name each speaker inside `support`.

## Example (placeholder content)

**Verdict:** [Guest] says [...] will reprice routine diligence within two years. Plan for that now.

**What the guest argued**
- **[Claim of up to 8 words].** [Firm] cut review time from [...] hours to [...]. Partners kept the final sign-off.
- **[Second claim].** [...]
- **[Third claim].** [...]

**Host's point:** [Host] doubts [...] because [...].

**For your practice**
- Price [...] work by output.
- Test [...] on one closed matter before a live one.

**India angle:** [...] runs into Bar Council of India rules on [...].

**Quotes**
- "[Exact words]." [Guest], [mm:ss]
- "[Exact words]." [Host], [mm:ss]

**Source:** [link] · [date] · [method] · [SHALLOW]
