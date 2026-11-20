#!/usr/bin/env python3
"""
bnl.py: the Brave New Law kit's command-line tool.

A chat bot calls this tool. It finds law podcasts from the last N hours, pulls
transcripts, checks records and renders roundups. Everything is free to run:
podcast RSS feeds, the Apple podcast search API and YouTube search. No API key
is needed. Podcast Index is used too when PODCASTINDEX_KEY and
PODCASTINDEX_SECRET are set (a free sign-up at podcastindex.org).

Python 3.9+. Standard library only, plus two optional helpers:
    pip install -r requirements.txt     # yt-dlp, youtube-transcript-api

Commands (run `python3 tools/bnl.py <command> -h` for options):
    search "<topic>"    episodes on a topic from the last 24 hours (default)
    trending            the last 24 hours across every topic in sources/topics.json
    transcript <id>     fetch a transcript for an episode from a search result
    validate <file>     check an episode record against the schema in SPEC.md
    render <files>      turn records into a phone-friendly markdown roundup
    feeds               check every curated feed and show its latest episode

Output is JSON on stdout. Warnings go to stderr.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
FEEDS_FILE = ROOT / "sources" / "feeds.json"
TOPICS_FILE = ROOT / "sources" / "topics.json"
LIB = Path(os.environ.get("BNL_LIBRARY", ROOT / "library"))
UA = "BraveNewLawKit/1.0 (podcast research tool)"
NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "podcast": "https://podcastindex.org/namespace/1.0",
}
MIN_SECONDS = 15 * 60  # shorter items are clips, not episodes


# ------------------------------------------------------------------ helpers

def warn(msg: str) -> None:
    print(f"[warn] {msg}", file=sys.stderr)


def http_get(url: str, timeout: int = 20, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(raw):
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def text_of(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def strip_html(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t]+", " ", s).strip()


def stable_id(*parts: str) -> str:
    key = "|".join(p for p in parts if p)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def parse_duration(raw) -> int:
    """itunes:duration may be seconds, MM:SS or HH:MM:SS."""
    if raw in (None, "", "NA"):
        return 0
    raw = str(raw).strip()
    try:
        if ":" in raw:
            secs = 0
            for part in raw.split(":"):
                secs = secs * 60 + int(float(part))
            return secs
        return int(float(raw))
    except ValueError:
        return 0


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_feeds() -> list:
    data = load_json(FEEDS_FILE, {}) or {}
    return [f for f in data.get("feeds", []) if f.get("rss") and f.get("status", "active") == "active"]


def load_topics() -> dict:
    return (load_json(TOPICS_FILE, {}) or {}).get("topics", {})


def find_ytdlp():
    if os.environ.get("YTDLP"):
        return os.environ["YTDLP"]
    found = shutil.which("yt-dlp")
    if found:
        return found
    for cand in sorted(Path.home().glob("Library/Python/*/bin/yt-dlp")):
        return str(cand)
    local = Path.home() / ".local" / "bin" / "yt-dlp"
    return str(local) if local.exists() else None


# ------------------------------------------------------------------ relevance

def topic_set(topic: str) -> dict:
    """Expand a topic into phrases and single keywords. Named sets come from topics.json."""
    key = topic.strip().lower()
    for name, t in load_topics().items():
        if key == name.lower() or key in [x.lower() for x in t.get("aliases", [])]:
            return {"terms": t["terms"], "keywords": t.get("keywords", [])}
    return {"terms": [topic.strip()], "keywords": []}


LAW_WORDS = [
    "law", "legal", "lawyer", "attorney", "advocate", "court", "judge", "judiciary",
    "justice", "litigation", "contract", "regulat", "compliance", "counsel", "barrister",
    "solicitor", "legaltech", "paralegal", "dispute", "arbitration", "rights",
]


# Not podcasts: exam coaching, live court news, reaction clips.
NOISE = re.compile(r"\b(upsc|aibe|clat|judiciary exam|pcs j|one shot|lecture|coaching|current affairs|"
                   r"live|breaking|shorts|reaction|caught|stupid|exposed|court no|cause list|testimony|llb)\b|\U0001F534", re.I)
PODCASTY = re.compile(r"\b(podcast|episode|ep\.?\s?\d+|interview|conversation|in conversation|fireside|webinar|panel)\b|#\d+", re.I)


def relevance(text: str, terms: list, keywords: list = ()):
    low = text.lower()
    hits = [t for t in terms if t.lower() in low]
    if not hits:  # fall back to the topic's single keywords, such as "ai"
        hits = [w for w in keywords if re.search(r"\b" + re.escape(w.lower()) + r"\b", low)][:2]
    law = sum(1 for w in LAW_WORDS if re.search(r"\b" + w, low))
    return 3.0 * len(hits) + min(law, 4) * 0.75, hits, law


# ------------------------------------------------------------------ sources

def ep(source, podcast, title, published, url, **kw) -> dict:
    d = {
        "id": stable_id(url or "", title, podcast),
        "source": source,
        "podcast": podcast,
        "title": strip_html(title),
        "published": published.isoformat() if published else None,
        "url": url,
        "audio_url": kw.get("audio_url"),
        "youtube_id": kw.get("youtube_id"),
        "duration_sec": kw.get("duration_sec", 0),
        "views": kw.get("views"),
        "channel": kw.get("channel"),
        "description": strip_html(kw.get("description", ""))[:1500],
        "curated": kw.get("curated", False),
        "region": kw.get("region"),
    }
    return d


def from_feed(feed: dict, since: datetime) -> list:
    out = []
    try:
        root = ET.fromstring(http_get(feed["rss"], timeout=20))
    except Exception as e:  # a dead feed must never stop the run
        warn(f"feed failed: {feed.get('name')}: {e}")
        return out
    for item in root.iter("item"):
        pub = parse_dt(text_of(item.find("pubDate")))
        if not pub or pub < since:
            continue
        enc = item.find("enclosure")
        link = text_of(item.find("link")) or (enc.get("url") if enc is not None else "")
        out.append(ep(
            "rss", feed["name"], text_of(item.find("title")), pub, link,
            audio_url=enc.get("url") if enc is not None else None,
            duration_sec=parse_duration(text_of(item.find("itunes:duration", NS))),
            description=text_of(item.find("description")) or text_of(item.find("itunes:summary", NS)),
            curated=True, region=feed.get("region"),
        ))
    return out


def from_apple(term: str, since: datetime) -> list:
    q = urllib.parse.urlencode({"media": "podcast", "entity": "podcastEpisode", "term": term, "limit": 50})
    try:
        data = json.loads(http_get("https://itunes.apple.com/search?" + q))
    except Exception as e:
        warn(f"apple search failed for {term!r}: {e}")
        return []
    out = []
    for r in data.get("results", []):
        pub = parse_dt(r.get("releaseDate"))
        if not pub or pub < since:
            continue
        out.append(ep(
            "apple", r.get("collectionName", ""), r.get("trackName", ""), pub,
            r.get("trackViewUrl") or r.get("episodeUrl"),
            audio_url=r.get("episodeUrl"),
            duration_sec=int((r.get("trackTimeMillis") or 0) / 1000),
            description=r.get("description") or r.get("shortDescription") or "",
        ))
    return out


# YouTube search filters: "uploaded today" + "videos only", and "this week".
YT_FILTER = {24: "EgQIAhAB", 168: "EgQIAxAB"}


def from_youtube(term: str, hours: int, limit: int = 20) -> list:
    ytdlp = find_ytdlp()
    if not ytdlp:
        warn("yt-dlp not installed; skipping YouTube (pip install yt-dlp)")
        return []
    sp = YT_FILTER[24] if hours <= 24 else YT_FILTER[168]
    url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": term, "sp": sp})
    try:
        proc = subprocess.run(
            [ytdlp, "--flat-playlist", "--dump-json", "--playlist-end", str(limit), "--no-warnings", url],
            capture_output=True, text=True, timeout=90,
        )
    except Exception as e:
        warn(f"youtube search failed for {term!r}: {e}")
        return []
    out = []
    for line in proc.stdout.splitlines():
        try:
            v = json.loads(line)
        except ValueError:
            continue
        dur = int(v.get("duration") or 0)
        if dur < MIN_SECONDS:  # no length means a short or a live stream
            continue
        ts = v.get("timestamp") or v.get("release_timestamp")
        pub = datetime.fromtimestamp(ts, timezone.utc) if ts else None
        out.append(ep(
            "youtube", v.get("channel") or v.get("uploader") or "", v.get("title", ""),
            pub, f"https://www.youtube.com/watch?v={v.get('id')}",
            youtube_id=v.get("id"), duration_sec=dur, views=v.get("view_count"),
            channel=v.get("channel"), description=v.get("description") or "",
        ))
    for e in out:  # flat results carry no exact date; YouTube's filter sets the window
        e["published"] = e["published"] or f"within {'24h' if hours <= 24 else '7d'} (YouTube filter)"
    return out


def from_podcastindex(term: str, since: datetime) -> list:
    key, secret = os.environ.get("PODCASTINDEX_KEY"), os.environ.get("PODCASTINDEX_SECRET")
    if not (key and secret):
        return []
    ts = str(int(time.time()))
    auth = hashlib.sha1((key + secret + ts).encode()).hexdigest()
    q = urllib.parse.urlencode({"q": term, "max": 40, "fulltext": ""})
    try:
        data = json.loads(http_get(
            "https://api.podcastindex.org/api/1.0/search/byterm?" + q,
            headers={"X-Auth-Key": key, "X-Auth-Date": ts, "Authorization": auth},
        ))
    except Exception as e:
        warn(f"podcast index failed: {e}")
        return []
    out = []
    for f in data.get("feeds", [])[:15]:
        newest = f.get("newestItemPublishTime") or f.get("lastUpdateTime") or 0
        if newest and datetime.fromtimestamp(newest, timezone.utc) >= since and f.get("url"):
            out += from_feed({"name": f.get("title", ""), "rss": f["url"]}, since)
    return out


# ------------------------------------------------------------------ ranking

def score(e: dict, ts: dict, hours: int) -> dict:
    text = f"{e['title']} {e['podcast']} {e['description']}"
    rel, hits, law = relevance(text, ts["terms"], ts["keywords"])
    views = e.get("views") or 0
    pop = 0.6 * min(math.log10(views + 1), 5.0) if views else 0.0
    rec = 0.0
    pub = parse_dt(e["published"]) if e.get("published") and "within" not in e["published"] else None
    if pub:
        age_h = max((now_utc() - pub).total_seconds() / 3600, 0)
        rec = max(0.0, 2.0 * (1 - age_h / max(hours, 1)))
    else:
        rec = 1.0
    bonus = 3.0 if e.get("curated") else 0.0
    if e["source"] != "youtube" or PODCASTY.search(e["title"] + " " + (e.get("channel") or "")):
        bonus += 1.5  # an actual podcast episode or long-form talk
    title = e["title"]
    latin = sum(c.isascii() for c in title if c.isalpha()) / max(1, sum(c.isalpha() for c in title))
    if latin < 0.6 and not e.get("curated"):
        bonus -= 3.0  # Binit reads in English; keep Hindi only from curated shows
    e["matched_terms"] = hits
    e["law_signal"] = law
    e["score"] = round(rel + pop + rec + bonus, 2)
    return e


def keep(e: dict) -> bool:
    """Drop items with no topic hit and weak law signal, and clips."""
    if e.get("duration_sec") and e["duration_sec"] < MIN_SECONDS:
        return False
    if e.get("curated"):
        return bool(e["matched_terms"])
    if NOISE.search(e["title"]):
        return False
    return bool(e["matched_terms"]) and e["law_signal"] >= 1


def dedupe(items: list) -> list:
    seen, out = set(), []
    for e in sorted(items, key=lambda x: -x["score"]):
        k = re.sub(r"\W+", "", e["title"].lower())[:60]
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def run_search(topic: str, hours: int, limit: int, sources: list) -> dict:
    since = now_utc() - timedelta(hours=hours)
    ts = topic_set(topic)
    terms = ts["terms"]
    jobs = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        if "rss" in sources:
            jobs += [pool.submit(from_feed, f, since) for f in load_feeds()]
        for t in terms[:6]:
            if "apple" in sources:
                jobs.append(pool.submit(from_apple, t, since))
            if "youtube" in sources:
                jobs.append(pool.submit(from_youtube, t, hours))
            if "podcastindex" in sources:
                jobs.append(pool.submit(from_podcastindex, t, since))
        raw = []
        for j in jobs:
            try:
                raw += j.result()
            except Exception as e:
                warn(f"source error: {e}")
    ranked = [score(e, ts, hours) for e in raw]
    ranked = dedupe([e for e in ranked if keep(e)])[:limit]
    remember(ranked)
    result = {
        "topic": topic,
        "terms": terms,
        "window_hours": hours,
        "searched_at": now_utc().isoformat(),
        "sources": sources,
        "found_raw": len(raw),
        "count": len(ranked),
        "episodes": ranked,
    }
    save_search(result)
    return result


# ------------------------------------------------------------------ library

def remember(items: list) -> None:
    LIB.mkdir(parents=True, exist_ok=True)
    idx_path = LIB / "episodes.json"
    idx = load_json(idx_path, {}) or {}
    for e in items:
        idx[e["id"]] = e
    idx_path.write_text(json.dumps(idx, indent=1, ensure_ascii=False), encoding="utf-8")


def lookup(ep_id: str) -> dict:
    idx = load_json(LIB / "episodes.json", {}) or {}
    if ep_id in idx:
        return idx[ep_id]
    for k, v in idx.items():
        if k.startswith(ep_id):
            return v
    sys.exit(f"unknown episode id {ep_id!r}: run a search first")


def save_search(result: dict) -> None:
    d = LIB / "searches"
    d.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"\W+", "-", result["topic"].lower()).strip("-")[:40] or "search"
    stamp = now_utc().strftime("%Y%m%d-%H%M")
    (d / f"{stamp}-{slug}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------ transcripts

CAPTION_LANGS = ["en", "en-IN", "en-US", "en-GB", "hi"]


def mmss(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def yt_captions(video_id: str, notes: list):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        notes.append("youtube-transcript-api not installed")
        return None
    try:
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=CAPTION_LANGS)
    except Exception as e:
        notes.append(f"no captions for {video_id}: {type(e).__name__}")
        return None
    lines = [f"[{mmss(s.start)}] {s.text.strip()}" for s in fetched if s.text.strip()]
    notes.append(f"captions: {getattr(fetched, 'language_code', '?')}, {'auto' if getattr(fetched, 'is_generated', True) else 'manual'}")
    return "\n".join(lines)


def head_matches(text: str, title: str) -> bool:
    """SPEC.md 4b: the opening of a transcript should share words with the title."""
    stop = {"with", "from", "that", "this", "about", "into", "their", "what", "when", "episode", "podcast", "part"}
    words = {w for w in re.findall(r"[a-z0-9]{4,}", title.lower()) if w not in stop}
    if not words:
        return True
    head = text[:4000].lower()
    return sum(1 for w in words if w in head) >= max(1, len(words) // 4)


def find_youtube_match(e: dict, notes: list):
    ytdlp = find_ytdlp()
    if not ytdlp:
        return None
    q = f"ytsearch5:{e['title']} {e['podcast']}"
    try:
        proc = subprocess.run([ytdlp, "--flat-playlist", "--dump-json", "--no-warnings", q],
                              capture_output=True, text=True, timeout=60)
    except Exception as ex:
        notes.append(f"youtube match search failed: {ex}")
        return None
    want = e.get("duration_sec") or 0
    for line in proc.stdout.splitlines():
        try:
            v = json.loads(line)
        except ValueError:
            continue
        dur = int(v.get("duration") or 0)
        if dur and dur < MIN_SECONDS:
            continue
        if want and dur and abs(dur - want) > 0.25 * want:
            continue
        notes.append(f"youtube candidate {v.get('id')}: {v.get('title')}")
        return v.get("id")
    return None


def whisper_via_reference(e: dict, out: Path, notes: list) -> bool:
    if not (os.environ.get("STT_OPENAI_BASE_URL") and os.environ.get("STT_OPENAI_KEY") and e.get("audio_url")):
        return False
    script = ROOT / "reference-impl" / "get_transcript.py"
    proc = subprocess.run([sys.executable, str(script), "--title", e["title"], "--audio-url", e["audio_url"],
                           "--out", str(out)], capture_output=True, text=True, timeout=3600)
    notes.append("whisper: " + (proc.stdout.strip()[-200:] or proc.stderr.strip()[-200:]))
    return proc.returncode == 0 and out.exists() and out.stat().st_size > 2000


def cmd_transcript(args) -> dict:
    e = lookup(args.id)
    tdir = LIB / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    out = tdir / f"{e['id']}.txt"
    notes, method, text = [], None, None

    vid = e.get("youtube_id")
    if vid:
        text = yt_captions(vid, notes)
        method = "youtube-captions" if text else None
    if not text and not args.no_match:
        vid = find_youtube_match(e, notes)
        if vid:
            text = yt_captions(vid, notes)
            if text and not head_matches(text, e["title"]):
                notes.append("rejected YouTube match: opening does not fit the title")
                text = None
            method = "youtube-match" if text else None
    if text:
        out.write_text(text, encoding="utf-8")
    elif whisper_via_reference(e, out, notes):
        method = "whisper"
    else:
        out.write_text(f"{e['title']}\n\n{e['description']}", encoding="utf-8")
        method = "fallback"
    shallow = method == "fallback"
    meta = {"id": e["id"], "title": e["title"], "podcast": e["podcast"], "method": method,
            "shallow": shallow, "transcript_file": str(out), "chars": out.stat().st_size, "notes": notes}
    e.update({"method": method, "shallow": shallow, "transcript_file": str(out)})
    remember([e])
    return meta


# ------------------------------------------------------------------ records

LIMITS = {"verdict": 20, "claim": 8, "support": 40, "host_point": 30, "practice": 20, "india_angle": 40, "quote": 30}
REQUIRED = ["id", "title", "podcast", "date", "url", "method", "shallow", "verdict",
            "guest", "host", "guest_points", "for_practice", "quotes"]


def words(s: str) -> int:
    return len(re.findall(r"\S+", s or ""))


def flat(s: str) -> str:
    s = re.sub(r"\[\d+:\d{2}(?::\d{2})?\]", " ", (s or "").lower())
    return " ".join(re.findall(r"[a-z0-9]+", s))


def style_problems(label: str, s: str) -> list:
    out = []
    if re.search("[—–]", s or ""):
        out.append(f"{label}: contains a dash")
    if re.search(r"\bnot\b[^.;]{1,40}\bbut\b", s or "", re.I):
        out.append(f"{label}: 'not X but Y' construction")
    return out


def validate_record(rec: dict) -> list:
    errs = [f"missing key: {k}" for k in REQUIRED if k not in rec]
    if errs:
        return errs
    def cap(label, s, n):
        if words(s) > n:
            errs.append(f"{label}: {words(s)} words, limit {n}")
        errs.extend(style_problems(label, s))
    cap("verdict", rec["verdict"], LIMITS["verdict"])
    gp = rec["guest_points"]
    if not rec["shallow"] and not 3 <= len(gp) <= 4:
        errs.append(f"guest_points: {len(gp)} items, want 3 to 4")
    for i, g in enumerate(gp, 1):
        if not isinstance(g, dict) or "claim" not in g or "support" not in g:
            errs.append(f"guest_points[{i}]: needs claim and support")
            continue
        cap(f"guest_points[{i}].claim", g["claim"], LIMITS["claim"])
        cap(f"guest_points[{i}].support", g["support"], LIMITS["support"])
    if rec.get("host_point"):
        cap("host_point", rec["host_point"], LIMITS["host_point"])
    if not rec["shallow"] and not 2 <= len(rec["for_practice"]) <= 3:
        errs.append(f"for_practice: {len(rec['for_practice'])} items, want 2 to 3")
    for i, b in enumerate(rec["for_practice"], 1):
        cap(f"for_practice[{i}]", b, LIMITS["practice"])
    if rec.get("india_angle"):
        cap("india_angle", rec["india_angle"], LIMITS["india_angle"])
    total = sum(words(x) for x in [rec["verdict"], rec.get("host_point", ""), rec.get("india_angle", "")])
    total += sum(words(g.get("claim", "")) + words(g.get("support", "")) for g in gp if isinstance(g, dict))
    total += sum(words(b) for b in rec["for_practice"])
    total += sum(words(q.get("text", "")) for q in rec["quotes"] if isinstance(q, dict))
    if total > 250:
        errs.append(f"breakdown runs {total} words, limit 250")

    tfile = rec.get("transcript_file")
    transcript = Path(tfile).read_text(encoding="utf-8") if tfile and Path(tfile).exists() else None
    if rec["shallow"] and rec["quotes"]:
        errs.append("shallow record cannot carry quotes")
    if transcript is None and rec["quotes"]:
        errs.append("transcript_file missing, so quotes cannot be checked")
    if transcript:
        tf = flat(transcript)
        for i, q in enumerate(rec["quotes"], 1):
            if not isinstance(q, dict) or not q.get("text"):
                errs.append(f"quotes[{i}]: needs text, speaker, time")
                continue
            cap(f"quotes[{i}]", q["text"], LIMITS["quote"])
            if flat(q["text"]) not in tf:
                errs.append(f"quotes[{i}]: not found word for word in the transcript")
        nums = set()
        for g in gp:
            if isinstance(g, dict):
                nums |= set(re.findall(r"\d[\d,.]*", g.get("support", "") + " " + g.get("claim", "")))
        for n in nums:
            if n.strip(".,") not in transcript:
                errs.append(f"number {n!r} not found in the transcript")
    return errs


def cmd_validate(args) -> dict:
    report = {}
    for f in args.files:
        rec = load_json(f)
        report[f] = ["not valid JSON"] if rec is None else validate_record(rec)
    return {"ok": all(not v for v in report.values()), "problems": report}


# ------------------------------------------------------------------ render

def render_episode(r: dict) -> str:
    L = [f"## {r['title']}", f"*{r['podcast']}* · {r['date']}", "", f"**Verdict:** {r['verdict']}", ""]
    if r.get("guest_points"):
        who = (r.get("guest") or {}).get("name") or "the guest"
        L += [f"**What {who} argued**"]
        L += [f"- **{g['claim']}** {g['support']}" for g in r["guest_points"]]
        L.append("")
    if r.get("host_point"):
        L += [f"**Host's point:** {r['host_point']}", ""]
    if r.get("for_practice"):
        L += ["**For your practice**"] + [f"- {b}" for b in r["for_practice"]] + [""]
    if r.get("india_angle"):
        L += [f"**India angle:** {r['india_angle']}", ""]
    if r.get("quotes"):
        L += ["**Quotes**"] + [f"- \"{q['text']}\" {q.get('speaker', '')}, {q.get('time', '')}".rstrip(", ") for q in r["quotes"]] + [""]
    src = f"**Source:** [{r['url']}]({r['url']}) · {r['date']} · {r['method']}"
    L += [src + (" · SHALLOW (show notes only)" if r.get("shallow") else ""), ""]
    return "\n".join(L)


def cmd_render(args) -> dict:
    recs = [load_json(f) for f in args.files]
    recs = [r for r in recs if r]
    rup = load_json(args.roundup, {}) if args.roundup else {}
    day = now_utc().astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%d %b %Y")
    L = [f"# Brave New Law · {day}", ""]
    if rup.get("headline"):
        L += [f"**{rup['headline']}**", ""]
    if rup.get("themes"):
        L += ["### Themes"] + [f"- **{t['title']}** {t['body']}" for t in rup["themes"]] + [""]
    L += ["### Episodes"]
    for r in recs:
        L.append(f"- [{r['title']}](#{re.sub(r'[^a-z0-9]+', '-', r['title'].lower()).strip('-')}) · {r['podcast']}. {r.get('summary') or r['verdict']}")
    L.append("")
    if rup.get("pick"):
        L += [f"### Worth a full listen", f"{rup['pick']}", ""]
    if args.search:
        done = {r["id"] for r in recs}
        res = load_json(args.search, {}) or {}
        rest = [e for e in res.get("episodes", []) if e["id"] not in done][: args.also]
        if rest:
            L += ["### Also new in this window"]
            L += [f"- [{e['title']}]({e['url']}) · {e['podcast']} · id {e['id']}" for e in rest] + [""]
    L += ["---", ""] + [render_episode(r) for r in recs]
    md = "\n".join(L)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md, encoding="utf-8")
        return {"written": args.out, "episodes": len(recs), "words": words(md)}
    print(md)
    return {}


# ------------------------------------------------------------------ feeds

def latest(feed: dict) -> dict:
    row = {"name": feed["name"], "rss": feed["rss"], "region": feed.get("region")}
    try:
        root = ET.fromstring(http_get(feed["rss"], timeout=20))
        dates = [parse_dt(text_of(i.find("pubDate"))) for i in root.iter("item")]
        dates = [d for d in dates if d]
        row["ok"] = True
        row["latest"] = max(dates).date().isoformat() if dates else None
        row["days_since"] = (now_utc() - max(dates)).days if dates else None
    except Exception as e:
        row["ok"] = False
        row["error"] = f"{type(e).__name__}: {str(e)[:80]}"
    return row


def cmd_feeds(args) -> dict:
    data = load_json(FEEDS_FILE, {}) or {}
    feeds = [f for f in data.get("feeds", []) if f.get("rss")]
    with ThreadPoolExecutor(max_workers=10) as pool:
        rows = list(pool.map(latest, feeds))
    return {"checked": len(rows), "ok": sum(1 for r in rows if r.get("ok")), "feeds": rows}


# ------------------------------------------------------------------ cli

def main() -> None:
    ap = argparse.ArgumentParser(prog="bnl", description="Brave New Law podcast kit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    allsrc = ["rss", "apple", "youtube", "podcastindex"]

    s = sub.add_parser("search", help="episodes on a topic from the last N hours")
    s.add_argument("topic")
    s.add_argument("--hours", type=int, default=24)
    s.add_argument("--limit", type=int, default=15)
    s.add_argument("--sources", default=",".join(allsrc))

    t = sub.add_parser("trending", help="every topic in sources/topics.json")
    t.add_argument("--hours", type=int, default=24)
    t.add_argument("--limit", type=int, default=20)
    t.add_argument("--sources", default=",".join(allsrc))

    tr = sub.add_parser("transcript", help="fetch a transcript to disk for an episode id")
    tr.add_argument("id")
    tr.add_argument("--no-match", action="store_true", help="skip the YouTube look-alike search")

    v = sub.add_parser("validate", help="check records against the schema and style rules")
    v.add_argument("files", nargs="+")

    r = sub.add_parser("render", help="render records into a markdown roundup")
    r.add_argument("files", nargs="+")
    r.add_argument("--roundup", help="JSON with headline, themes and pick")
    r.add_argument("--search", help="a saved search or trending JSON, to list episodes not yet broken down")
    r.add_argument("--also", type=int, default=8, help="how many extra episodes to list")
    r.add_argument("--out")

    sub.add_parser("feeds", help="check every curated feed")

    a = ap.parse_args()
    if a.cmd == "search":
        res = run_search(a.topic, a.hours, a.limit, a.sources.split(","))
    elif a.cmd == "trending":
        names = list(load_topics())
        merged = {}
        for n in names:
            for e in run_search(n, a.hours, a.limit, a.sources.split(","))["episodes"]:
                e.setdefault("topics", []).append(n)
                if e["id"] not in merged or merged[e["id"]]["score"] < e["score"]:
                    merged[e["id"]] = e
        eps = sorted(merged.values(), key=lambda x: -x["score"])[: a.limit]
        res = {"topics": names, "window_hours": a.hours, "count": len(eps), "episodes": eps}
    elif a.cmd == "transcript":
        res = cmd_transcript(a)
    elif a.cmd == "validate":
        res = cmd_validate(a)
        print(json.dumps(res, indent=1, ensure_ascii=False))
        sys.exit(0 if res["ok"] else 1)
    elif a.cmd == "render":
        res = cmd_render(a)
    else:
        res = cmd_feeds(a)
    if res:
        print(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

