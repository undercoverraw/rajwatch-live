import asyncio
import email.utils
import html
import json
import os
import re
import tempfile
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import feedparser
import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from twikit import Client

app = FastAPI(title="CIU Watch — Real-Time Media & X Intelligence")

IST = timezone(timedelta(hours=5, minutes=30))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "hi-IN,hi;q=0.9,en-IN;q=0.8,en;q=0.7",
}
YT_LIVE_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": "CONSENT=YES+cb; SOCS=CAI",
}

YOUTUBE_CHANNELS = [
    ("First India News", "UC_cZYFovNQTsKw1ZLmO_AMA"),
    ("Zee Rajasthan", "UCfnvGSUXKIuaCv-9E9a2UJg"),
    ("News18 Rajasthan", "UC7FlLbNo66YsCEAPuJITiNg"),
]

NEWSPAPER_FEEDS = [
    ("Dainik Bhaskar", "https://feeds.bhaskar.com/rajasthan/"),
    ("Rajasthan Patrika", "https://www.patrika.com/rss/rajasthan/"),
    ("Zee News Rajasthan", "https://zeenews.india.com/rss/india/rajasthan.xml"),
]

NEGATIVE_WORDS = [
    "leak", "scam", "protest", "dharna", "virodh", "ghotala", "bhrashtachar",
    "lathicharge", "hadsa", "accident", "fire", "arrest", "murder", "crime",
    "paper leak", "strike", "outrage", "corruption", "bribe", "suspended",
    "fail", "failure", "shortage", "crisis", "attack", "accused", "allegation",
    "fraud", "death", "killed", "clash", "violence", "water crisis", "power cut",
    "unemployment", "inflation", "negligence", "acb", "trap", "slams", "boycott",
    "shame", "resign", "worst", "jumla", "barbad",
    "पेपर लीक", "लीक", "धरना", "विरोध", "घोटाला", "भ्रष्टाचार", "हादसा",
    "आग", "लाठीचार्ज", "गिरफ्तार", "गिरफ्तारी", "हंगामा", "नाराज", "मुर्दाबाद",
    "बंद", "प्रदर्शन", "आरोप", "निंदा", "नाकाम", "विफल", "आक्रोश", "गुस्सा",
    "निलंबित", "सस्पेंड", "हड़ताल", "चक्का जाम", "किल्लत", "संकट", "कटौती",
    "लापरवाही", "धांधली", "फर्जीवाड़ा", "ठगी", "रिश्वत", "एसीबी", "हत्या",
    "अपराध", "बवाल", "झड़प", "हमला", "बेरोजगारी", "महंगाई", "इस्तीफा", "घेराव",
    "मौत", "शर्मनाक", "चोरी", "लूट", "फेल", "तंज", "निशाना", "बदहाल",
]

POSITIVE_WORDS = [
    "launch", "launched", "inaugurate", "inaugurated", "approved", "approval",
    "development", "growth", "scheme", "relief", "bonus", "award", "praise",
    "success", "successful", "record", "investment", "mou", "jobs", "recruitment",
    "vacancy", "budget", "boost", "benefit", "welfare", "free", "subsidy",
    "grant", "milestone", "historic", "gift", "initiative", "reforms", "support",
    "congratulations", "proud", "thank you", "kudos", "saugat", "tohfa", "vikas",
    "उद्घाटन", "शिलान्यास", "योजना", "तोहफा", "सौगात", "विकास", "बधाई", "सफल",
    "सफलता", "राहत", "मंजूरी", "बजट", "निवेश", "रोजगार", "भर्ती", "नौकरी",
    "पुरस्कार", "सम्मान", "तारीफ", "सराहना", "ऐतिहासिक", "रिकॉर्ड", "लाभ",
    "फायदा", "मुफ्त", "फ्री", "सब्सिडी", "मुआवजा", "खुशखबरी", "बेहतर", "प्रगति",
    "कल्याण", "समझौता", "एमओयू", "बढ़ोतरी", "उपलब्धि", "शुरुआत", "सहायता", "स्वागत",
    "धन्यवाद", "आभार", "जिंदाबाद", "शानदार",
]

DISTRICTS = {
    "Jaipur": ["jaipur", "जयपुर", "jda"],
    "Jodhpur": ["jodhpur", "जोधपुर"],
    "Udaipur": ["udaipur", "उदयपुर"],
    "Kota": ["kota", "कोटा"],
    "Ajmer": ["ajmer", "अजमेर", "rpsc"],
    "Bikaner": ["bikaner", "बीकानेर"],
    "Bharatpur": ["bharatpur", "भरतपुर"],
    "Sikar": ["sikar", "सीकर"],
    "Alwar": ["alwar", "अलवर"],
    "Barmer": ["barmer", "बाड़मेर"],
    "Nagaur": ["nagaur", "नागौर"],
    "Jhunjhunu": ["jhunjhunu", "झुंझुनू", "झुंझुनूं"],
    "Bhilwara": ["bhilwara", "भीलवाड़ा"],
    "Pali": ["pali", "पाली"],
    "Dausa": ["dausa", "दौसा"],
    "Churu": ["churu", "चूरू"],
    "Ganganagar": ["ganganagar", "गंगानगर"],
    "Hanumangarh": ["hanumangarh", "हनुमानगढ़"],
    "Chittorgarh": ["chittorgarh", "चित्तौड़गढ़"],
    "Banswara": ["banswara", "बांसवाड़ा"],
    "Jaisalmer": ["jaisalmer", "जैसलमेर"],
    "Tonk": ["tonk", "टोंक"],
    "Sawai Madhopur": ["sawai madhopur", "सवाई माधोपुर"],
    "Karauli": ["karauli", "करौली"],
    "Dholpur": ["dholpur", "धौलपुर"],
    "Jhalawar": ["jhalawar", "झालावाड़"],
}

DEPARTMENTS = {
    "Politics & CMO": [
        "cm", "bhajanlal", "cmo", "rajcmo", "congress", "bjp", "minister", "mla", "mp",
        "election", "zila pramukh", "jila pramukh", "panchayat", "vidhan sabha",
        "भजनलाल", "मुख्यमंत्री", "कांग्रेस", "भाजपा", "मंत्री", "विधायक", "चुनाव",
        "जिला प्रमुख", "विधानसभा", "सरकार", "मंत्रिमंडल",
    ],
    "Education & RPSC": [
        "rpsc", "reet", "exam", "paper", "school", "college", "university", "teacher",
        "student", "rssb", "परीक्षा", "पेपर", "स्कूल", "शिक्षक", "छात्र", "भर्ती",
    ],
    "Police & Crime": [
        "police", "acb", "crime", "arrest", "fir", "murder", "cyber", "gangster",
        "पुलिस", "एसीबी", "अपराध", "गिरफ्तार", "हत्या", "थाना", "गैंगस्टर",
    ],
    "Water & Energy": [
        "water", "phed", "electricity", "power", "discom", "solar", "ercp",
        "पानी", "पेयजल", "बिजली", "ऊर्जा", "सोलर", "नहर", "डिस्कॉम",
    ],
    "Health & Medical": [
        "hospital", "doctor", "health", "medical", "sms hospital", "disease",
        "अस्पताल", "डॉक्टर", "स्वास्थ्य", "चिकित्सा", "मरीज",
    ],
    "Roads & Urban (PWD/JDA)": [
        "road", "highway", "pwd", "jda", "metro", "traffic", "municipal", "nagar nigam",
        "सड़क", "हाईवे", "मेट्रो", "नगर निगम", "अतिक्रमण", "ट्रैफिक",
    ],
    "Farmers & Rural": [
        "farmer", "kisan", "crop", "mandi", "msp", "agriculture",
        "किसान", "फसल", "मंडी", "कृषि", "खाद", "यूरिया",
    ],
}

# Persistent rolling store so tweets NEVER disappear on refresh
STORE_FILE = Path(tempfile.gettempdir()) / "ciuwatch_live_store.json"
LIVE_TWEET_STORE: Dict[str, Dict[str, Any]] = {}
ARCHIVE_X_CACHE: Dict[str, Dict[str, Any]] = {}
LAST_X_POLL: Dict[str, Any] = {"query": "", "ts": 0.0, "status": "idle", "error": ""}
DAILY_TRACKER: Dict[str, Any] = {"date": "", "items": {}}
LIVE_TV_CACHE: Dict[str, Any] = {"ts": 0.0, "streams": []}
cookie_turn = 0


def load_persisted_store():
    try:
        if STORE_FILE.exists():
            data = json.loads(STORE_FILE.read_text(encoding="utf-8"))
            if isinstance(data.get("tweets"), dict):
                LIVE_TWEET_STORE.update(data["tweets"])
            if isinstance(data.get("daily"), dict):
                DAILY_TRACKER["date"] = data["daily"].get("date", "")
                DAILY_TRACKER["items"] = data["daily"].get("items", {})
    except Exception:
        pass


def save_persisted_store():
    try:
        payload = {
            "tweets": LIVE_TWEET_STORE,
            "daily": DAILY_TRACKER,
        }
        STORE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


load_persisted_store()


def get_cookie_pool():
    pool = []
    for s in ["", "_2", "_3"]:
        auth = os.getenv(f"X_AUTH_TOKEN{s}", "").strip()
        ct0 = os.getenv(f"X_CT0{s}", "").strip()
        if auth and ct0:
            pool.append({"auth_token": auth, "ct0": ct0})
    return pool


def clean_text(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def detect_language(text: str) -> str:
    return "Hindi" if re.search(r"[\u0900-\u097F]", text or "") else "English"


def analyze_sentiment(text: str) -> Tuple[str, str]:
    low = (text or "").lower()
    neg = sum(1 for w in NEGATIVE_WORDS if w in low)
    pos = sum(1 for w in POSITIVE_WORDS if w in low)
    if neg > pos or (neg > 0 and neg == pos):
        return "negative", "ALERT"
    if pos > neg:
        return "positive", "POSITIVE"
    return "neutral", "INFO"


def detect_district(text: str) -> str:
    low = (text or "").lower()
    for d, terms in DISTRICTS.items():
        if any(t in low for t in terms):
            return d
    return "Statewide"


def detect_department(text: str) -> str:
    low = (text or "").lower()
    for dp, terms in DEPARTMENTS.items():
        if any(t in low for t in terms):
            return dp
    return "General / Admin"


def parse_timestamp(date_val: Any, default_now: bool = False) -> float:
    now_ts = time.time()
    fallback = now_ts if default_now else (now_ts - 1800)
    if not date_val:
        return fallback
    if isinstance(date_val, datetime):
        dt = date_val if date_val.tzinfo else date_val.replace(tzinfo=timezone.utc)
        return min(dt.timestamp(), now_ts)

    s = str(date_val).strip()
    if not s:
        return fallback

    # Twitter exact format first: "Wed Nov 25 11:30:00 +0000 2026"
    for fmt in (
        "%a %b %d %H:%M:%S %z %Y",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return min(dt.timestamp(), now_ts)
        except Exception:
            continue

    try:
        dt = email.utils.parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return min(dt.timestamp(), now_ts)
    except Exception:
        pass

    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return min(dt.timestamp(), now_ts)
    except Exception:
        return fallback


def enrich_item(item: Dict[str, Any]) -> Dict[str, Any]:
    text = clean_text(item.get("text", ""))
    sentiment, risk = analyze_sentiment(text)
    ts = float(item.get("ts", time.time()))
    dt_ist = datetime.fromtimestamp(ts, tz=IST)
    item.update({
        "text": text,
        "sentiment": sentiment,
        "risk": risk,
        "language": detect_language(text),
        "district": detect_district(text),
        "department": detect_department(text),
        "hour_ist": dt_ist.hour,
        "date_ist": dt_ist.strftime("%Y-%m-%d"),
        "date_label": dt_ist.strftime("%d %b"),
        "time_str": dt_ist.strftime("%I:%M %p"),
    })
    return item


def validate_date(date_str: str) -> Optional[Tuple[str, str]]:
    s = (date_str or "").strip()
    if not s:
        return None
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
        next_dt = dt + timedelta(days=1)
        return (dt.strftime("%Y-%m-%d"), next_dt.strftime("%Y-%m-%d"))
    except ValueError:
        return None


def prune_live_tweet_store(now_ts: float):
    cutoff = now_ts - 86400
    old_ids = [tid for tid, tw in LIVE_TWEET_STORE.items() if tw.get("ts", 0) < cutoff]
    for tid in old_ids:
        LIVE_TWEET_STORE.pop(tid, None)
    if len(LIVE_TWEET_STORE) > 500:
        sorted_ids = sorted(LIVE_TWEET_STORE.keys(), key=lambda k: LIVE_TWEET_STORE[k].get("ts", 0))
        for tid in sorted_ids[: len(LIVE_TWEET_STORE) - 500]:
            LIVE_TWEET_STORE.pop(tid, None)


def get_stored_live_tweets(now_ts: float) -> List[Dict[str, Any]]:
    prune_live_tweet_store(now_ts)
    return sorted(LIVE_TWEET_STORE.values(), key=lambda x: x["ts"], reverse=True)


# EXACT WORKING X FETCHER (twifork) + MERGE STORE (Tweets NEVER vanish on refresh!)
async def fetch_x_tweets(
    keywords: list[str],
    force: bool = False,
    date_window: Optional[Tuple[str, str]] = None,
):
    global cookie_turn
    now = time.time()
    pool = get_cookie_pool()
    if not pool:
        existing = [] if date_window else get_stored_live_tweets(now)
        return existing, "no_cookies", "Render Environment mein X_AUTH_TOKEN aur X_CT0 add karein"

    clean_kw = [k.strip() for k in keywords if k.strip()][:7]
    if not clean_kw:
        clean_kw = ["Rajasthan govt", "Bhajanlal", "RajCMO", "Jaipur"]
    base_q = "(" + " OR ".join([f'"{k}"' if " " in k else k for k in clean_kw]) + ") -is:retweet"

    if date_window:
        start_d, end_d = date_window
        query_str = f"{base_q} since:{start_d} until:{end_d}"
        cached = ARCHIVE_X_CACHE.get(query_str)
        min_gap = 20 if force else 120
        if cached and (now - cached["ts"]) < min_gap and cached["items"]:
            return cached["items"], f"live ({len(pool)} acc)", ""
        fetch_count = 35
    else:
        query_str = base_q
        min_gap = 12 if force else 22
        if LAST_X_POLL["query"] == query_str and (now - LAST_X_POLL["ts"]) < min_gap and LIVE_TWEET_STORE:
            return get_stored_live_tweets(now), f"live ({len(pool)} acc)", ""
        fetch_count = 20

    creds = pool[cookie_turn % len(pool)]
    cookie_turn += 1

    try:
        client = Client("en-US")
        client.set_cookies(creds)
        tweets = await client.search_tweet(query_str, "Latest", count=fetch_count)
        fresh_results = []
        for t in tweets or []:
            text = getattr(t, "text", "") or ""
            if not text:
                continue
            user = getattr(t, "user", None)
            handle = getattr(user, "screen_name", "unknown") if user else "unknown"
            name = getattr(user, "name", handle) if user else handle
            tid = str(getattr(t, "id", ""))
            if not tid:
                continue
            created_dt = getattr(t, "created_at_datetime", None)
            created_raw = created_dt if created_dt is not None else getattr(t, "created_at", "")
            ts = parse_timestamp(created_raw, default_now=(date_window is None))
            fresh_results.append(enrich_item({
                "id": f"x_{tid}",
                "source": "X",
                "author": f"{name} (@{handle})",
                "handle": f"@{handle}",
                "text": text,
                "url": f"https://x.com/{handle}/status/{tid}",
                "ts": ts,
                "likes": getattr(t, "favorite_count", 0) or 0,
                "retweets": getattr(t, "retweet_count", 0) or 0,
            }))

        if date_window:
            # Never overwrite existing cached archive tweets with an empty list
            if fresh_results:
                if len(ARCHIVE_X_CACHE) > 25:
                    oldest_q = sorted(ARCHIVE_X_CACHE.keys(), key=lambda k: ARCHIVE_X_CACHE[k]["ts"])[0]
                    ARCHIVE_X_CACHE.pop(oldest_q, None)
                ARCHIVE_X_CACHE[query_str] = {"ts": now, "items": fresh_results}
                return fresh_results, f"live ({len(pool)} acc)", ""
            cached = ARCHIVE_X_CACHE.get(query_str)
            return (cached["items"] if cached else []), f"live ({len(pool)} acc)", ""
        else:
            # MERGE new tweets into rolling LIVE_TWEET_STORE (never erase old tweets!)
            for tw in fresh_results:
                LIVE_TWEET_STORE[tw["id"]] = tw
            LAST_X_POLL.update({"query": query_str, "ts": now, "status": f"live ({len(pool)} acc)", "error": ""})
            if fresh_results:
                save_persisted_store()
            return get_stored_live_tweets(now), f"live ({len(pool)} acc)", ""

    except Exception as e:
        err_msg = str(e)[:140]
        if date_window:
            cached = ARCHIVE_X_CACHE.get(query_str)
            return (cached["items"] if cached else []), "warning", f"X temporary error: {err_msg}"
        # Keep showing all previously fetched live tweets even during a temporary X hiccup!
        return get_stored_live_tweets(now), "warning", f"X temporary error (purane tweets safe hain): {err_msg}"


async def fetch_google_news(
    http: httpx.AsyncClient,
    keywords: list[str],
    lang: str = "en",
    date_window: Optional[Tuple[str, str]] = None,
):
    top_kw = [k.strip() for k in keywords if k.strip()][:5]
    if not top_kw:
        top_kw = ["Rajasthan government", "Bhajanlal Sharma", "Jaipur"]
    kw_part = "(" + " OR ".join(f'"{k}"' if " " in k else k for k in top_kw) + ") Rajasthan"
    if date_window:
        q = f"{kw_part} after:{date_window[0]} before:{date_window[1]}"
    else:
        q = f"{kw_part} when:1d"

    if lang == "hi":
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=hi&gl=IN&ceid=IN:hi"
    else:
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=en-IN&gl=IN&ceid=IN:en"

    items = []
    try:
        r = await http.get(url, headers=HEADERS)
        feed = feedparser.parse(r.text)
        for entry in feed.entries[:20]:
            raw_title = clean_text(entry.get("title", ""))
            link = entry.get("link", "")
            pub = entry.get("published", "")
            source_name = (
                entry.get("source", {}).get("title", "Rajasthan News")
                if isinstance(entry.get("source"), dict)
                else "Rajasthan News"
            )
            if " - " in raw_title and source_name == "Rajasthan News":
                parts = raw_title.rsplit(" - ", 1)
                raw_title, source_name = parts[0].strip(), parts[1].strip()
            items.append(enrich_item({
                "id": f"gn_{lang}_{abs(hash(link or raw_title))}",
                "source": "NEWS",
                "author": source_name,
                "handle": "",
                "text": raw_title,
                "url": link,
                "ts": parse_timestamp(pub),
                "likes": 0,
                "retweets": 0,
            }))
    except Exception:
        pass
    return items


async def fetch_newspaper_feeds(http: httpx.AsyncClient):
    async def get_paper(name: str, feed_url: str):
        out = []
        try:
            r = await http.get(feed_url, headers=HEADERS)
            feed = feedparser.parse(r.text)
            for entry in feed.entries[:12]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                pub = entry.get("published") or entry.get("updated") or ""
                if not title:
                    continue
                out.append(enrich_item({
                    "id": f"paper_{abs(hash(link or title))}",
                    "source": "NEWSPAPER",
                    "author": name,
                    "handle": "",
                    "text": title,
                    "url": link,
                    "ts": parse_timestamp(pub),
                    "likes": 0,
                    "retweets": 0,
                }))
        except Exception:
            pass
        return out

    batches = await asyncio.gather(*(get_paper(n, u) for n, u in NEWSPAPER_FEEDS))
    return [it for b in batches for it in b]


async def fetch_youtube_feeds(http: httpx.AsyncClient):
    async def get_ch(ch_name: str, ch_id: str):
        out = []
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}"
        try:
            r = await http.get(url, headers=HEADERS)
            feed = feedparser.parse(r.text)
            for entry in feed.entries[:8]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                pub = entry.get("published") or entry.get("updated") or ""
                vid = str(entry.get("yt_videoid", "") or "")
                out.append(enrich_item({
                    "id": f"yt_{vid or abs(hash(link))}",
                    "video_id": vid,
                    "source": "YOUTUBE",
                    "author": ch_name,
                    "handle": "",
                    "text": title,
                    "url": link,
                    "ts": parse_timestamp(pub),
                    "likes": 0,
                    "retweets": 0,
                }))
        except Exception:
            pass
        return out

    batches = await asyncio.gather(*(get_ch(n, cid) for n, cid in YOUTUBE_CHANNELS))
    return [it for b in batches for it in b]


async def resolve_live_tv(http: httpx.AsyncClient, yt_items: List[Dict[str, Any]], force: bool = False):
    now = time.time()
    ttl = 60 if force else 600
    if LIVE_TV_CACHE["streams"] and (now - LIVE_TV_CACHE["ts"]) < ttl:
        return LIVE_TV_CACHE["streams"]

    async def get_live_stream(ch_name: str, cid: str):
        vid = None
        try:
            r = await http.get(f"https://www.youtube.com/channel/{cid}/live", headers=YT_LIVE_HEADERS, timeout=7.0)
            m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"', r.text)
            if m:
                vid = m.group(1)
            else:
                m2 = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"[^}]{0,300}"isLive":true', r.text)
                if m2:
                    vid = m2.group(1)
        except Exception:
            pass

        if not vid:
            try:
                r2 = await http.get(f"https://www.youtube.com/channel/{cid}/streams", headers=YT_LIVE_HEADERS, timeout=7.0)
                m_live = re.search(r'"videoId":"([A-Za-z0-9_-]{11})".{0,1800}?"style":"LIVE"', r2.text)
                if m_live:
                    vid = m_live.group(1)
                else:
                    m_any = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', r2.text)
                    if m_any:
                        vid = m_any.group(1)
            except Exception:
                pass

        if not vid:
            for item in yt_items:
                if item.get("author") == ch_name and len(item.get("video_id", "")) == 11:
                    vid = item["video_id"]
                    break

        if vid:
            return {
                "source": ch_name,
                "title": f"{ch_name} — Live Rajasthan TV",
                "embed_url": f"https://www.youtube.com/embed/{vid}?mute=1&rel=0",
            }
        return {
            "source": ch_name,
            "title": f"{ch_name} — Live Rajasthan TV",
            "embed_url": f"https://www.youtube.com/embed/live_stream?channel={cid}&mute=1",
        }

    streams = await asyncio.gather(*(get_live_stream(n, cid) for n, cid in YOUTUBE_CHANNELS))
    LIVE_TV_CACHE["ts"] = now
    LIVE_TV_CACHE["streams"] = list(streams)
    return LIVE_TV_CACHE["streams"]


@app.get("/")
async def serve_home():
    return FileResponse(Path(__file__).parent / "index.html")


@app.get("/api/feed")
async def api_feed(
    keywords: str = Query(default="Bhajanlal,RajCMO,Rajasthan govt,Jaipur,paper leak,RPSC"),
    force: bool = Query(default=False),
    date: str = Query(default=""),
):
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    now_ist = datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")

    date_window = validate_date(date)
    is_archive = date_window is not None and date_window[0] != today_str

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as http:
        if is_archive:
            x_res, news_en, news_hi = await asyncio.gather(
                fetch_x_tweets(kw_list, force=force, date_window=date_window),
                fetch_google_news(http, kw_list, lang="en", date_window=date_window),
                fetch_google_news(http, kw_list, lang="hi", date_window=date_window),
            )
            papers, yt_items = [], []
            live_tv = LIVE_TV_CACHE["streams"] or await resolve_live_tv(http, [], force=False)
        else:
            x_res, news_en, news_hi, papers, yt_items = await asyncio.gather(
                fetch_x_tweets(kw_list, force=force, date_window=None),
                fetch_google_news(http, kw_list, lang="en", date_window=None),
                fetch_google_news(http, kw_list, lang="hi", date_window=None),
                fetch_newspaper_feeds(http),
                fetch_youtube_feeds(http),
            )
            live_tv = await resolve_live_tv(http, yt_items, force=force)

    x_items, x_status, x_error = x_res
    cutoff = now_ist.timestamp() - 86400

    # Deduplicate X tweets by unique tweet ID (so tweets are NEVER dropped by title similarity!)
    deduped_x = []
    seen_x_ids = set()
    for tw in sorted(x_items, key=lambda x: x["ts"], reverse=True):
        if not is_archive and tw["ts"] < cutoff:
            continue
        if tw["id"] in seen_x_ids:
            continue
        seen_x_ids.add(tw["id"])
        deduped_x.append(tw)

    # Deduplicate News / Papers / YouTube separately
    deduped_media = []
    seen_media = set()
    for it in sorted(news_hi + news_en + papers + yt_items, key=lambda x: x["ts"], reverse=True):
        if not is_archive and it["ts"] < cutoff:
            continue
        k = f"{it['source']}_{re.sub(r'[^a-zA-Z0-9\u0900-\u097F]+', '', it['text'].lower())[:50]}"
        if not k or k in seen_media:
            continue
        seen_media.add(k)
        deduped_media.append(it)

    # Keep ALL stored X tweets (up to 150) + media (up to 120) so news never pushes tweets out!
    deduped = sorted(deduped_x[:150] + deduped_media[:120], key=lambda x: x["ts"], reverse=True)

    # Hourly Charts (Both ALL Media+X and ONLY X Tweets)
    if is_archive:
        hours_range = list(range(0, 24))
        h_pos = {h: 0 for h in hours_range}
        h_neg = {h: 0 for h in hours_range}
        h_neu = {h: 0 for h in hours_range}
        hx_pos = {h: 0 for h in hours_range}
        hx_neg = {h: 0 for h in hours_range}
        hx_neu = {h: 0 for h in hours_range}
        x_today_count = 0

        for it in deduped:
            h = it["hour_ist"]
            s = it["sentiment"]
            if s == "positive":
                h_pos[h] += 1
            elif s == "negative":
                h_neg[h] += 1
            else:
                h_neu[h] += 1

            if it["source"] == "X":
                x_today_count += 1
                if s == "positive":
                    hx_pos[h] += 1
                elif s == "negative":
                    hx_neg[h] += 1
                else:
                    hx_neu[h] += 1
    else:
        if DAILY_TRACKER["date"] != today_str:
            DAILY_TRACKER["date"] = today_str
            DAILY_TRACKER["items"] = {}

        for it in deduped:
            if it["date_ist"] == today_str:
                DAILY_TRACKER["items"][it["id"]] = {
                    "hour": it["hour_ist"],
                    "sentiment": it["sentiment"],
                    "source": it["source"],
                }

        if len(DAILY_TRACKER["items"]) > 1500:
            for old_k in list(DAILY_TRACKER["items"].keys())[:300]:
                DAILY_TRACKER["items"].pop(old_k, None)
        save_persisted_store()

        hours_range = list(range(0, now_ist.hour + 1))
        h_pos = {h: 0 for h in hours_range}
        h_neg = {h: 0 for h in hours_range}
        h_neu = {h: 0 for h in hours_range}
        hx_pos = {h: 0 for h in hours_range}
        hx_neg = {h: 0 for h in hours_range}
        hx_neu = {h: 0 for h in hours_range}
        x_today_count = 0

        for rec in DAILY_TRACKER["items"].values():
            h = rec.get("hour", 0)
            s = rec.get("sentiment", "neutral")
            src = rec.get("source", "")
            if h in h_pos:
                if s == "positive":
                    h_pos[h] += 1
                elif s == "negative":
                    h_neg[h] += 1
                else:
                    h_neu[h] += 1

                if src == "X":
                    x_today_count += 1
                    if s == "positive":
                        hx_pos[h] += 1
                    elif s == "negative":
                        hx_neg[h] += 1
                    else:
                        hx_neu[h] += 1

    # Stats, Districts, Departments & Account-Wise Bifurcation
    pos_total = neg_total = neu_total = 0
    x_pos = x_neg = x_neu = 0
    x_total = hi_total = en_total = yt_total = np_total = 0
    district_stats: Dict[str, Dict[str, int]] = {}
    dept_stats: Dict[str, Dict[str, int]] = {}
    account_stats: Dict[str, Dict[str, Any]] = {}

    for it in deduped:
        s = it["sentiment"]
        if s == "positive":
            pos_total += 1
        elif s == "negative":
            neg_total += 1
        else:
            neu_total += 1

        src = it["source"]
        if src == "X":
            x_total += 1
            if s == "positive":
                x_pos += 1
            elif s == "negative":
                x_neg += 1
            else:
                x_neu += 1
        elif src == "YOUTUBE":
            yt_total += 1
        elif src in ("NEWS", "NEWSPAPER"):
            np_total += 1

        if it["language"] == "Hindi":
            hi_total += 1
        else:
            en_total += 1

        d = it["district"]
        district_stats.setdefault(d, {"total": 0, "negative": 0, "positive": 0})
        district_stats[d]["total"] += 1
        if s == "negative":
            district_stats[d]["negative"] += 1
        elif s == "positive":
            district_stats[d]["positive"] += 1

        dp = it["department"]
        dept_stats.setdefault(dp, {"total": 0, "negative": 0, "positive": 0})
        dept_stats[dp]["total"] += 1
        if s == "negative":
            dept_stats[dp]["negative"] += 1
        elif s == "positive":
            dept_stats[dp]["positive"] += 1

        acc_key = f"{src}::{it['author']}"
        if acc_key not in account_stats:
            account_stats[acc_key] = {
                "author": it["author"],
                "handle": it.get("handle") or "",
                "source": src,
                "total": 0,
                "positive": 0,
                "negative": 0,
                "neutral": 0,
                "likes": 0,
                "retweets": 0,
                "last_ts": it["ts"],
            }
        acc = account_stats[acc_key]
        acc["total"] += 1
        acc[s] += 1
        acc["likes"] += int(it.get("likes") or 0)
        acc["retweets"] += int(it.get("retweets") or 0)
        if it["ts"] > acc["last_ts"]:
            acc["last_ts"] = it["ts"]

    accounts_list = []
    for acc in account_stats.values():
        t = max(acc["total"], 1)
        acc["pos_pct"] = round((acc["positive"] / t) * 100, 1)
        acc["neg_pct"] = round((acc["negative"] / t) * 100, 1)
        acc["neu_pct"] = round((acc["neutral"] / t) * 100, 1)
        if acc["negative"] > acc["positive"]:
            acc["stance"] = "Negative"
        elif acc["positive"] > acc["negative"]:
            acc["stance"] = "Positive"
        elif acc["negative"] > 0:
            acc["stance"] = "Mixed"
        else:
            acc["stance"] = "Neutral"
        accounts_list.append(acc)

    accounts_list.sort(key=lambda a: (a["total"], a["negative"]), reverse=True)

    total = max(len(deduped), 1)
    neg_pct = round((neg_total / total) * 100, 1)
    pos_pct = round((pos_total / total) * 100, 1)
    neu_pct = round((neu_total / total) * 100, 1)
    recent_neg = (
        neg_total
        if is_archive
        else sum(1 for it in deduped if it["sentiment"] == "negative" and (now_ist.timestamp() - it["ts"]) <= 7200)
    )
    crisis_score = min(100, int((neg_pct * 1.3) + (recent_neg * 4))) if deduped else 0

    return {
        "server_time": time.time(),
        "updated_at": now_ist.strftime("%d %b %Y, %I:%M:%S %p IST"),
        "mode": "archive" if is_archive else "live",
        "active_date": date_window[0] if is_archive else today_str,
        "today_date": today_str,
        "x_status": x_status,
        "x_error": x_error,
        "count": len(deduped),
        "stats": {
            "total": len(deduped),
            "x_count": x_total,
            "x_today_count": x_today_count,
            "news_count": np_total,
            "yt_count": yt_total,
            "positive": pos_total,
            "negative": neg_total,
            "neutral": neu_total,
            "x_positive": x_pos,
            "x_negative": x_neg,
            "x_neutral": x_neu,
            "positive_pct": pos_pct,
            "negative_pct": neg_pct,
            "neutral_pct": neu_pct,
            "hindi_count": hi_total,
            "english_count": en_total,
            "crisis_score": crisis_score,
        },
        "hourly_chart": {
            "labels": [f"{h:02d}:00" for h in hours_range],
            "positive": [h_pos[h] for h in hours_range],
            "negative": [h_neg[h] for h in hours_range],
            "neutral": [h_neu[h] for h in hours_range],
        },
        "hourly_chart_x": {
            "labels": [f"{h:02d}:00" for h in hours_range],
            "positive": [hx_pos[h] for h in hours_range],
            "negative": [hx_neg[h] for h in hours_range],
            "neutral": [hx_neu[h] for h in hours_range],
        },
        "districts": sorted(
            [{"name": k, **v} for k, v in district_stats.items()],
            key=lambda x: (x["negative"], x["total"]),
            reverse=True,
        )[:10],
        "departments": sorted(
            [{"name": k, **v} for k, v in dept_stats.items()],
            key=lambda x: (x["negative"], x["total"]),
            reverse=True,
        ),
        "accounts": accounts_list,
        "live_tv": live_tv,
        "items": deduped,
    }
