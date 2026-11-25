import asyncio
import email.utils
import html
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import feedparser
import httpx
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI(title="RajWatch PRO | Live X Bridge + Newspapers + Live TV")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

IST = timezone(timedelta(hours=5, minutes=30))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "hi-IN,hi;q=0.9,en-IN;q=0.8,en;q=0.7",
}

# Verified Direct Rajasthan Newspapers RSS
NEWSPAPER_RSS_FEEDS = [
    ("Dainik Bhaskar", "https://feeds.bhaskar.com/rajasthan/"),
    ("Rajasthan Patrika", "https://www.patrika.com/rss/rajasthan/"),
    ("Zee News Rajasthan", "https://zeenews.india.com/rss/india/rajasthan.xml"),
]

# Verified Rajasthan News TV Channels (Exact XML timestamps + 24x7 Live TV Wall)
YOUTUBE_TV_CHANNELS = [
    ("First India News", "UC_cZYFovNQTsKw1ZLmO_AMA"),
    ("Zee Rajasthan", "UCfnvGSUXKIuaCv-9E9a2UJg"),
    ("News18 Rajasthan", "UC7FlLbNo66YsCEAPuJITiNg"),
]

# In-memory Live X Store (Instant updates from Chrome Extension)
LIVE_TWEETS: Dict[str, Dict[str, Any]] = {}
BRIDGE_STATE: Dict[str, Any] = {"last_ping": 0.0, "total_received": 0, "active_query": ""}
API_CACHE: Dict[str, Any] = {"query": "", "ts": 0.0}

# 45-second cache for Newspapers & YouTube RSS so 12s X polling stays super fast
MEDIA_CACHE: Dict[str, Dict[str, Any]] = {}
MEDIA_CACHE_TTL = 45

NEGATIVE_WORDS = [
    "leak", "scam", "paper leak", "protest", "dharna", "virodh", "ghotala",
    "bhrashtachar", "lathicharge", "hadsa", "accident", "fire", "arrest",
    "murder", "crime", "strike", "outrage", "corruption", "bribe", "suspended",
    "fail", "failure", "shortage", "crisis", "attack", "accused", "allegation",
    "fraud", "death", "killed", "clash", "violence", "water crisis", "power cut",
    "unemployment", "inflation", "negligence", "acb", "trap", "slams", "boycott",
    "shame", "resign", "worst", "fake", "jumla", "barbad", "nikamma",
    "पेपर लीक", "लीक", "धरना", "विरोध", "घोटाला", "भ्रष्टाचार", "हादसा",
    "आग", "लाठीचार्ज", "गिरफ्तार", "गिरफ्तारी", "हंगामा", "नाराज", "मुर्दाबाद",
    "बंद", "प्रदर्शन", "आरोप", "निंदा", "नाकाम", "विफल", "आक्रोश", "गुस्सा",
    "निलंबित", "सस्पेंड", "हड़ताल", "चक्का जाम", "किल्लत", "संकट", "कटौती",
    "लापरवाही", "धांधली", "फर्जीवाड़ा", "ठगी", "रिश्वत", "एसीबी", "हत्या",
    "अपराध", "बवाल", "झड़प", "हमला", "बेरोजगारी", "महंगाई", "इस्तीफा", "घेराव",
    "मौत", "शर्मनाक", "चोरी", "लूट", "फेल", "तंज", "निशाना", "बदहाल", "अन्याय",
]

POSITIVE_WORDS = [
    "launch", "launched", "inaugurate", "inaugurated", "approved", "approval",
    "development", "growth", "scheme", "relief", "bonus", "award", "praise",
    "success", "successful", "record", "investment", "mou", "jobs", "recruitment",
    "vacancy", "budget", "boost", "benefit", "welfare", "free", "subsidy",
    "grant", "milestone", "historic", "gift", "initiative", "reforms", "support",
    "congratulations", "proud", "thank you", "kudos", "great work", "amazing",
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


def clean_text(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def detect_language(text: str) -> str:
    return "Hindi" if re.search(r"[\u0900-\u097F]", text) else "English"


def analyze_sentiment(text: str) -> Tuple[str, List[str]]:
    low = (text or "").lower()
    neg = [w for w in NEGATIVE_WORDS if w in low]
    pos = [w for w in POSITIVE_WORDS if w in low]
    if len(neg) > len(pos):
        return "negative", neg[:3]
    if len(pos) > len(neg):
        return "positive", pos[:3]
    if neg:
        return "negative", neg[:3]
    return "neutral", []


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
    return "General / Public"


def parse_ts(date_str: str, default_offset_sec: int = 0) -> float:
    now_ts = time.time()
    if not date_str:
        return now_ts - default_offset_sec
    s = str(date_str).strip()
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
        pass
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return min(dt.timestamp(), now_ts)
        except Exception:
            continue
    return now_ts - default_offset_sec


def format_time_ago(ts: float, now_ts: float) -> str:
    diff = max(0, int(now_ts - ts))
    if diff < 60:
        return "Just now"
    if diff < 3600:
        return f"{diff // 60}m ago"
    if diff < 86400:
        return f"{diff // 3600}h ago"
    return f"{diff // 86400}d ago"


def build_item(
    item_id: str,
    category: str,
    title: str,
    source: str,
    url: str,
    ts: float,
    handle: str = "",
    metrics: str = "",
) -> Dict[str, Any]:
    title_clean = clean_text(title)
    dt_ist = datetime.fromtimestamp(ts, tz=IST)
    sent, triggers = analyze_sentiment(title_clean)
    return {
        "id": item_id,
        "category": category,  # "x" | "newspaper" | "youtube"
        "title": title_clean,
        "source": clean_text(source),
        "handle": handle,
        "url": url or "#",
        "language": detect_language(title_clean),
        "sentiment": sent,
        "triggers": triggers,
        "district": detect_district(title_clean),
        "department": detect_department(title_clean),
        "published_ts": ts,
        "hour_ist": dt_ist.hour,
        "date_ist": dt_ist.strftime("%Y-%m-%d"),
        "time_str": dt_ist.strftime("%I:%M %p"),
        "metrics": metrics,
    }


def store_tweet(tw: Dict[str, Any]):
    tid = str(tw.get("id") or "").strip()
    text = clean_text(tw.get("text") or "")
    if not tid or not text:
        return

    ts = parse_ts(tw.get("created_at") or "")
    handle = clean_text(tw.get("handle") or "x_user").lstrip("@")
    name = clean_text(tw.get("name") or handle)
    url = tw.get("url") or f"https://x.com/{handle}/status/{tid}"
    likes = str(tw.get("likes") or "0")
    retweets = str(tw.get("retweets") or "0")
    replies = str(tw.get("replies") or "0")

    LIVE_TWEETS[tid] = build_item(
        item_id=f"x_{tid}",
        category="x",
        title=text,
        source=f"{name} (@{handle})",
        url=url,
        ts=ts,
        handle=f"@{handle}",
        metrics=f"💬 {replies}   ↻ {retweets}   ♥ {likes}",
    )

    if len(LIVE_TWEETS) > 600:
        oldest_keys = sorted(LIVE_TWEETS.keys(), key=lambda k: LIVE_TWEETS[k]["published_ts"])[:100]
        for k in oldest_keys:
            LIVE_TWEETS.pop(k, None)


# Optional 24x7 X API support if TWITTERAPI_IO_KEY is configured
async def fetch_from_twitterapi_io(keywords: List[str]) -> bool:
    api_key = os.getenv("TWITTERAPI_IO_KEY", "").strip()
    if not api_key:
        return False

    q = " OR ".join(f'"{k}"' if " " in k else k for k in keywords[:6])
    now = time.time()
    if API_CACHE["query"] == q and (now - API_CACHE["ts"]) < 25:
        return True

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.get(
                "https://api.twitterapi.io/twitter/tweet/advanced_search",
                headers={"X-API-Key": api_key},
                params={"query": f"({q}) -filter:retweets", "queryType": "Latest"},
            )
            if r.status_code == 200:
                for tw in r.json().get("tweets", [])[:30]:
                    author = tw.get("author") or {}
                    store_tweet(
                        {
                            "id": str(tw.get("id") or ""),
                            "text": tw.get("text") or "",
                            "name": author.get("name") or "X User",
                            "handle": author.get("userName") or "x",
                            "url": tw.get("url") or "",
                            "created_at": tw.get("createdAt") or "",
                            "likes": tw.get("likeCount") or 0,
                            "retweets": tw.get("retweetCount") or 0,
                            "replies": tw.get("replyCount") or 0,
                        }
                    )
                API_CACHE.update({"query": q, "ts": now})
                return True
    except Exception:
        pass
    return False


# Direct Rajasthan Newspapers RSS (Exact timestamps)
async def fetch_direct_newspapers(http: httpx.AsyncClient) -> List[Dict[str, Any]]:
    async def get_feed(name: str, url: str):
        out = []
        try:
            r = await http.get(url, headers=HEADERS, timeout=9.0)
            feed = feedparser.parse(r.text)
            for entry in feed.entries[:15]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                pub = entry.get("published") or entry.get("updated") or ""
                if not title:
                    continue
                out.append(
                    build_item(
                        item_id=f"np_{abs(hash(link or title))}",
                        category="newspaper",
                        title=title,
                        source=name,
                        url=link,
                        ts=parse_ts(pub, default_offset_sec=3600),
                    )
                )
        except Exception:
            pass
        return out

    batches = await asyncio.gather(*(get_feed(n, u) for n, u in NEWSPAPER_RSS_FEEDS))
    return [it for b in batches for it in b]


# Hindi + English Newspaper Portals via Google News RSS (Exact timestamps)
async def fetch_google_newspapers(
    http: httpx.AsyncClient, query: str, lang: str, limit: int = 25
) -> List[Dict[str, Any]]:
    encoded_q = urllib.parse.quote(f"{query} when:1d")
    if lang == "hi":
        url = f"https://news.google.com/rss/search?q={encoded_q}&hl=hi&gl=IN&ceid=IN:hi"
    else:
        url = f"https://news.google.com/rss/search?q={encoded_q}&hl=en-IN&gl=IN&ceid=IN:en"

    out = []
    try:
        r = await http.get(url, headers=HEADERS, timeout=10.0)
        feed = feedparser.parse(r.text)
        for entry in feed.entries[:limit]:
            raw_title = clean_text(entry.get("title", ""))
            source = "News Paper"
            if entry.get("source") and isinstance(entry.source, dict):
                source = entry.source.get("title", source)
            elif " - " in raw_title:
                parts = raw_title.rsplit(" - ", 1)
                raw_title, source = parts[0].strip(), parts[1].strip()
            pub = entry.get("published") or entry.get("updated") or ""
            link = entry.get("link", "#")
            if not raw_title:
                continue
            out.append(
                build_item(
                    item_id=f"gn_{lang}_{abs(hash(link or raw_title))}",
                    category="newspaper",
                    title=raw_title,
                    source=source,
                    url=link,
                    ts=parse_ts(pub, default_offset_sec=3600),
                )
            )
    except Exception:
        pass
    return out


# Verified Official YouTube News Channels XML Feeds (Exact timestamps)
async def fetch_youtube_tv_news(http: httpx.AsyncClient) -> List[Dict[str, Any]]:
    async def get_ch(ch_name: str, ch_id: str):
        out = []
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}"
        try:
            r = await http.get(url, headers=HEADERS, timeout=9.0)
            feed = feedparser.parse(r.text)
            for entry in feed.entries[:10]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                pub = entry.get("published") or entry.get("updated") or ""
                vid = entry.get("yt_videoid", abs(hash(link)))
                if not title:
                    continue
                out.append(
                    build_item(
                        item_id=f"yt_{vid}",
                        category="youtube",
                        title=title,
                        source=ch_name,
                        url=link,
                        ts=parse_ts(pub, default_offset_sec=3600),
                    )
                )
        except Exception:
            pass
        return out

    batches = await asyncio.gather(*(get_ch(n, cid) for n, cid in YOUTUBE_TV_CHANNELS))
    return [it for b in batches for it in b]


async def get_cached_media(kw_list: List[str], force: bool = False) -> List[Dict[str, Any]]:
    cache_key = ",".join(k.lower() for k in kw_list[:6])
    now = time.time()
    cached = MEDIA_CACHE.get(cache_key)
    if not force and cached and (now - cached["ts"] < MEDIA_CACHE_TTL):
        return cached["items"]

    or_q = " OR ".join(f'"{k}"' if " " in k else k for k in kw_list[:6])
    async with httpx.AsyncClient(timeout=11.0, follow_redirects=True) as http:
        direct_np, gn_hi, gn_en, yt_news = await asyncio.gather(
            fetch_direct_newspapers(http),
            fetch_google_newspapers(http, or_q, "hi", 28),
            fetch_google_newspapers(http, or_q, "en", 25),
            fetch_youtube_tv_news(http),
            return_exceptions=True,
        )

    combined: List[Dict[str, Any]] = []
    for batch in (direct_np, gn_hi, gn_en, yt_news):
        if isinstance(batch, list):
            combined.extend(batch)

    MEDIA_CACHE[cache_key] = {"ts": now, "items": combined}
    return combined


@app.post("/api/ingest")
async def ingest_tweets(request: Request):
    """Chrome Extension sends live X tweets here (no extension changes needed)."""
    data = await request.json()
    tweets = data.get("tweets") or []
    added = 0
    for tw in tweets:
        if isinstance(tw, dict) and tw.get("id") and tw.get("text"):
            store_tweet(tw)
            added += 1

    BRIDGE_STATE["last_ping"] = time.time()
    BRIDGE_STATE["total_received"] += added
    if data.get("query"):
        BRIDGE_STATE["active_query"] = str(data.get("query"))

    return JSONResponse({"ok": True, "received": added, "total_stored": len(LIVE_TWEETS)})


@app.get("/")
async def serve_home():
    return FileResponse(Path(__file__).parent / "index.html")


@app.get("/api/feed")
async def api_feed(
    keywords: str = Query(default="Bhajanlal,RajCMO,Rajasthan govt,Jaipur,RPSC,paper leak,Jila Pramukh"),
    force: bool = Query(default=False),
):
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    if not kw_list:
        kw_list = ["Rajasthan", "Bhajanlal", "Jaipur"]

    api_active, media_items = await asyncio.gather(
        fetch_from_twitterapi_io(kw_list),
        get_cached_media(kw_list, force=force),
    )

    now_ist = datetime.now(IST)
    now_ts = now_ist.timestamp()
    today_str = now_ist.strftime("%Y-%m-%d")
    cutoff = now_ts - 86400

    # Combine Live X Tweets + Newspapers + YouTube TV & sort strictly newest first
    all_raw = list(LIVE_TWEETS.values()) + media_items
    items: List[Dict[str, Any]] = []
    seen = set()

    for raw in sorted(all_raw, key=lambda x: x["published_ts"], reverse=True):
        if raw["published_ts"] < cutoff:
            continue
        dedup_key = f"{raw['category']}_{re.sub(r'[^a-zA-Z0-9\u0900-\u097F]+', '', raw['title'].lower())[:50]}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        item_copy = dict(raw)
        item_copy["time_ago"] = format_time_ago(raw["published_ts"], now_ts)
        items.append(item_copy)

    hours_range = list(range(0, now_ist.hour + 1))
    hourly_pos = {h: 0 for h in hours_range}
    hourly_neg = {h: 0 for h in hours_range}
    hourly_neu = {h: 0 for h in hours_range}

    district_stats: Dict[str, Dict[str, int]] = {}
    dept_stats: Dict[str, Dict[str, int]] = {}
    voices: Dict[str, Dict[str, Any]] = {}

    pos_total = neg_total = neu_total = 0
    x_total = np_total = yt_total = hi_total = en_total = 0

    for it in items:
        s = it["sentiment"]
        if s == "positive":
            pos_total += 1
        elif s == "negative":
            neg_total += 1
        else:
            neu_total += 1

        cat = it["category"]
        if cat == "x":
            x_total += 1
        elif cat == "newspaper":
            np_total += 1
        elif cat == "youtube":
            yt_total += 1

        if it["language"] == "Hindi":
            hi_total += 1
        else:
            en_total += 1

        if it["date_ist"] == today_str:
            h = it["hour_ist"]
            if h in hourly_pos:
                if s == "positive":
                    hourly_pos[h] += 1
                elif s == "negative":
                    hourly_neg[h] += 1
                else:
                    hourly_neu[h] += 1

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

        if cat == "x" and it.get("handle"):
            hnd = it["handle"]
            voices.setdefault(hnd, {"handle": hnd, "total": 0, "negative": 0})
            voices[hnd]["total"] += 1
            if s == "negative":
                voices[hnd]["negative"] += 1

    total = max(len(items), 1)
    neg_pct = round((neg_total / total) * 100, 1)
    pos_pct = round((pos_total / total) * 100, 1)
    neu_pct = round((neu_total / total) * 100, 1)

    recent_neg = sum(1 for it in items if it["sentiment"] == "negative" and (now_ts - it["published_ts"]) <= 3600)
    crisis_score = min(100, int((neg_pct * 1.3) + (recent_neg * 4))) if items else 0

    bridge_age = int(now_ts - BRIDGE_STATE["last_ping"]) if BRIDGE_STATE["last_ping"] else 99999
    if bridge_age <= 90:
        x_status = "LIVE_BRIDGE"
    elif api_active:
        x_status = "LIVE_API"
    else:
        x_status = "WAITING"

    live_tv = [
        {
            "source": name,
            "title": f"{name} — 24x7 Live TV Stream",
            "embed_url": f"https://www.youtube.com/embed/live_stream?channel={cid}&mute=1",
        }
        for name, cid in YOUTUBE_TV_CHANNELS
    ]

    return JSONResponse(
        {
            "updated_at": now_ist.strftime("%d %b %Y, %I:%M:%S %p IST"),
            "status": x_status,
            "stats": {
                "total": len(items),
                "x_count": x_total,
                "newspaper_count": np_total,
                "youtube_count": yt_total,
                "live_tv_count": len(live_tv),
                "positive": pos_total,
                "negative": neg_total,
                "neutral": neu_total,
                "positive_pct": pos_pct,
                "negative_pct": neg_pct,
                "neutral_pct": neu_pct,
                "crisis_score": crisis_score,
                "hindi_count": hi_total,
                "english_count": en_total,
            },
            "hourly_chart": {
                "labels": [f"{h:02d}:00" for h in hours_range],
                "positive": [hourly_pos[h] for h in hours_range],
                "negative": [hourly_neg[h] for h in hours_range],
                "neutral": [hourly_neu[h] for h in hours_range],
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
            "top_voices": sorted(voices.values(), key=lambda x: (x["negative"], x["total"]), reverse=True)[:8],
            "live_tv": live_tv,
            "items": items[:180],
        }
    )
