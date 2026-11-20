# Daily roundup

Format by Writer Guy, 21 Nov 2026. The roundup runs to 350 words at most. With one full breakdown, it reads in under 3 minutes.

Write a JSON file and pass it to `render --roundup`:

```json
{
  "headline": "up to 12 words",
  "themes": [{"title": "bold title", "body": "up to 35 words"}],
  "pick": "one episode and up to 40 words on why it deserves a full listen"
}
```

| Section | Limit |
|---|---|
| Headline | 12 words |
| Themes | 3 themes, each a bold title and up to 35 words. A theme must span at least two episodes. With one episode, write one theme. |
| Episodes | Rendered from each record's `summary`, 30 words each |
| Worth a full listen | 1 pick, up to 40 words |
| Also new | Rendered from the search file: episodes found but not yet briefed |

Same writing rules as the breakdown. Themes come from the briefed episodes only.
