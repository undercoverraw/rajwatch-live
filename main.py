import asyncio
import email.utils
import os
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from twikit import Client

app = FastAPI(title="RajWatch Live - Open Source")

# Verified Rajasthan News YouTube Channel RSS Feeds
YOUTUBE_CHANNELS = [
    ("First India News", "UC_cZYFovNQTsKw1ZLmO_AMA"),
    ("Zee Rajasthan", "UCfnvGSUXKIuaCv-9E9a2UJg"),
    ("News18 Rajasthan", "UC7FlLbNo66YsCEAPuJITiNg"),
]

# Negative / Crisis keywords (Hindi + English) for automatic red highlight
RISK_WORDS = [
    "leak", "scam", "protest", "dharna", "virodh", "ghotala", "bhrashtachar",
    "lathicharge", "hadsa", "accident", "fire", "arrest", "murder", "crime",
    "paper leak", "पेपर लीक", "धरना", "विरोध", "घोटाला", "भ्रष्टाचार", "हादसा",
    "आग", "लाठीचार्ज", "गिरफ्तार", "हंगामा", "नाराज", "मुर्दाबाद", "बंद", "strike"
]

# Cookie pool loader (supports up to 3 backup X accounts for safe rotation)
def get_cookie_pool():
    pool = []
    suffixes = ["", "_2", "_3"]
    for s in suffixes:
        auth = os.getenv(f"X_AUTH_TOKEN{s}", "").strip()
        ct0 = os.getenv(f"X_CT0{s}", "").strip()
        if auth and ct0:
            pool.append({"auth_token": auth, "ct0": ct0})
    return pool

# Simple in-memory cache to protect X rate limits (50 calls / 15 min per account)
x_cache = {"query": "", "ts": 0.0, "items": [], "status": "idle", "error": ""}
cookie_turn = 0


def detect_risk(text: str) -> str:
    low = (text or "").lower()
    for w in RISK_WORDS:
        if w in low:
            return "ALERT"
    return "INFO"


def parse_timestamp(date_str: str) -> float:
    if not date_str:
        return time.time()
    try:
        dt = email.utils.parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return time.time()


async def fetch_x_tweets(keywords: list[str], force: bool = False):
    global cookie_turn
    pool = get_cookie_pool()
    if not pool:
        return [], "no_cookies", "Render Environment mein X_AUTH_TOKEN aur X_CT0 add karein"

    # Build one smart OR query so 1 X call covers multiple keywords
    clean_kw = [k.strip() for k in keywords if k.strip()][:7]
    if not clean_kw:
        clean_kw = ["Rajasthan govt", "Bhajanlal", "RajCMO", "Jaipur"]
    query_str = "(" + " OR ".join([f'"{k}"' if " " in k else k for k in clean_kw]) + ") -is:retweet"

    now = time.time()
    min_gap = 12 if force else 22
    if x_cache["query"] == query_str and (now - x_cache["ts"]) < min_gap and x_cache["items"]:
        return x_cache["items"], f"live ({len(pool)} acc)", ""

    creds = pool[cookie_turn % len(pool)]
    cookie_turn += 1

    try:
        client = Client("en-US")
        client.set_cookies(creds)
        tweets = await client.search_tweet(query_str, "Latest", count=20)
        results = []
        for t in tweets:
            text = getattr(t, "text", "") or ""
            user = getattr(t, "user", None)
            handle = getattr(user, "screen_name", "unknown") if user else "unknown"
            name = getattr(user, "name", handle) if user else handle
            tid = str(getattr(t, "id", ""))
            created = getattr(t, "created_at", "")
            ts = parse_timestamp(str(created))
            results.append({
                "id": f"x_{tid}",
                "source": "X",
                "author": f"{name} (@{handle})",
                "text": text,
                "url": f"https://x.com/{handle}/status/{tid}" if tid else "https://x.com",
                "ts": ts,
                "likes": getattr(t, "favorite_count", 0) or 0,
                "retweets": getattr(t, "retweet_count", 0) or 0,
                "risk": detect_risk(text),
            })
        x_cache.update({"query": query_str, "ts": now, "items": results, "status": "live", "error": ""})
        return results, f"live ({len(pool)} acc)", ""
    except Exception as e:
        err_msg = str(e)[:140]
        return x_cache["items"], "warning", f"X temporary error: {err_msg}"


async def fetch_google_news(keywords: list[str]):
    top_kw = [k.strip() for k in keywords if k.strip()][:5]
    if not top_kw:
        top_kw = ["Rajasthan government", "Bhajanlal Sharma", "Jaipur"]
    q = "(" + " OR ".join(top_kw) + ") Rajasthan when:1d"
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=en-IN&gl=IN&ceid=IN:en"
    items = []
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as http:
            r = await http.get(url)
            feed = feedparser.parse(r.text)
            for entry in feed.entries[:20]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                pub = entry.get("published", "")
                source_name = entry.get("source", {}).get("title", "Rajasthan News")
                items.append({
                    "id": f"news_{abs(hash(link or title))}",
                    "source": "NEWS",
                    "author": source_name,
                    "text": title,
                    "url": link,
                    "ts": parse_timestamp(pub),
                    "likes": 0,
                    "retweets": 0,
                    "risk": detect_risk(title),
                })
    except Exception:
        pass
    return items


async def fetch_youtube_feeds():
    items = []
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as http:
        async def get_ch(ch_name, ch_id):
            url = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}"
            try:
                r = await http.get(url)
                feed = feedparser.parse(r.text)
                for entry in feed.entries[:6]:
                    title = entry.get("title", "")
                    link = entry.get("link", "")
                    pub = entry.get("published", "")
                    items.append({
                        "id": f"yt_{entry.get('yt_videoid', abs(hash(link)))}",
                        "source": "YOUTUBE",
                        "author": ch_name,
                        "text": title,
                        "url": link,
                        "ts": parse_timestamp(pub),
                        "likes": 0,
                        "retweets": 0,
                        "risk": detect_risk(title),
                    })
            except Exception:
                pass

        await asyncio.gather(*(get_ch(n, cid) for n, cid in YOUTUBE_CHANNELS))
    return items


@app.get("/")
async def serve_home():
    return FileResponse(Path(__file__).parent / "index.html")


@app.get("/api/feed")
async def api_feed(
    keywords: str = Query(default="Bhajanlal,RajCMO,Rajasthan govt,Jaipur,paper leak"),
    force: bool = Query(default=False),
):
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    x_res, news_items, yt_items = await asyncio.gather(
        fetch_x_tweets(kw_list, force=force),
        fetch_google_news(kw_list),
        fetch_youtube_feeds(),
    )
    x_items, x_status, x_error = x_res
    all_items = x_items + news_items + yt_items
    all_items.sort(key=lambda x: x["ts"], reverse=True)
    return {
        "server_time": time.time(),
        "x_status": x_status,
        "x_error": x_error,
        "count": len(all_items),
        "items": all_items[:80],
    }
