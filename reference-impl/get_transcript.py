#!/usr/bin/env python3
"""
get_transcript.py: acquire a transcript for one episode, with a fallback chain.

Reference implementation for SPEC.md §4-§5. Dependencies: requests (+ yt-dlp and ffmpeg on PATH
for the Whisper path).

Chain:  website transcript (if given) -> Supadata (YouTube) -> Whisper (RSS audio) -> none

Environment:
    SUPADATA_API_KEY        optional, https://supadata.ai
    STT_OPENAI_BASE_URL     optional, e.g. https://api.openai.com/v1 (or any compatible gateway)
    STT_OPENAI_KEY          optional
    YTDLP                   optional, path to yt-dlp (default: yt-dlp on PATH)

Usage:
    python3 get_transcript.py --title "Episode title" --audio-url "https://..." --out t.txt
    python3 get_transcript.py --title "..." --youtube-id abc123 --out t.txt
    python3 get_transcript.py --title "..." --website-url "https://show/ep/1" --out t.txt

Prints JSON on stdout: {"method": ..., "transcript": path|null, "chars": N, "notes": [...]}
Exit 0 if a transcript was obtained, 1 if it fell through to nothing.

IMPORTANT: this script writes the transcript to disk and does NOT print it. That is deliberate —
see SPEC.md invariant I3. The calling agent should read the file once, from disk, when writing
takeaways, and never paste it into a prompt again.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

UA = "podcast-digest/1.0"


def note(notes, msg):
    notes.append(msg)
    print(f"[info] {msg}", file=sys.stderr)


# ---------------------------------------------------------------- verification

def head_matches(transcript: str, title: str, guest: str = "") -> bool:
    """SPEC.md §4b — cheap sanity check that the transcript is the right episode."""
    import re

    head = transcript[:1200].lower()
    words = {
        w
        for w in re.findall(r"[a-z0-9]{4,}", (title + " " + guest).lower())
        if w not in {"with", "from", "that", "this", "about", "into", "their", "what", "when"}
    }
    if not words:
        return True
    hits = sum(1 for w in words if w in head)
    return hits >= max(1, len(words) // 4)


def looks_wrong_language(transcript: str) -> bool:
    """Crude non-Latin check — the classic 'Arabic transcript' failure."""
    sample = transcript[:2000]
    latin = sum(1 for c in sample if c.isascii() and c.isalpha())
    alpha = sum(1 for c in sample if c.isalpha())
    return alpha > 200 and (latin / alpha) < 0.5


# ---------------------------------------------------------------- sources

def from_website(url: str, notes) -> str | None:
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": UA})
        r.raise_for_status()
        text = r.text
    except Exception as exc:
        note(notes, f"website fetch failed: {exc}")
        return None
    # crude HTML strip; a real implementation should target the transcript container
    import re

    text = re.sub(r"(?is)<(script|style).*?</\\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\\s+", " ", text).strip()
    return text if len(text) > 2000 else None


def find_youtube_id(title: str, guest: str, notes) -> str | None:
    ytdlp = os.environ.get("YTDLP") or shutil.which("yt-dlp")
    if not ytdlp:
        note(notes, "yt-dlp not found; skipping YouTube search")
        return None
    query = f"ytsearch6:{title} {guest}".strip()
    cmd = [
        ytdlp, "--flat-playlist",
        "--print", "%(id)s | %(duration)s | %(channel)s | %(title)s", query,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=180).stdout
    except Exception as exc:
        note(notes, f"yt-dlp failed: {exc}")
        return None

    best = None
    for line in out.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            continue
        vid, dur = parts[0], parts[1]
        try:
            secs = int(float(dur))
        except ValueError:
            secs = 0
        if secs and secs < 900:  # reject clips for interview shows
            note(notes, f"skip clip {vid} ({secs}s)")
            continue
        best = vid
        note(notes, f"candidate {vid} ({secs}s) {parts[2]}")
        break
    return best


def from_supadata(youtube_id: str, notes) -> str | None:
    key = os.environ.get("SUPADATA_API_KEY")
    if not key:
        note(notes, "SUPADATA_API_KEY unset")
        return None
    url = f"https://www.youtube.com/watch?v={youtube_id}"
    try:
        r = requests.get(
            "https://api.supadata.ai/v1/youtube/transcript",
            params={"url": url, "lang": "en"},
            headers={"x-api-key": key},
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        note(notes, f"supadata failed: {exc}")
        return None

    content = data.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(seg.get("text", "") for seg in content).strip()
    return None


def from_whisper(audio_url: str, notes) -> str | None:
    base = os.environ.get("STT_OPENAI_BASE_URL")
    key = os.environ.get("STT_OPENAI_KEY")
    if not (base and key and audio_url):
        note(notes, "STT endpoint or audio url missing")
        return None

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        note(notes, "ffmpeg not found")
        return None

    work = Path(tempfile.mkdtemp(prefix="pd_"))
    src = work / "src.audio"
    small = work / "small.mp3"

    try:
        with requests.get(audio_url, stream=True, timeout=300, headers={"User-Agent": UA}) as r:
            r.raise_for_status()
            with open(src, "wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    fh.write(chunk)
    except Exception as exc:
        note(notes, f"audio download failed: {exc}")
        return None

    # mono 16k — this is what prevents HTTP 413
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
         "-ac", "1", "-ar", "16000", "-b:a", "32k", str(small)],
        check=False,
    )
    if not small.exists():
        note(notes, "ffmpeg transcode failed")
        return None

    # ~20 minute segments, safely under the upload limit
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(small),
         "-f", "segment", "-segment_time", "1200", "-c", "copy",
         str(work / "chunk_%03d.mp3")],
        check=False,
    )
    chunks = sorted(work.glob("chunk_*.mp3"))
    if not chunks:
        note(notes, "no audio chunks produced")
        return None

    parts = []
    for i, chunk in enumerate(chunks, 1):
        try:
            with open(chunk, "rb") as fh:
                r = requests.post(
                    f"{base.rstrip('/')}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {key}"},
                    files={"file": (chunk.name, fh, "audio/mpeg")},
                    data={"model": "whisper-large-v3", "response_format": "json"},
                    timeout=600,
                )
            r.raise_for_status()
            parts.append(r.json().get("text", ""))
            print(f"[info] chunk {i}/{len(chunks)} ok", file=sys.stderr)
        except Exception as exc:
            note(notes, f"chunk {i} failed: {exc}")  # sequential on purpose — no parallel uploads
    text = " ".join(p.strip() for p in parts if p).strip()
    return text or None


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--guest", default="")
    ap.add_argument("--audio-url", default="")
    ap.add_argument("--youtube-id", default="")
    ap.add_argument("--website-url", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-chars", type=int, default=3000)
    args = ap.parse_args()

    notes: list[str] = []
    method, text = None, None

    if args.website_url:
        text = from_website(args.website_url, notes)
        if text:
            method = "website"

    if not text:
        yt = args.youtube_id or find_youtube_id(args.title, args.guest, notes)
        if yt:
            text = from_supadata(yt, notes)
            if text:
                method = "supadata"

    if not text and args.audio_url:
        text = from_whisper(args.audio_url, notes)
        if text:
            method = "whisper"

    if text and len(text) < args.min_chars:
        note(notes, f"transcript suspiciously short ({len(text)} chars) — treating as incomplete")
        text = None

    if text and looks_wrong_language(text):
        note(notes, "transcript looks non-English — retry with lang=en on the correct video id")
        text = None

    if text and not head_matches(text, args.title, args.guest):
        note(notes, "HEAD CHECK FAILED — transcript may be a different episode; not trusting it")
        text = None

    if not text:
        print(json.dumps({"method": "fallback", "transcript": None, "chars": 0, "notes": notes}))
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {"method": method, "transcript": str(out), "chars": len(text), "notes": notes}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
