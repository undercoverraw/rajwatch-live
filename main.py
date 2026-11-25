import asyncio
import email.utils
import html
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import feedparser
import httpx
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="RajWatch Pro - 360 Media & Sentiment War Room")

IST = timezone(timedelta(hours=5, minutes=30))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "hi-IN,hi;q=0.9,en-IN;q=0.8,en;q=0.7",
}

# Simple 45-second cache so Render Free Tier stays fast
FEED_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_SECONDS = 45

# Bilingual (Hindi + English) Government Sentiment Lexicon
NEGATIVE_WORDS = [
    # English
    "scam", "leak", "paper leak", "protest", "strike", "outrage", "anger",
    "corruption", "bribe", "arrest", "suspended", "suspend", "fail", "failure",
    "shortage", "crisis", "attack", "accused", "allegation", "fraud", "cheat",
    "death", "killed", "murder", "rape", "crime", "clash", "violence", "lathicharge",
    "water crisis", "power cut", "blackout", "unemployment", "inflation", "delay",
    "negligence", "collapse", "fir", "ed raid", "acb", "trap", "opposition slams",
    "slams", "criticize", "condemn", "furious", "dharna", "agitation", "boycott",
    "irregularities", "mismanagement", "unsafe", "scandal", "resign", "worst",
    # Hindi (Devanagari)
    "घोटाला", "पेपर लीक", "लीक", "विरोध", "धरना", "प्रदर्शन", "हंगामा", "भ्रष्टाचार",
    "आरोप", "निंदा", "नाकाम", "विफल", "आक्रोश", "गुस्सा", "लाठीचार्ज", "गिरफ्तारी",
    "निलंबित", "सस्पेंड", "हड़ताल", "चक्का जाम", "किल्लत", "संकट", "कटौती", "बिजली कटौती",
    "पानी संकट", "लापरवाही", "धांधली", "फर्जीवाड़ा", "ठगी", "रिश्वत", "एसीबी", "ट्रैप",
    "हत्या", "अपराध", "बवाल", "झड़प", "हमला", "बेरोजगारी", "महंगाई", "नाराजगी",
    "इस्तीफा", "घेराव", "बहिष्कार", "सवाल", "तंज", "निशाना", "बदहाल", "खस्ताहाल",
    "मौत", "हादसा", "अन्याय", "शर्मनाक", "चोरी", "लूट", "फेल",
    # Romanized Hindi
    "ghotala", "virodh", "hungama", "bhrashtachar", "aarop", "aakrosh", "dhandhli",
    "laparwahi", "rishwat", "hadtal", "katal", "bawal", "sankat", "killat",
]

POSITIVE_WORDS = [
    # English
    "launch", "launched", "inaugurate", "inaugurated", "approved", "approval",
    "development", "growth", "scheme", "relief", "bonus", "award", "praise",
    "success", "successful", "record", "investment", "mou", "jobs", "recruitment",
    "vacancy", "budget", "boost", "modern", "upgrade", "benefit", "welfare",
    "free", "subsidy", "grant", "milestone", "top", "best", "historic", "gift",
    "celebrate", "congratulate", "initiative", "reforms", "digital", "clean",
    "green", "highway", "metro", "hospital", "school", "compensation", "support",
    # Hindi (Devanagari)
    "उद्घाटन", "शिलान्यास", "योजना", "तोहफा", "सौगात", "विकास", "बधाई", "सफल",
    "सफलता", "राहत", "मंजूरी", "बजट", "निवेश", "रोजगार", "भर्ती", "नौकरी",
    "पुरस्कार", "सम्मान", "तारीफ", "सराहना", "ऐतिहासिक", "रिकॉर्ड", "लाभ", "फायदा",
    "मुफ्त", "फ्री", "सब्सिडी", "मुआवजा", "खुशखबरी", "बेहतर", "प्रगति", "कल्याण",
    "समझौता", "एमओयू", "बढ़ोतरी", "मजबूत", "उपलब्धि", "शुरुआत", "सहायता", "स्वागत",
    # Romanized Hindi
    "saugat", "tohfa", "vikas", "yojana", "rahat", "manzoori", "safal", "khushkhabri",
    "udghatan", "bharti", "rozgar", "badhai",
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
        "cm", "bhajanlal", "cmo", "congress", "bjp", "minister", "mla", "mp",
        "election", "zila pramukh", "jila pramukh", "panchayat", "vidhan sabha",
        "भजनलाल", "मुख्यमंत्री", "कांग्रेस", "भाजपा", "मंत्री", "विधायक", "चुनाव",
        "जिला प्रमुख", "विधानसभा", "सरकार", "मंत्रिमंडल",
    ],
    "Education & RPSC": [
        "rpsc", "reet", "exam", "paper", "school", "college", "university", "teacher",
        "student", "rssb", "coaching", "परीक्षा", "पेपर", "स्कूल", "शिक्षक", "छात्र",
        "भर्ती", "विश्वविद्यालय",
    ],
    "Police & Crime": [
        "police", "acb", "crime", "arrest", "fir", "murder", "cyber", "gangster",
        "law and order", "पुलिस", "एसीबी", "अपराध", "गिरफ्तार", "हत्या", "थाना", "गैंगस्टर",
    ],
    "Water & Energy": [
        "water", "phed", "electricity", "power", "discom", "solar", "ercp", "canal",
        "पानी", "पेयजल", "बिजली", "ऊर्जा", "सोलर", "नहर", "डिस्कॉम",
    ],
    "Health & Medical": [
        "hospital", "doctor", "health", "medical", "sms hospital", "disease", "clinic",
        "अस्पताल", "डॉक्टर", "स्वास्थ्य", "चिकित्सा", "बीमारी", "मरीज",
    ],
    "Roads & Urban (PWD/JDA)": [
        "road", "highway", "pwd", "jda", "metro", "traffic", "municipal", "nagar nigam",
        "सड़क", "हाईवे", "मेट्रो", "नगर निगम", "अतिक्रमण", "ट्रैफिक",
    ],
    "Farmers & Rural": [
        "farmer", "kisan", "crop", "mandi", "msp", "agriculture", "किसान", "फसल",
        "मंडी", "कृषि", "खाद", "यूरिया",
    ],
}


def clean_text(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def detect_language(text: str) -> str:
    return "Hindi" if re.search(r"[\u0900-\u097F]", text) else "English"


def analyze_sentiment(text: str) -> Dict[str, Any]:
    lower = text.lower()
    neg_hits = [w for w in NEGATIVE_WORDS if w in lower]
    pos_hits = [w for w in POSITIVE_WORDS if w in lower]

    neg_score = len(neg_hits)
    pos_score = len(pos_hits)

    if neg_score > pos_score:
        label = "negative"
    elif pos_score > neg_score:
        label = "positive"
    elif neg_score > 0 and neg_score == pos_score:
        # In political monitoring, mixed alerts are treated cautiously as negative
        label = "negative"
    else:
        label = "neutral"

    return {
        "label": label,
        "neg_count": neg_score,
        "pos_count": pos_score,
        "triggers": (neg_hits if label == "negative" else pos_hits)[:3],
    }


def detect_district(text: str) -> str:
    lower = text.lower()
    for district, terms in DISTRICTS.items():
        if any(t in lower for t in terms):
            return district
    return "Statewide"


def detect_department(text: str) -> str:
    lower = text.lower()
    for dept, terms in DEPARTMENTS.items():
        if any(t in lower for t in terms):
            return dept
    return "General / Admin"


def format_time_ago(dt_ist: datetime, now_ist: datetime) -> str:
    diff = int((now_ist - dt_ist).total_seconds())
    if diff < 60:
        return "Just now"
    if diff < 3600:
        return f"{diff // 60}m ago"
    if diff < 86400:
        return f"{diff // 3600}h ago"
    return f"{diff // 86400}d ago"


def parse_rss_datetime(entry: Any, fallback: datetime) -> datetime:
    published_str = entry.get("published") or entry.get("updated")
    if published_str:
        try:
            dt = email.utils.parsedate_to_datetime(published_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(IST)
        except Exception:
            pass
    return fallback


async def fetch_google_news(
    client: httpx.AsyncClient,
    query: str,
    lang: str = "hi",
    category: str = "news",
    limit: int = 25,
) -> List[Dict[str, Any]]:
    encoded_q = urllib.parse.quote(f"{query} when:1d")
    if lang == "hi":
        url = f"https://news.google.com/rss/search?q={encoded_q}&hl=hi&gl=IN&ceid=IN:hi"
    else:
        url = f"https://news.google.com/rss/search?q={encoded_q}&hl=en-IN&gl=IN&ceid=IN:en"

    items: List[Dict[str, Any]] = []
    now_ist = datetime.now(IST)
    try:
        resp = await client.get(url, headers=HEADERS, timeout=12.0, follow_redirects=True)
        if resp.status_code != 200:
            return items
        feed = feedparser.parse(resp.text)
        for entry in feed.entries[:limit]:
            raw_title = clean_text(entry.get("title", ""))
            source = "News Desk"
            if entry.get("source") and isinstance(entry.source, dict):
                source = entry.source.get("title", "News Desk")
            elif " - " in raw_title:
                parts = raw_title.rsplit(" - ", 1)
                raw_title, source = parts[0].strip(), parts[1].strip()

            dt_ist = parse_rss_datetime(entry, now_ist)
            sentiment = analyze_sentiment(raw_title)
            items.append(
                {
                    "id": entry.get("id") or entry.get("link") or raw_title[:60],
                    "title": raw_title,
                    "source": source,
                    "url": entry.get("link", "#"),
                    "category": category,
                    "language": detect_language(raw_title),
                    "sentiment": sentiment["label"],
                    "triggers": sentiment["triggers"],
                    "district": detect_district(raw_title),
                    "department": detect_department(raw_title),
                    "published_ts": dt_ist.timestamp(),
                    "hour_ist": dt_ist.hour,
                    "time_str": dt_ist.strftime("%I:%M %p"),
                    "time_ago": format_time_ago(dt_ist, now_ist),
                    "video_id": None,
                }
            )
    except Exception:
        pass
    return items


def _find_video_renderers(obj: Any, results: List[Dict[str, Any]]) -> None:
    if isinstance(obj, dict):
        if "videoRenderer" in obj and isinstance(obj["videoRenderer"], dict):
            results.append(obj["videoRenderer"])
            return
        for val in obj.values():
            _find_video_renderers(val, results)
    elif isinstance(obj, list):
        for elem in obj:
            _find_video_renderers(elem, results)


def _extract_yt_text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return str(node["simpleText"])
    if "runs" in node and isinstance(node["runs"], list):
        return "".join(str(r.get("text", "")) for r in node["runs"])
    return ""


async def fetch_youtube_search(
    client: httpx.AsyncClient,
    query: str,
    live_only: bool = False,
    limit: int = 12,
) -> List[Dict[str, Any]]:
    encoded_q = urllib.parse.quote(query)
    # EgJAAQ%3D%3D = Live right now | EgIIAg%3D%3D = Uploaded Today
    sp = "EgJAAQ%3D%3D" if live_only else "EgIIAg%3D%3D"
    url = f"https://www.youtube.com/results?search_query={encoded_q}&sp={sp}"

    output: List[Dict[str, Any]] = []
    now_ist = datetime.now(IST)
    try:
        resp = await client.get(url, headers=HEADERS, timeout=12.0, follow_redirects=True)
        if resp.status_code != 200:
            return output

        match = re.search(r"var ytInitialData = (\{.*?\});</script>", resp.text)
        if not match:
            match = re.search(r"ytInitialData\"\s*\]\s*=\s*(\{.*?\});", resp.text)
        if not match:
            return output

        data = json.loads(match.group(1))
        renderers: List[Dict[str, Any]] = []
        _find_video_renderers(data, renderers)

        seen_ids = set()
        for r in renderers:
            vid = r.get("videoId")
            if not vid or vid in seen_ids:
                continue
            seen_ids.add(vid)

            title = clean_text(_extract_yt_text(r.get("title")))
            channel = clean_text(_extract_yt_text(r.get("ownerText"))) or "YouTube TV"
            pub_text = clean_text(_extract_yt_text(r.get("publishedTimeText")))
            view_text = clean_text(_extract_yt_text(r.get("shortViewCountText")))

            # Estimate approximate IST timestamp from YouTube relative time
            est_dt = now_ist
            m_min = re.search(r"(\d+)\s*minute", pub_text.lower())
            m_hr = re.search(r"(\d+)\s*hour", pub_text.lower())
            if m_min:
                est_dt = now_ist - timedelta(minutes=int(m_min.group(1)))
            elif m_hr:
                est_dt = now_ist - timedelta(hours=int(m_hr.group(1)))

            sentiment = analyze_sentiment(title)
            output.append(
                {
                    "id": f"yt_{vid}",
                    "title": title,
                    "source": channel,
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "category": "tv_live" if live_only else "youtube",
                    "language": detect_language(title),
                    "sentiment": sentiment["label"],
                    "triggers": sentiment["triggers"],
                    "district": detect_district(title),
                    "department": detect_department(title),
                    "published_ts": est_dt.timestamp(),
                    "hour_ist": est_dt.hour,
                    "time_str": "LIVE TV" if live_only else est_dt.strftime("%I:%M %p"),
                    "time_ago": "LIVE NOW" if live_only else (pub_text or "Today"),
                    "views": view_text,
                    "video_id": vid,
                    "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                }
            )
            if len(output) >= limit:
                break
    except Exception:
        pass
    return output


@app.get("/", response_class=HTMLResponse)
async def serve_home():
    index_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/api/feed")
async def get_feed(
    keywords: str = Query(
        default="Rajasthan,Bhajanlal,Jaipur,RPSC,paper leak,Jila Pramukh"
    )
):
    cache_key = keywords.strip().lower()
    now_epoch = time.time()
    cached = FEED_CACHE.get(cache_key)
    if cached and (now_epoch - cached["ts"] < CACHE_TTL_SECONDS):
        return JSONResponse(cached["data"])

    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    if not kw_list:
        kw_list = ["Rajasthan", "Jaipur", "Bhajanlal"]

    # Build concise OR query (top 6 keywords)
    or_query = " OR ".join(f'"{k}"' if " " in k else k for k in kw_list[:6])
    portal_query = (
        "(site:bhaskar.com OR site:patrika.com OR site:firstindianews.com "
        "OR site:ndtv.com OR site:timesofindia.indiatimes.com OR site:aajtak.in "
        f"OR site:news18.com OR site:zeenews.india.com) ({or_query})"
    )
    yt_fallback_query = f"site:youtube.com ({or_query})"

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            # 1. Hindi News (All India & Rajasthan)
            fetch_google_news(client, or_query, lang="hi", category="hindi_news", limit=35),
            # 2. English News
            fetch_google_news(client, or_query, lang="en", category="english_news", limit=30),
            # 3. Direct Major News Websites / Portals
            fetch_google_news(client, portal_query, lang="hi", category="website", limit=25),
            # 4. YouTube Today's Videos (Direct Search)
            fetch_youtube_search(client, f"Rajasthan news {kw_list[0]}", live_only=False, limit=18),
            # 5. Live TV Streams running RIGHT NOW on YouTube
            fetch_youtube_search(client, "Rajasthan news live tv News18 Zee First India", live_only=True, limit=6),
            # 6. YouTube RSS Backup (in case YT rate-limits Render IP)
            fetch_google_news(client, yt_fallback_query, lang="hi", category="youtube", limit=15),
            return_exceptions=True,
        )

    all_items: List[Dict[str, Any]] = []
    live_tv_streams: List[Dict[str, Any]] = []

    for idx, res in enumerate(results):
        if isinstance(res, Exception) or not isinstance(res, list):
            continue
        if idx == 4:
            live_tv_streams = res
        else:
            all_items.extend(res)

    # Deduplicate by normalized title prefix
    deduped: List[Dict[str, Any]] = []
    seen_titles = set()
    for item in sorted(all_items, key=lambda x: x["published_ts"], reverse=True):
        key = re.sub(r"\W+", "", item["title"].lower())[:55]
        if not key or key in seen_titles:
            continue
        seen_titles.add(key)
        deduped.append(item)

    # Build Full-Day Hourly Sentiment Graph (IST: 00:00 up to current hour)
    now_ist = datetime.now(IST)
    current_hour = now_ist.hour
    hours_range = list(range(0, current_hour + 1))
    hourly_pos = {h: 0 for h in hours_range}
    hourly_neg = {h: 0 for h in hours_range}
    hourly_neu = {h: 0 for h in hours_range}

    district_stats: Dict[str, Dict[str, int]] = {}
    dept_stats: Dict[str, Dict[str, int]] = {}

    pos_total = neg_total = neu_total = 0
    hindi_total = eng_total = yt_total = web_total = 0

    for item in deduped:
        s = item["sentiment"]
        if s == "positive":
            pos_total += 1
        elif s == "negative":
            neg_total += 1
        else:
            neu_total += 1

        if item["language"] == "Hindi":
            hindi_total += 1
        else:
            eng_total += 1

        if item["category"] == "youtube":
            yt_total += 1
        elif item["category"] == "website":
            web_total += 1

        # Bucket into today's hourly chart
        h = item["hour_ist"]
        if h in hourly_pos:
            if s == "positive":
                hourly_pos[h] += 1
            elif s == "negative":
                hourly_neg[h] += 1
            else:
                hourly_neu[h] += 1

        # District aggregation
        d = item["district"]
        if d not in district_stats:
            district_stats[d] = {"total": 0, "negative": 0, "positive": 0}
        district_stats[d]["total"] += 1
        if s == "negative":
            district_stats[d]["negative"] += 1
        elif s == "positive":
            district_stats[d]["positive"] += 1

        # Department aggregation
        dp = item["department"]
        if dp not in dept_stats:
            dept_stats[dp] = {"total": 0, "negative": 0, "positive": 0}
        dept_stats[dp]["total"] += 1
        if s == "negative":
            dept_stats[dp]["negative"] += 1
        elif s == "positive":
            dept_stats[dp]["positive"] += 1

    total = max(len(deduped), 1)
    neg_pct = round((neg_total / total) * 100, 1)
    pos_pct = round((pos_total / total) * 100, 1)
    neu_pct = round((neu_total / total) * 100, 1)

    # Crisis Score (0 - 100): weighted by % negative + recent 2-hour negative spike
    recent_neg = sum(
        1 for it in deduped
        if it["sentiment"] == "negative" and (now_ist.timestamp() - it["published_ts"]) <= 7200
    )
    crisis_score = min(100, int((neg_pct * 1.3) + (recent_neg * 4)))

    top_districts = sorted(
        [{"name": k, **v} for k, v in district_stats.items()],
        key=lambda x: (x["negative"], x["total"]),
        reverse=True,
    )[:10]

    top_departments = sorted(
        [{"name": k, **v} for k, v in dept_stats.items()],
        key=lambda x: (x["negative"], x["total"]),
        reverse=True,
    )

    payload = {
        "updated_at": now_ist.strftime("%d %b %Y, %I:%M:%S %p IST"),
        "stats": {
            "total": len(deduped),
            "positive": pos_total,
            "negative": neg_total,
            "neutral": neu_total,
            "positive_pct": pos_pct,
            "negative_pct": neg_pct,
            "neutral_pct": neu_pct,
            "crisis_score": crisis_score,
            "hindi_count": hindi_total,
            "english_count": eng_total,
            "youtube_count": yt_total,
            "website_count": web_total,
            "live_tv_count": len(live_tv_streams),
        },
        "hourly_chart": {
            "labels": [f"{h:02d}:00" for h in hours_range],
            "positive": [hourly_pos[h] for h in hours_range],
            "negative": [hourly_neg[h] for h in hours_range],
            "neutral": [hourly_neu[h] for h in hours_range],
        },
        "districts": top_districts,
        "departments": top_departments,
        "live_tv": live_tv_streams,
        "items": deduped,
    }

    FEED_CACHE[cache_key] = {"ts": now_epoch, "data": payload}
    return JSONResponse(payload)
