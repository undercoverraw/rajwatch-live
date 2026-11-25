import asyncio
import email.utils
import html
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

import feedparser
import httpx
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="RajWatch Pro - 360 Media, X & Live TV War Room")

IST = timezone(timedelta(hours=5, minutes=30))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "hi-IN,hi;q=0.9,en-IN;q=0.8,en;q=0.7",
}
YT_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept-Language": "en-US,en;q=0.9",
}

X_BEARER = (
    "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

FEED_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_SECONDS = 15
X_ACCOUNT_INDEX = 0
CACHED_SEARCH_QID: Dict[str, Any] = {"qid": "nK1dw4oV3k4w5TdtcAdSww", "ts": 0.0}

NEGATIVE_WORDS = [
    "scam", "leak", "paper leak", "protest", "strike", "outrage", "anger",
    "corruption", "bribe", "arrest", "suspended", "suspend", "fail", "failure",
    "shortage", "crisis", "attack", "accused", "allegation", "fraud", "cheat",
    "death", "killed", "murder", "rape", "crime", "clash", "violence", "lathicharge",
    "water crisis", "power cut", "blackout", "unemployment", "inflation", "delay",
    "negligence", "collapse", "fir", "ed raid", "acb", "trap", "slams", "criticize",
    "condemn", "furious", "dharna", "agitation", "boycott", "irregularities",
    "mismanagement", "unsafe", "scandal", "resign", "worst", "shame",
    "घोटाला", "पेपर लीक", "लीक", "विरोध", "धरना", "प्रदर्शन", "हंगामा", "भ्रष्टाचार",
    "आरोप", "निंदा", "नाकाम", "विफल", "आक्रोश", "गुस्सा", "लाठीचार्ज", "गिरफ्तारी",
    "निलंबित", "सस्पेंड", "हड़ताल", "चक्का जाम", "किल्लत", "संकट", "कटौती", "बिजली कटौती",
    "पानी संकट", "लापरवाही", "धांधली", "फर्जीवाड़ा", "ठगी", "रिश्वत", "एसीबी", "ट्रैप",
    "हत्या", "अपराध", "बवाल", "झड़प", "हमला", "बेरोजगारी", "महंगाई", "नाराजगी",
    "इस्तीफा", "घेराव", "बहिष्कार", "सवाल", "तंज", "निशाना", "बदहाल", "खस्ताहाल",
    "मौत", "हादसा", "अन्याय", "शर्मनाक", "चोरी", "लूट", "फेल",
    "ghotala", "virodh", "hungama", "bhrashtachar", "aarop", "aakrosh", "dhandhli",
    "laparwahi", "rishwat", "hadtal", "katal", "bawal", "sankat", "killat",
]

POSITIVE_WORDS = [
    "launch", "launched", "inaugurate", "inaugurated", "approved", "approval",
    "development", "growth", "scheme", "relief", "bonus", "award", "praise",
    "success", "successful", "record", "investment", "mou", "jobs", "recruitment",
    "vacancy", "budget", "boost", "modern", "upgrade", "benefit", "welfare",
    "free", "subsidy", "grant", "milestone", "top", "best", "historic", "gift",
    "celebrate", "congratulate", "initiative", "reforms", "digital", "clean",
    "green", "highway", "metro", "hospital", "school", "compensation", "support",
    "उद्घाटन", "शिलान्यास", "योजना", "तोहफा", "सौगात", "विकास", "बधाई", "सफल",
    "सफलता", "राहत", "मंजूरी", "बजट", "निवेश", "रोजगार", "भर्ती", "नौकरी",
    "पुरस्कार", "सम्मान", "तारीफ", "सराहना", "ऐतिहासिक", "रिकॉर्ड", "लाभ", "फायदा",
    "मुफ्त", "फ्री", "सब्सिडी", "मुआवजा", "खुशखबरी", "बेहतर", "प्रगति", "कल्याण",
    "समझौता", "एमओयू", "बढ़ोतरी", "मजबूत", "उपलब्धि", "शुरुआत", "सहायता", "स्वागत",
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
        "cm", "bhajanlal", "cmo", "rajcmo", "congress", "bjp", "minister", "mla", "mp",
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

    if len(neg_hits) > len(pos_hits):
        label = "negative"
    elif len(pos_hits) > len(neg_hits):
        label = "positive"
    elif len(neg_hits) > 0:
        label = "negative"
    else:
        label = "neutral"

    return {
        "label": label,
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
    diff = max(0, int((now_ist - dt_ist).total_seconds()))
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


def parse_twitter_time(raw: str, fallback: datetime) -> datetime:
    if not raw:
        return fallback
    try:
        return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y").astimezone(IST)
    except Exception:
        return fallback


def parse_yt_relative_time(pub_text: str, now_ist: datetime) -> Tuple[datetime, bool]:
    """Parses both English and Hindi YouTube relative timestamps accurately."""
    t = (pub_text or "").lower().strip()
    if not t:
        return (now_ist - timedelta(hours=4), False)

    m_sec = re.search(r"(\d+)\s*(?:second|sec|सेकंड)", t)
    if m_sec:
        return (now_ist - timedelta(seconds=int(m_sec.group(1))), True)

    m_min = re.search(r"(\d+)\s*(?:minute|min|मिनट)", t)
    if m_min:
        return (now_ist - timedelta(minutes=int(m_min.group(1))), True)

    m_hr = re.search(r"(\d+)\s*(?:hour|hr|घं|घंट)", t)
    if m_hr:
        return (now_ist - timedelta(hours=int(m_hr.group(1))), True)

    m_day = re.search(r"(\d+)\s*(?:day|दिन)", t)
    if m_day:
        return (now_ist - timedelta(days=int(m_day.group(1))), True)

    m_wk = re.search(r"(\d+)\s*(?:week|सप्ताह|हफ्त)", t)
    if m_wk:
        return (now_ist - timedelta(weeks=int(m_wk.group(1))), True)

    m_mo = re.search(r"(\d+)\s*(?:month|महीन)", t)
    if m_mo:
        return (now_ist - timedelta(days=30 * int(m_mo.group(1))), True)

    return (now_ist - timedelta(hours=4), False)


def get_x_accounts() -> List[Tuple[str, str]]:
    accounts: List[Tuple[str, str]] = []
    for sfx in ["", "_2", "_3"]:
        tok = (os.getenv(f"X_AUTH_TOKEN{sfx}") or "").strip().strip('"').strip("'")
        ct0 = (os.getenv(f"X_CT0{sfx}") or "").strip().strip('"').strip("'")
        if tok and ct0:
            accounts.append((tok, ct0))
    return accounts


def build_x_headers(auth_token: str, ct0: str) -> Dict[str, str]:
    return {
        "authorization": X_BEARER,
        "x-csrf-token": ct0,
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "x-twitter-client-language": "en",
        "cookie": f"auth_token={auth_token}; ct0={ct0}",
        "referer": "https://x.com/search",
        "origin": "https://x.com",
        "user-agent": HEADERS["User-Agent"],
        "accept": "*/*",
    }


def _make_tweet_item(
    tw_id: str,
    text: str,
    display_name: str,
    screen_name: str,
    created_at: str,
    likes: int,
    retweets: int,
    now_ist: datetime,
) -> Dict[str, Any]:
    dt_ist = parse_twitter_time(created_at, now_ist - timedelta(hours=2))
    sentiment = analyze_sentiment(text)
    return {
        "id": f"x_{tw_id}",
        "title": text,
        "source": f"{display_name} (@{screen_name})",
        "url": f"https://x.com/{screen_name}/status/{tw_id}",
        "category": "x",
        "language": detect_language(text),
        "sentiment": sentiment["label"],
        "triggers": sentiment["triggers"],
        "district": detect_district(text),
        "department": detect_department(text),
        "published_ts": dt_ist.timestamp(),
        "is_today": dt_ist.date() == now_ist.date(),
        "hour_ist": dt_ist.hour,
        "time_str": dt_ist.strftime("%I:%M %p"),
        "time_ago": format_time_ago(dt_ist, now_ist),
        "metrics": f"♥ {likes}  ↻ {retweets}",
        "video_id": None,
    }


async def fetch_x_adaptive(
    client: httpx.AsyncClient,
    x_query: str,
    auth_token: str,
    ct0: str,
) -> Tuple[List[Dict[str, Any]], int, str]:
    now_ist = datetime.now(IST)
    headers = build_x_headers(auth_token, ct0)
    params = {
        "q": f"({x_query}) -filter:retweets",
        "count": "20",
        "tweet_search_mode": "live",
        "tweet_mode": "extended",
        "query_source": "typed_query",
        "pc": "1",
        "spelling_corrections": "1",
    }
    try:
        resp = await client.get(
            "https://x.com/i/api/2/search/adaptive.json",
            headers=headers,
            params=params,
            timeout=10.0,
        )
        if resp.status_code != 200:
            return ([], resp.status_code, f"HTTP {resp.status_code}")

        data = resp.json()
        global_obj = data.get("globalObjects", {})
        tweets_map = global_obj.get("tweets", {})
        users_map = global_obj.get("users", {})

        items: List[Dict[str, Any]] = []
        for tw_id, tw in tweets_map.items():
            text = clean_text(tw.get("full_text") or tw.get("text") or "")
            if not text:
                continue
            uid = str(tw.get("user_id_str") or tw.get("user_id") or "")
            user = users_map.get(uid, {})
            items.append(
                _make_tweet_item(
                    tw_id=str(tw_id),
                    text=text,
                    display_name=user.get("name", "X User"),
                    screen_name=user.get("screen_name", "x"),
                    created_at=tw.get("created_at", ""),
                    likes=int(tw.get("favorite_count", 0) or 0),
                    retweets=int(tw.get("retweet_count", 0) or 0),
                    now_ist=now_ist,
                )
            )
        return (items, 200, "OK")
    except Exception as e:
        return ([], 0, str(e)[:80])


async def _refresh_search_qid(client: httpx.AsyncClient) -> str:
    now_ts = time.time()
    if now_ts - CACHED_SEARCH_QID["ts"] < 1800:
        return CACHED_SEARCH_QID["qid"]
    try:
        home = await client.get("https://x.com", headers=HEADERS, timeout=8.0, follow_redirects=True)
        js_urls = re.findall(r'https://abs\.twimg\.com/responsive-web/client-web/main\.[A-Za-z0-9_-]+\.js', home.text)
        if js_urls:
            js_resp = await client.get(js_urls[0], headers=HEADERS, timeout=8.0)
            m = re.search(r'queryId:"([A-Za-z0-9_-]+)",operationName:"SearchTimeline"', js_resp.text)
            if m:
                CACHED_SEARCH_QID["qid"] = m.group(1)
                CACHED_SEARCH_QID["ts"] = now_ts
    except Exception:
        pass
    return CACHED_SEARCH_QID["qid"]


def _extract_graphql_tweets(obj: Any, results: List[Dict[str, Any]]) -> None:
    if isinstance(obj, dict):
        if "tweet_results" in obj and isinstance(obj["tweet_results"], dict):
            res = obj["tweet_results"].get("result")
            if isinstance(res, dict):
                if res.get("__typename") == "TweetWithVisibilityResults" and "tweet" in res:
                    res = res["tweet"]
                if isinstance(res, dict) and "legacy" in res:
                    results.append(res)
            return
        for val in obj.values():
            _extract_graphql_tweets(val, results)
    elif isinstance(obj, list):
        for elem in obj:
            _extract_graphql_tweets(elem, results)


async def fetch_x_graphql(
    client: httpx.AsyncClient,
    x_query: str,
    auth_token: str,
    ct0: str,
) -> Tuple[List[Dict[str, Any]], int, str]:
    now_ist = datetime.now(IST)
    qid = await _refresh_search_qid(client)
    headers = build_x_headers(auth_token, ct0)
    variables = {
        "rawQuery": f"({x_query}) -filter:retweets",
        "count": 20,
        "querySource": "typed_query",
        "product": "Latest",
    }
    features = {
        "rweb_tipjar_consumption_enabled": True,
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "responsive_web_graphql_timeline_navigation_enabled": True,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "communities_web_enable_tweet_community_results_fetch": True,
        "c9s_tweet_anatomy_moderator_badge_enabled": True,
        "articles_preview_enabled": True,
        "responsive_web_edit_tweet_api_enabled": True,
        "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
        "view_counts_everywhere_api_enabled": True,
        "longform_notetweets_consumption_enabled": True,
        "responsive_web_twitter_article_tweet_consumption_enabled": True,
        "tweet_awards_web_tipping_enabled": False,
        "freedom_of_speech_not_reach_fetch_enabled": True,
        "standardized_nudges_misinfo": True,
        "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
        "longform_notetweets_rich_text_read_enabled": True,
        "longform_notetweets_inline_media_enabled": True,
        "responsive_web_enhance_cards_enabled": False,
    }
    try:
        resp = await client.get(
            f"https://x.com/i/api/graphql/{qid}/SearchTimeline",
            headers=headers,
            params={
                "variables": json.dumps(variables, separators=(",", ":")),
                "features": json.dumps(features, separators=(",", ":")),
            },
            timeout=10.0,
        )
        if resp.status_code != 200:
            return ([], resp.status_code, f"HTTP {resp.status_code}")

        raw_tweets: List[Dict[str, Any]] = []
        _extract_graphql_tweets(resp.json(), raw_tweets)

        items: List[Dict[str, Any]] = []
        seen = set()
        for res in raw_tweets:
            legacy = res.get("legacy", {})
            tw_id = str(res.get("rest_id") or legacy.get("id_str") or "")
            if not tw_id or tw_id in seen:
                continue
            seen.add(tw_id)
            text = clean_text(legacy.get("full_text") or "")
            if not text:
                continue
            user_legacy = (
                res.get("core", {})
                .get("user_results", {})
                .get("result", {})
                .get("legacy", {})
            )
            items.append(
                _make_tweet_item(
                    tw_id=tw_id,
                    text=text,
                    display_name=user_legacy.get("name", "X User"),
                    screen_name=user_legacy.get("screen_name", "x"),
                    created_at=legacy.get("created_at", ""),
                    likes=int(legacy.get("favorite_count", 0) or 0),
                    retweets=int(legacy.get("retweet_count", 0) or 0),
                    now_ist=now_ist,
                )
            )
        return (items, 200, "OK")
    except Exception as e:
        return ([], 0, str(e)[:80])


async def fetch_x_backup_stream(
    client: httpx.AsyncClient,
    x_query: str,
) -> List[Dict[str, Any]]:
    handles_q = (
        "(site:x.com/RajCMO OR site:x.com/BhajanlalBjp OR site:x.com/INCRajasthan "
        "OR site:x.com/BJP4Rajasthan OR site:x.com/1stIndiaNews OR site:x.com/News18Rajasthan "
        "OR site:x.com/zeerajasthan_)"
    )
    res1, res2 = await asyncio.gather(
        fetch_google_news(
            client, f"(site:x.com OR site:twitter.com) ({x_query})", lang="hi", category="x", limit=18
        ),
        fetch_google_news(client, handles_q, lang="hi", category="x", limit=12),
        return_exceptions=True,
    )
    out: List[Dict[str, Any]] = []
    for r in (res1, res2):
        if isinstance(r, list):
            out.extend(r)
    return out


async def fetch_x_live(
    client: httpx.AsyncClient,
    kw_list: List[str],
) -> Tuple[List[Dict[str, Any]], str, str]:
    global X_ACCOUNT_INDEX
    accounts = get_x_accounts()
    x_query = " OR ".join(f'"{k}"' if " " in k else k for k in kw_list[:5])

    if not accounts:
        backup = await fetch_x_backup_stream(client, x_query)
        return (
            backup,
            "PENDING",
            "Render Environment mein X_AUTH_TOKEN aur X_CT0 add karein. Abhi Backup X Stream chal rahi hai.",
        )

    last_code = 0
    last_err = ""
    for attempt in range(len(accounts)):
        idx = (X_ACCOUNT_INDEX + attempt) % len(accounts)
        auth_token, ct0 = accounts[idx]

        items, code, err = await fetch_x_graphql(client, x_query, auth_token, ct0)
        if code == 200 and items:
            X_ACCOUNT_INDEX = (idx + 1) % len(accounts)
            return (items, "LIVE", f"X Live Connected ({len(items)} tweets)")

        items2, code2, err2 = await fetch_x_adaptive(client, x_query, auth_token, ct0)
        if code2 == 200 and items2:
            X_ACCOUNT_INDEX = (idx + 1) % len(accounts)
            return (items2, "LIVE", f"X Live Connected ({len(items2)} tweets)")

        last_code = code if code in (401, 403, 429) else code2
        last_err = f"GQL:{err} / ADP:{err2}"

    backup = await fetch_x_backup_stream(client, x_query)
    if last_code == 401:
        return (
            backup,
            "EXPIRED",
            "X ne 401 Unauthorized diya — auth_token ya ct0 expire ho gaya hai. Nayi cookies daalein (abhi Backup X posts chal rahe hain).",
        )
    return (
        backup,
        "MIRROR",
        f"Cookies theek hain, lekin X ne Render ke free cloud server IP ko direct search se roka ({last_err}). Backup X Live Index + Rajasthan Handles chal rahe hain.",
    )


async def fetch_google_news(
    client: httpx.AsyncClient,
    query: str,
    lang: str = "hi",
    category: str = "hindi_news",
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
            source = "X / Twitter" if category == "x" else "News Desk"
            if entry.get("source") and isinstance(entry.source, dict):
                source = entry.source.get("title", source)
            elif " - " in raw_title:
                parts = raw_title.rsplit(" - ", 1)
                raw_title, source = parts[0].strip(), parts[1].strip()

            dt_ist = parse_rss_datetime(entry, now_ist - timedelta(hours=3))
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
                    "is_today": dt_ist.date() == now_ist.date(),
                    "hour_ist": dt_ist.hour,
                    "time_str": dt_ist.strftime("%I:%M %p"),
                    "time_ago": format_time_ago(dt_ist, now_ist),
                    "metrics": "",
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
    # EgJAAQ%3D%3D = Live right now | CAI%3D = Strictly Sorted by Upload Date (Newest First)
    sp = "EgJAAQ%3D%3D" if live_only else "CAI%3D"
    url = f"https://www.youtube.com/results?search_query={encoded_q}&sp={sp}"

    output: List[Dict[str, Any]] = []
    now_ist = datetime.now(IST)
    try:
        resp = await client.get(url, headers=YT_HEADERS, timeout=12.0, follow_redirects=True)
        if resp.status_code != 200:
            return output

        match = re.search(r"var ytInitialData = (\{.*?\});</script>", resp.text)
        if not match:
            match = re.search(r'ytInitialData"\]\s*=\s*(\{.*?\});', resp.text)
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

            if live_only:
                est_dt = now_ist
                time_ago_label = "LIVE NOW"
            else:
                est_dt, parsed_ok = parse_yt_relative_time(pub_text, now_ist)
                # Skip videos older than 24 hours in regular live feed
                if (now_ist.timestamp() - est_dt.timestamp()) > 86400:
                    continue
                time_ago_label = format_time_ago(est_dt, now_ist) if parsed_ok else "Live Stream"

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
                    "is_today": est_dt.date() == now_ist.date(),
                    "hour_ist": est_dt.hour,
                    "time_str": "LIVE TV" if live_only else est_dt.strftime("%I:%M %p"),
                    "time_ago": time_ago_label,
                    "metrics": view_text,
                    "video_id": vid,
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
        default="Rajasthan,Bhajanlal,RajCMO,Jaipur,RPSC,paper leak,Jila Pramukh"
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

    or_query = " OR ".join(f'"{k}"' if " " in k else k for k in kw_list[:6])
    portal_query = (
        "(site:bhaskar.com OR site:patrika.com OR site:firstindianews.com "
        "OR site:ndtv.com OR site:timesofindia.indiatimes.com OR site:aajtak.in "
        f"OR site:news18.com OR site:zeenews.india.com) ({or_query})"
    )
    yt_fallback_query = f"site:youtube.com ({or_query})"

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            fetch_x_live(client, kw_list),
            fetch_google_news(client, or_query, lang="hi", category="hindi_news", limit=35),
            fetch_google_news(client, or_query, lang="en", category="english_news", limit=30),
            fetch_google_news(client, portal_query, lang="hi", category="website", limit=25),
            fetch_youtube_search(client, f"Rajasthan news {kw_list[0]}", live_only=False, limit=18),
            fetch_youtube_search(client, "Rajasthan news live tv News18 Zee First India", live_only=True, limit=6),
            fetch_google_news(client, yt_fallback_query, lang="hi", category="youtube", limit=15),
            return_exceptions=True,
        )

    all_items: List[Dict[str, Any]] = []
    live_tv_streams: List[Dict[str, Any]] = []
    x_status = "PENDING"
    x_message = ""

    if not isinstance(results[0], Exception) and isinstance(results[0], tuple):
        x_items, x_status, x_message = results[0]
        all_items.extend(x_items)

    for idx in range(1, len(results)):
        res = results[idx]
        if isinstance(res, Exception) or not isinstance(res, list):
            continue
        if idx == 5:
            live_tv_streams = res
        else:
            all_items.extend(res)

    now_ist = datetime.now(IST)
    cutoff_ts = now_ist.timestamp() - 86400

    # Strictly sort newest first (minutes ago at top) & keep last 24h only
    deduped: List[Dict[str, Any]] = []
    seen_titles = set()
    for item in sorted(all_items, key=lambda x: x["published_ts"], reverse=True):
        if item["published_ts"] < cutoff_ts:
            continue
        key = re.sub(r"\W+", "", item["title"].lower())[:55]
        if not key or key in seen_titles:
            continue
        seen_titles.add(key)
        deduped.append(item)

    hours_range = list(range(0, now_ist.hour + 1))
    hourly_pos = {h: 0 for h in hours_range}
    hourly_neg = {h: 0 for h in hours_range}
    hourly_neu = {h: 0 for h in hours_range}

    district_stats: Dict[str, Dict[str, int]] = {}
    dept_stats: Dict[str, Dict[str, int]] = {}

    pos_total = neg_total = neu_total = 0
    x_total = hindi_total = eng_total = yt_total = web_total = 0

    for item in deduped:
        s = item["sentiment"]
        if s == "positive":
            pos_total += 1
        elif s == "negative":
            neg_total += 1
        else:
            neu_total += 1

        cat = item["category"]
        if cat == "x":
            x_total += 1
        elif cat == "youtube":
            yt_total += 1
        elif cat == "website":
            web_total += 1

        if item["language"] == "Hindi":
            hindi_total += 1
        else:
            eng_total += 1

        # Plot ONLY today's IST stories on today's hourly chart
        if item.get("is_today"):
            h = item["hour_ist"]
            if h in hourly_pos:
                if s == "positive":
                    hourly_pos[h] += 1
                elif s == "negative":
                    hourly_neg[h] += 1
                else:
                    hourly_neu[h] += 1

        d = item["district"]
        if d not in district_stats:
            district_stats[d] = {"total": 0, "negative": 0, "positive": 0}
        district_stats[d]["total"] += 1
        if s == "negative":
            district_stats[d]["negative"] += 1
        elif s == "positive":
            district_stats[d]["positive"] += 1

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
        "x_status": x_status,
        "x_message": x_message,
        "stats": {
            "total": len(deduped),
            "x_count": x_total,
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
