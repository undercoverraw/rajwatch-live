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
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from twikit import Client

app = FastAPI(title="RajWatch Live Pro - Open Source")

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

# Verified Rajasthan News YouTube Channel RSS Feeds
YOUTUBE_CHANNELS = [
    ("First India News", "UC_cZYFovNQTsKw1ZLmO_AMA"),
    ("Zee Rajasthan", "UCfnvGSUXKIuaCv-9E9a2UJg"),
    ("News18 Rajasthan", "UC7FlLbNo66YsCEAPuJITiNg"),
]

# Direct Rajasthan Newspaper Websites RSS
NEWSPAPER_FEEDS = [
    ("Dainik Bhaskar", "https://feeds.bhaskar.com/rajasthan/"),
    ("Rajasthan Patrika", "https://www.patrika.com/rss/rajasthan/"),
    ("Zee News Rajasthan", "https://zeenews.india.com/rss/india/rajasthan.xml"),
]

# Negative / Against-Govt Keywords (Hindi + English + Hinglish)
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

# Positive / Pro-Govt Keywords (Hindi + English + Hinglish)
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


def get_cookie_pool():
    pool = []
    suffixes = ["", "_2", "_3"]
    for s in suffixes:
        auth = os.getenv(f"X_AUTH_TOKEN{s}", "").strip()
        ct0 = os.getenv(f"X_CT0{s}", "").strip()
        if auth and ct0:
            pool.append({"auth_token": auth, "ct0": ct0})
    return pool


x_cache = {"query": "", "ts": 0.0, "items": [], "status": "idle", "error": ""}
cookie_turn = 0
DAILY_TRACKER: Dict[str, Any] = {"date": "", "items": {}}
LIVE_TV_CACHE: Dict[str, Any] = {"ts": 0.0, "streams": []}


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


def parse_timestamp(date_str: str) -> float:
    now_ts = time.time()
    if not date_str:
        return now_ts - 1800
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
    try:
        dt = datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
        return min(dt.timestamp(), now_ts)
    except Exception:
        return now_ts - 1800


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
        "time_str": dt_ist.strftime("%I:%M %p"),
    })
    return item


# EXACT WORKING X FETCHER (using twifork) — Untouched
async def fetch_x_tweets(keywords: list[str], force: bool = False):
    global cookie_turn
    pool = get_cookie_pool()
    if not pool:
        return [], "no_cookies", "Render Environment mein X_AUTH_TOKEN aur X_CT0 add karein"

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
            results.append(enrich_item({
                "id": f"x_{tid}",
                "source": "X",
                "author": f"{name} (@{handle})",
                "handle": f"@{handle}",
                "text": text,
                "url": f"https://x.com/{handle}/status/{tid}" if tid else "https://x.com",
                "ts": ts,
                "likes": getattr(t, "favorite_count", 0) or 0,
                "retweets": getattr(t, "retweet_count", 0) or 0,
            }))
        x_cache.update({"query": query_str, "ts": now, "items": results, "status": "live", "error": ""})
        return results, f"live ({len(pool)} acc)", ""
    except Exception as e:
        err_msg = str(e)[:140]
        return x_cache["items"], "warning", f"X temporary error: {err_msg}"


async def fetch_google_news(http: httpx.AsyncClient, keywords: list[str], lang: str = "en"):
    top_kw = [k.strip() for k in keywords if k.strip()][:5]
    if not top_kw:
        top_kw = ["Rajasthan government", "Bhajanlal Sharma", "Jaipur"]
    q = "(" + " OR ".join(f'"{k}"' if " " in k else k for k in top_kw) + ") Rajasthan when:1d"
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
            source_name = entry.get("source", {}).get("title", "Rajasthan News") if isinstance(entry.get("source"), dict) else "Rajasthan News"
            if " - " in raw_title and source_name == "Rajasthan News":
                parts = raw_title.rsplit(" - ", 1)
                raw_title, source_name = parts[0].strip(), parts[1].strip()
            items.append(enrich_item({
                "id": f"gn_{lang}_{abs(hash(link or raw_title))}",
                "source": "NEWS",
                "author": source_name,
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


# Resolves exact active Live Video ID per channel (fixes "This video is unavailable")
async def resolve_live_tv(http: httpx.AsyncClient, yt_items: List[Dict[str, Any]], force: bool = False):
    now = time.time()
    ttl = 60 if force else 600
    if LIVE_TV_CACHE["streams"] and (now - LIVE_TV_CACHE["ts"]) < ttl:
        return LIVE_TV_CACHE["streams"]

    async def get_live_stream(ch_name: str, cid: str):
        vid = None
        # Method 1: Check channel's /live page for exact canonical watch?v= video ID
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

        # Method 2: Check channel's /streams tab for active live stream ID
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

        # Method 3: Guaranteed fallback to latest video from this channel's XML feed
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
):
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as http:
        x_res, news_en, news_hi, papers, yt_items = await asyncio.gather(
            fetch_x_tweets(kw_list, force=force),
            fetch_google_news(http, kw_list, lang="en"),
            fetch_google_news(http, kw_list, lang="hi"),
            fetch_newspaper_feeds(http),
            fetch_youtube_feeds(http),
        )
        live_tv = await resolve_live_tv(http, yt_items, force=force)

    x_items, x_status, x_error = x_res
    raw_items = x_items + news_hi + news_en + papers + yt_items

    now_ist = datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")
    cutoff = now_ist.timestamp() - 86400

    deduped = []
    seen_keys = set()
    for it in sorted(raw_items, key=lambda x: x["ts"], reverse=True):
        if it["ts"] < cutoff:
            continue
        k = f"{it['source']}_{re.sub(r'[^a-zA-Z0-9\u0900-\u097F]+', '', it['text'].lower())[:50]}"
        if not k or k in seen_keys:
            continue
        seen_keys.add(k)
        deduped.append(it)

    if DAILY_TRACKER["date"] != today_str:
        DAILY_TRACKER["date"] = today_str
        DAILY_TRACKER["items"] = {}
    for it in deduped:
        if it["date_ist"] == today_str:
            DAILY_TRACKER["items"][it["id"]] = {
                "hour": it["hour_ist"],
                "sentiment": it["sentiment"],
            }
    if len(DAILY_TRACKER["items"]) > 1000:
        for old_k in list(DAILY_TRACKER["items"].keys())[:200]:
            DAILY_TRACKER["items"].pop(old_k, None)

    hours_range = list(range(0, now_ist.hour + 1))
    h_pos = {h: 0 for h in hours_range}
    h_neg = {h: 0 for h in hours_range}
    h_neu = {h: 0 for h in hours_range}
    for rec in DAILY_TRACKER["items"].values():
        h = rec["hour"]
        if h in h_pos:
            if rec["sentiment"] == "positive":
                h_pos[h] += 1
            elif rec["sentiment"] == "negative":
                h_neg[h] += 1
            else:
                h_neu[h] += 1

    pos_total = neg_total = neu_total = 0
    x_total = hi_total = en_total = yt_total = np_total = 0
    district_stats: Dict[str, Dict[str, int]] = {}
    dept_stats: Dict[str, Dict[str, int]] = {}

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

    total = max(len(deduped), 1)
    neg_pct = round((neg_total / total) * 100, 1)
    pos_pct = round((pos_total / total) * 100, 1)
    neu_pct = round((neu_total / total) * 100, 1)
    recent_neg = sum(1 for it in deduped if it["sentiment"] == "negative" and (now_ist.timestamp() - it["ts"]) <= 7200)
    crisis_score = min(100, int((neg_pct * 1.3) + (recent_neg * 4)))

    return {
        "server_time": time.time(),
        "updated_at": now_ist.strftime("%d %b %Y, %I:%M:%S %p IST"),
        "x_status": x_status,
        "x_error": x_error,
        "count": len(deduped),
        "stats": {
            "total": len(deduped),
            "x_count": x_total,
            "news_count": np_total,
            "yt_count": yt_total,
            "positive": pos_total,
            "negative": neg_total,
            "neutral": neu_total,
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
        "live_tv": live_tv,
        "items": deduped[:140],
    }
