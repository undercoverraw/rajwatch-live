import html
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI(title="RajWatch X-LIVE | Real-Time X Radar & Sentiment War Room")

# Chrome Extension se direct tweets receive karne ke liye CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

IST = timezone(timedelta(hours=5, minutes=30))

# In-memory Live Tweet Store (Last 24h, max 600 latest tweets)
LIVE_TWEETS: Dict[str, Dict[str, Any]] = {}
BRIDGE_STATE: Dict[str, Any] = {"last_ping": 0.0, "total_received": 0, "active_query": ""}
API_CACHE: Dict[str, Any] = {"query": "", "ts": 0.0}

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
    return re.sub(r"\s+", " ", text).strip()


def detect_language(text: str) -> str:
    return "Hindi" if re.search(r"[\u0900-\u097F]", text) else "English"


def analyze_sentiment(text: str):
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


def parse_ts(date_str: str) -> float:
    now_ts = time.time()
    if not date_str:
        return now_ts
    s = str(date_str).strip()
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
    return now_ts


def format_time_ago(ts: float, now_ts: float) -> str:
    diff = max(0, int(now_ts - ts))
    if diff < 60:
        return "Just now"
    if diff < 3600:
        return f"{diff // 60}m ago"
    if diff < 86400:
        return f"{diff // 3600}h ago"
    return f"{diff // 86400}d ago"


def store_tweet(tw: Dict[str, Any]):
    tid = str(tw.get("id") or "").strip()
    text = clean_text(tw.get("text") or "")
    if not tid or not text:
        return

    ts = parse_ts(tw.get("created_at") or "")
    dt_ist = datetime.fromtimestamp(ts, tz=IST)
    sent, triggers = analyze_sentiment(text)

    handle = clean_text(tw.get("handle") or "x_user").lstrip("@")
    name = clean_text(tw.get("name") or handle)
    url = tw.get("url") or f"https://x.com/{handle}/status/{tid}"

    likes = str(tw.get("likes") or "0")
    retweets = str(tw.get("retweets") or "0")
    replies = str(tw.get("replies") or "0")

    LIVE_TWEETS[tid] = {
        "id": tid,
        "title": text,
        "name": name,
        "handle": f"@{handle}",
        "source": f"{name} (@{handle})",
        "url": url,
        "language": detect_language(text),
        "sentiment": sent,
        "triggers": triggers,
        "district": detect_district(text),
        "department": detect_department(text),
        "published_ts": ts,
        "hour_ist": dt_ist.hour,
        "date_ist": dt_ist.strftime("%Y-%m-%d"),
        "time_str": dt_ist.strftime("%I:%M %p"),
        "metrics": f"💬 {replies}   ↻ {retweets}   ♥ {likes}",
    }

    # Trim if > 600 tweets
    if len(LIVE_TWEETS) > 600:
        oldest_keys = sorted(LIVE_TWEETS.keys(), key=lambda k: LIVE_TWEETS[k]["published_ts"])[:100]
        for k in oldest_keys:
            LIVE_TWEETS.pop(k, None)


# Optional: Agar TWITTERAPI_IO_KEY Render Environment mein add ho toh 24x7 bina laptop ke bhi fetch karega
async def fetch_from_twitterapi_io(keywords: List[str]):
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


@app.post("/api/ingest")
async def ingest_tweets(request: Request):
    """Chrome Extension yahan har 10 second mein live X tweets bhejti hai."""
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


@app.post("/api/clear")
async def clear_tweets():
    LIVE_TWEETS.clear()
    return JSONResponse({"ok": True})


@app.get("/")
async def serve_home():
    return FileResponse(Path(__file__).parent / "index.html")


@app.get("/api/feed")
async def api_feed(
    keywords: str = Query(default="Bhajanlal,RajCMO,Rajasthan govt,Jaipur,RPSC,paper leak,Jila Pramukh")
):
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    api_active = await fetch_from_twitterapi_io(kw_list)

    now_ist = datetime.now(IST)
    now_ts = now_ist.timestamp()
    today_str = now_ist.strftime("%Y-%m-%d")
    cutoff = now_ts - 86400

    # Strictly sort newest tweets first (Just now / 1m ago on top)
    items: List[Dict[str, Any]] = []
    for tw in sorted(LIVE_TWEETS.values(), key=lambda x: x["published_ts"], reverse=True):
        if tw["published_ts"] < cutoff:
            continue
        item_copy = dict(tw)
        item_copy["time_ago"] = format_time_ago(tw["published_ts"], now_ts)
        items.append(item_copy)

    hours_range = list(range(0, now_ist.hour + 1))
    hourly_pos = {h: 0 for h in hours_range}
    hourly_neg = {h: 0 for h in hours_range}
    hourly_neu = {h: 0 for h in hours_range}

    district_stats: Dict[str, Dict[str, int]] = {}
    dept_stats: Dict[str, Dict[str, int]] = {}
    voices: Dict[str, Dict[str, Any]] = {}

    pos_total = neg_total = neu_total = hi_total = en_total = 0

    for it in items:
        s = it["sentiment"]
        if s == "positive":
            pos_total += 1
        elif s == "negative":
            neg_total += 1
        else:
            neu_total += 1

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
    crisis_score = min(100, int((neg_pct * 1.3) + (recent_neg * 5))) if items else 0

    bridge_age = int(now_ts - BRIDGE_STATE["last_ping"]) if BRIDGE_STATE["last_ping"] else 99999
    bridge_connected = bridge_age <= 90

    if bridge_connected:
        status = "LIVE_BRIDGE"
    elif api_active:
        status = "LIVE_API"
    else:
        status = "WAITING"

    return JSONResponse(
        {
            "updated_at": now_ist.strftime("%d %b %Y, %I:%M:%S %p IST"),
            "status": status,
            "bridge_age_sec": bridge_age if BRIDGE_STATE["last_ping"] else None,
            "stats": {
                "total": len(items),
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
            "items": items[:150],
        }
    )
