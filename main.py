import asyncio
import email.utils
import hashlib
import html
import json
import logging
import os
import re
import secrets
import tempfile
import time
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import feedparser
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from twikit import Client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ciu")

IST = timezone(timedelta(hours=5, minutes=30))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "hi-IN,hi;q=0.9,en-IN;q=0.8,en;q=0.7",
}
YT_LIVE_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": "CONSENT=YES+cb; SOCS=CAI",
}

DEFAULT_KEYWORDS = ["Bhajanlal", "RajCMO", "Rajasthan govt", "Jaipur", "paper leak", "RPSC"]
ACCESS_KEY = os.getenv("CIU_ACCESS_KEY", "").strip()
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
STATE_KEY = "ciuwatch:state:v2"
STATE_FILE = Path(tempfile.gettempdir()) / "ciuwatch_state_v2.json"


def parse_feed_env(name: str) -> List[Tuple[str, str]]:
    """Format: Naam|https://url;Naam2|https://url2"""
    out = []
    for part in os.getenv(name, "").split(";"):
        if "|" in part:
            n, u = part.split("|", 1)
            n, u = n.strip(), u.strip()
            if n and u.startswith("http"):
                out.append((n, u))
    return out


YOUTUBE_CHANNELS = [
    ("First India News", "UC_cZYFovNQTsKw1ZLmO_AMA"),
    ("Zee Rajasthan", "UCfnvGSUXKIuaCv-9E9a2UJg"),
    ("News18 Rajasthan", "UC7FlLbNo66YsCEAPuJITiNg"),
]
def _gn_site(domain: str) -> str:
    q = urllib.parse.quote(f"site:{domain} राजस्थान when:1d")
    return f"https://news.google.com/rss/search?q={q}&hl=hi&gl=IN&ceid=IN:hi"


NEWSPAPER_FEEDS = [
    ("Dainik Bhaskar", _gn_site("bhaskar.com")),
    ("Rajasthan Patrika", _gn_site("patrika.com")),
    ("Zee News Rajasthan", _gn_site("zeenews.india.com")),
] + parse_feed_env("PAPER_FEEDS")
EXTRA_FEEDS = parse_feed_env("EXTRA_RSS")  # "FB:Naam|url" -> Facebook, "IG:Naam|url" -> Instagram

# ----------------------------------------------------------------------------
# Word lists
# ----------------------------------------------------------------------------
STRONG_NEG = [
    "scam", "paper leak", "leak", "protest", "dharna", "ghotala", "bhrashtachar", "lathicharge",
    "corruption", "bribe", "suspended", "allegation", "fraud", "boycott", "resign", "negligence",
    "acb trap", "outrage", "shortage", "power cut", "water crisis", "crisis", "unemployment",
    "inflation", "failure", "jumla", "barbad",
    "पेपर लीक", "लीक", "धरना", "विरोध", "घोटाला", "भ्रष्टाचार", "लाठीचार्ज", "हंगामा", "नाराज",
    "मुर्दाबाद", "प्रदर्शन", "आक्रोश", "गुस्सा", "निलंबित", "सस्पेंड", "हड़ताल", "चक्का जाम",
    "किल्लत", "संकट", "कटौती", "लापरवाही", "धांधली", "फर्जीवाड़ा", "ठगी", "रिश्वत", "एसीबी",
    "बेरोजगारी", "महंगाई", "इस्तीफा", "घेराव", "शर्मनाक", "बदहाल", "नाकाम", "विफल", "निंदा", "आरोप",
]
INCIDENT = [
    "accident", "fire", "murder", "crime", "arrest", "arrested", "death", "killed", "violence", "clash",
    "हादसा", "आग", "हत्या", "अपराध", "गिरफ्तार", "गिरफ्तारी", "मौत", "बवाल", "झड़प", "चोरी", "लूट",
]
POSITIVE = [
    "launch", "launched", "inaugurate", "inaugurated", "approved", "approval", "development", "growth",
    "scheme", "relief", "bonus", "award", "praise", "success", "successful", "record", "investment",
    "mou", "jobs", "recruitment", "vacancy", "boost", "welfare", "subsidy", "grant", "milestone",
    "historic", "initiative", "reforms", "congratulations", "proud", "kudos", "saugat", "tohfa", "vikas",
    "उद्घाटन", "लोकार्पण", "शिलान्यास", "योजना", "तोहफा", "सौगात", "विकास", "बधाई", "सफल", "सफलता",
    "राहत", "मंजूरी", "निवेश", "रोजगार", "भर्ती", "नौकरी", "पुरस्कार", "सम्मान", "तारीफ", "सराहना",
    "ऐतिहासिक", "रिकॉर्ड", "लाभ", "मुफ्त", "सब्सिडी", "मुआवजा", "खुशखबरी", "प्रगति", "कल्याण",
    "समझौता", "एमओयू", "उपलब्धि", "सहायता", "स्वागत", "धन्यवाद", "आभार", "जिंदाबाद", "शानदार",
]
ATTACK = ["slams", "targets", "hits out", "निशाना", "तंज", "आड़े हाथ", "कटघरे में", "हमला बोला"]
GOV_TERMS = [
    "bhajanlal", "bhajan lal", "cmo", "rajcmo", "cm", "chief minister", "govt", "government",
    "bjp", "minister", "भजनलाल", "भजन लाल", "मुख्यमंत्री", "सरकार", "भाजपा", "मंत्री", "मंत्रिमंडल",
]
OPP_TERMS = [
    "congress", "gehlot", "ashok gehlot", "dotasra", "sachin pilot", "tikaram jully", "opposition",
    "कांग्रेस", "गहलोत", "डोटासरा", "सचिन पायलट", "टीकाराम जूली", "विपक्ष",
]

DISTRICTS = {
    "Jaipur": ["jaipur", "जयपुर", "jda"], "Jodhpur": ["jodhpur", "जोधपुर"],
    "Udaipur": ["udaipur", "उदयपुर"], "Kota": ["kota", "कोटा"], "Ajmer": ["ajmer", "अजमेर"],
    "Bikaner": ["bikaner", "बीकानेर"], "Bharatpur": ["bharatpur", "भरतपुर"], "Sikar": ["sikar", "सीकर"],
    "Alwar": ["alwar", "अलवर"], "Barmer": ["barmer", "बाड़मेर"], "Nagaur": ["nagaur", "नागौर"],
    "Jhunjhunu": ["jhunjhunu", "झुंझुनू", "झुंझुनूं"], "Bhilwara": ["bhilwara", "भीलवाड़ा"],
    "Pali": ["pali", "पाली"], "Dausa": ["dausa", "दौसा"], "Churu": ["churu", "चूरू"],
    "Ganganagar": ["ganganagar", "गंगानगर"], "Hanumangarh": ["hanumangarh", "हनुमानगढ़"],
    "Chittorgarh": ["chittorgarh", "चित्तौड़गढ़"], "Banswara": ["banswara", "बांसवाड़ा"],
    "Jaisalmer": ["jaisalmer", "जैसलमेर"], "Tonk": ["tonk", "टोंक"],
    "Sawai Madhopur": ["sawai madhopur", "सवाई माधोपुर"], "Karauli": ["karauli", "करौली"],
    "Dholpur": ["dholpur", "धौलपुर"], "Jhalawar": ["jhalawar", "झालावाड़"],
}
DEPARTMENTS = {
    "Politics & CMO": [
        "cm", "bhajanlal", "cmo", "rajcmo", "congress", "bjp", "minister", "mla", "mp", "election",
        "zila pramukh", "jila pramukh", "panchayat", "vidhan sabha", "भजनलाल", "मुख्यमंत्री", "कांग्रेस",
        "भाजपा", "मंत्री", "विधायक", "चुनाव", "जिला प्रमुख", "विधानसभा", "सरकार", "मंत्रिमंडल", "पंचायत",
    ],
    "Education & RPSC": [
        "rpsc", "reet", "exam", "paper", "school", "college", "university", "teacher", "student", "rssb",
        "परीक्षा", "पेपर", "स्कूल", "शिक्षक", "छात्र", "भर्ती",
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

# ----------------------------------------------------------------------------
# Matching: proper word boundaries (Hindi-aware), so "आग" does not hit "आगे"
# ----------------------------------------------------------------------------
DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_WCH = r"\w\u0900-\u097F"
_NOT_PREV = rf"(?<![{_WCH}])"
_NOT_WORD = rf"(?![{_WCH}])"


def compile_terms(terms: List[str], full: bool = False) -> "re.Pattern":
    pats = []
    for t in sorted({x.lower().strip() for x in terms if x.strip()}, key=len, reverse=True):
        body = r"\s+".join(re.escape(p) for p in t.split())
        if DEVANAGARI.search(t):
            right = "" if (not full and len(t) > 3) else _NOT_WORD
        else:
            right = (r"(?:s|es|ed|ing|d)?" + _NOT_WORD) if (not full and len(t) >= 5) else _NOT_WORD
        pats.append(_NOT_PREV + body + right)
    return re.compile("|".join(pats))


STRONG_RX = compile_terms(STRONG_NEG)
INCIDENT_RX = compile_terms(INCIDENT)
POS_RX = compile_terms(POSITIVE)
ATTACK_RX = compile_terms(ATTACK)
GOV_RX = compile_terms(GOV_TERMS, full=True)
OPP_RX = compile_terms(OPP_TERMS, full=True)
DISTRICT_RX = {d: compile_terms(t, full=True) for d, t in DISTRICTS.items()}
DEPT_RX = {d: compile_terms(t, full=True) for d, t in DEPARTMENTS.items()}


def clean_text(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def detect_language(text: str) -> str:
    return "Hindi" if DEVANAGARI.search(text or "") else "English"


def analyze_sentiment(text: str) -> Tuple[str, str]:
    """Sarkar ke nazariye se: negative = sarkar ke khilaf. Heuristic hai, perfect nahi."""
    low = (text or "").lower()
    neg = len(STRONG_RX.findall(low)) * 2 + len(INCIDENT_RX.findall(low))
    pos = len(POS_RX.findall(low)) * 1.5
    net = pos - neg
    am = ATTACK_RX.search(low)
    if am:
        gm, om = GOV_RX.search(low), OPP_RX.search(low)
        if gm and om:
            net += 3 if gm.start() < om.start() else -3  # jo pehle aaya wahi aksar kartaa hai
        elif om:
            net -= 2
        elif gm:
            net -= 1
        else:
            net -= 1
    if net <= -2:
        return "negative", "ALERT"
    if net >= 1.5:
        return "positive", "POSITIVE"
    return "neutral", "INFO"


def _best(rx_map: Dict[str, "re.Pattern"], text: str, default: str) -> str:
    low = (text or "").lower()
    best, best_n = None, 0
    for name, rx in rx_map.items():
        n = len(rx.findall(low))
        if n > best_n:
            best, best_n = name, n
    return best or default


def detect_district(text: str) -> str:
    return _best(DISTRICT_RX, text, "Statewide")


def detect_department(text: str) -> str:
    return _best(DEPT_RX, text, "General / Admin")


# ----------------------------------------------------------------------------
# Time / ids / items
# ----------------------------------------------------------------------------
FIRST_SEEN: Dict[str, float] = {}


def first_seen(id_: str) -> float:
    if len(FIRST_SEEN) > 4000:
        for k in list(FIRST_SEEN)[:1000]:
            FIRST_SEEN.pop(k, None)
    return FIRST_SEEN.setdefault(id_, time.time())


def hid(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:16]


def safe_url(u: str) -> str:
    u = (u or "").strip()
    return u if re.match(r"^https?://", u, re.I) else ""


def parse_timestamp(val: Any) -> Optional[float]:
    now_ts = time.time()
    if not val:
        return None
    if isinstance(val, datetime):
        dt = val if val.tzinfo else val.replace(tzinfo=timezone.utc)
        return min(dt.timestamp(), now_ts)
    s = str(val).strip()
    if not s:
        return None
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
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
        return None


def enrich_item(item: Dict[str, Any]) -> Dict[str, Any]:
    text = clean_text(item.get("text", ""))
    sentiment, risk = analyze_sentiment(text)
    dt_ist = datetime.fromtimestamp(float(item["ts"]), tz=IST)
    item.update({
        "text": text, "sentiment": sentiment, "risk": risk,
        "language": detect_language(text), "district": detect_district(text),
        "department": detect_department(text), "hour_ist": dt_ist.hour,
        "date_ist": dt_ist.strftime("%Y-%m-%d"), "date_label": dt_ist.strftime("%d %b"),
        "time_str": dt_ist.strftime("%I:%M %p"),
    })
    return item


def build_item(*, id_: str, source: str, author: str, text: str, url: str, ts: Optional[float],
               handle: str = "", likes: int = 0, retweets: int = 0, video_id: str = "",
               kws: Optional[List[str]] = None) -> Dict[str, Any]:
    approx = ts is None
    item = {
        "id": id_, "source": source, "author": author, "handle": handle, "text": text,
        "url": safe_url(url), "ts": first_seen(id_) if approx else ts, "ts_approx": approx,
        "likes": int(likes or 0), "retweets": int(retweets or 0),
    }
    if video_id:
        item["video_id"] = video_id
    if kws is not None:
        item["kws"] = kws
    return enrich_item(item)


def validate_date(date_str: str, today_str: str) -> Optional[Tuple[str, str]]:
    s = (date_str or "").strip()
    if not s:
        return None
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None
    if dt.strftime("%Y-%m-%d") > today_str:
        return None
    return dt.strftime("%Y-%m-%d"), (dt + timedelta(days=1)).strftime("%Y-%m-%d")


def sanitize_keywords(raw: str) -> List[str]:
    out: List[str] = []
    for k in (raw or "").split(","):
        k = re.sub(r'["()]', " ", k)
        k = re.sub(r"\s+", " ", k).strip().lstrip("-").strip()
        if not k or len(k) > 40 or ":" in k or k.upper() in ("OR", "AND", "NOT"):
            continue
        if k.lower() not in [x.lower() for x in out]:
            out.append(k)
    return out[:10]


# ----------------------------------------------------------------------------
# Shared state, persistence (Upstash free Redis, else temp file)
# ----------------------------------------------------------------------------
HTTP: Optional[httpx.AsyncClient] = None
LIVE_TWEET_STORE: Dict[str, Dict[str, Any]] = {}
MANUAL_STORE: Dict[str, Dict[str, Any]] = {}
DAILY_TRACKER: Dict[str, Any] = {"date": "", "items": {}}
CACHE: Dict[str, Dict[str, Any]] = {}
LOCKS: Dict[str, asyncio.Lock] = {}
BG_TASKS: set = set()
SOURCE_HEALTH: Dict[str, Dict[str, Any]] = {}
X_CLIENTS: Dict[int, Any] = {}
X_COOLDOWN: Dict[int, float] = {}
cookie_turn = 0
_dirty = False


def mark_dirty():
    global _dirty
    _dirty = True


def health_ok(name: str, count: int):
    SOURCE_HEALTH[name] = {"ok": True, "ts": time.time(), "last_ok": time.time(), "count": count, "error": ""}


def health_fail(name: str, err: Any):
    prev = SOURCE_HEALTH.get(name, {})
    SOURCE_HEALTH[name] = {"ok": False, "ts": time.time(), "last_ok": prev.get("last_ok", 0),
                           "count": 0, "error": (str(err) or repr(err))[:140]}


async def _kv_get() -> Optional[str]:
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            r = await HTTP.post(UPSTASH_URL, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                                json=["GET", STATE_KEY], timeout=8)
            return r.json().get("result")
        except Exception as e:
            log.warning("Upstash GET fail: %s", e)
            return None
    try:
        return STATE_FILE.read_text(encoding="utf-8") if STATE_FILE.exists() else None
    except Exception:
        return None


async def _kv_set(val: str):
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            await HTTP.post(UPSTASH_URL, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                            json=["SET", STATE_KEY, val, "EX", 604800], timeout=8)
        except Exception as e:
            log.warning("Upstash SET fail: %s", e)
        return
    try:
        STATE_FILE.write_text(val, encoding="utf-8")
    except Exception:
        pass


async def load_state():
    raw = await _kv_get()
    if not raw:
        return
    try:
        data = json.loads(raw)
        LIVE_TWEET_STORE.update(data.get("tweets") or {})
        MANUAL_STORE.update(data.get("manual") or {})
        d = data.get("daily") or {}
        DAILY_TRACKER["date"] = d.get("date", "")
        DAILY_TRACKER["items"] = d.get("items", {})
        log.info("State loaded: %d tweets, %d manual", len(LIVE_TWEET_STORE), len(MANUAL_STORE))
    except Exception as e:
        log.warning("State load fail: %s", e)


async def save_state(force: bool = False):
    global _dirty
    if not _dirty and not force:
        return
    _dirty = False
    tweets = dict(LIVE_TWEET_STORE)
    while True:
        s = json.dumps({"tweets": tweets, "manual": MANUAL_STORE, "daily": DAILY_TRACKER}, ensure_ascii=False)
        if len(s) < 900_000 or not tweets:
            break
        for k in sorted(tweets, key=lambda x: tweets[x].get("ts", 0))[:100]:
            tweets.pop(k, None)
    await _kv_set(s)


async def autosave_loop():
    while True:
        await asyncio.sleep(90)
        try:
            await save_state()
        except Exception as e:
            log.warning("autosave: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global HTTP
    HTTP = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=True)
    await load_state()
    if not ACCESS_KEY:
        log.warning("CIU_ACCESS_KEY set nahi hai - API sabke liye khuli hai!")
    task = asyncio.create_task(autosave_loop())
    yield
    task.cancel()
    await save_state(force=True)
    await HTTP.aclose()


app = FastAPI(title="CIU Watch", lifespan=lifespan)


async def require_key(x_access_key: str = Header(default=""), key: str = Query(default="")):
    if not ACCESS_KEY:
        return
    supplied = (x_access_key or key or "").encode()
    if not secrets.compare_digest(supplied, ACCESS_KEY.encode()):
        await asyncio.sleep(1)
        raise HTTPException(status_code=401, detail="invalid key")


# ----------------------------------------------------------------------------
# Stale-while-revalidate cache: sab users ke liye ek hi fetch
# ----------------------------------------------------------------------------
async def _bg_refresh(key: str, factory: Callable[[], Awaitable[Any]], lock: asyncio.Lock):
    async with lock:
        try:
            _put(key, await factory())
        except Exception as e:
            log.warning("bg refresh %s: %s", key, e)


def _put(key: str, val: Any):
    CACHE[key] = {"ts": time.time(), "val": val}
    if len(CACHE) > 60:
        for k in sorted(CACHE, key=lambda x: CACHE[x]["ts"])[:20]:
            CACHE.pop(k, None)


async def swr(key: str, ttl: float, factory: Callable[[], Awaitable[Any]], force: bool = False,
              min_gap: float = 10.0):
    ent = CACHE.get(key)
    lock = LOCKS.setdefault(key, asyncio.Lock())
    if ent:
        age = time.time() - ent["ts"]
        if age < (min_gap if force else ttl):
            return ent["val"]
        if not force:
            if not lock.locked():
                t = asyncio.create_task(_bg_refresh(key, factory, lock))
                BG_TASKS.add(t)
                t.add_done_callback(BG_TASKS.discard)
            return ent["val"]
    async with lock:
        ent = CACHE.get(key)
        if ent and time.time() - ent["ts"] < min_gap:
            return ent["val"]
        val = await factory()
        _put(key, val)
        return val


# ----------------------------------------------------------------------------
# X (twikit/twifork)
# ----------------------------------------------------------------------------
def get_cookie_pool():
    pool = []
    for s in ["", "_2", "_3"]:
        auth = os.getenv(f"X_AUTH_TOKEN{s}", "").strip()
        ct0 = os.getenv(f"X_CT0{s}", "").strip()
        if auth and ct0:
            pool.append({"auth_token": auth, "ct0": ct0})
    return pool


def build_x_query(kw: List[str], date_window: Optional[Tuple[str, str]]) -> str:
    q = "(" + " OR ".join(f'"{k}"' if " " in k else k for k in kw[:7]) + ") -filter:retweets"
    if date_window:
        q += f" since:{date_window[0]} until:{date_window[1]}"
    return q


def parse_tweets(tweets, kw: List[str]) -> List[Dict[str, Any]]:
    out = []
    kws = [k.lower() for k in kw[:7]]
    for t in tweets or []:
        text = getattr(t, "text", "") or ""
        tid = str(getattr(t, "id", "") or "")
        if not text or not tid:
            continue
        user = getattr(t, "user", None)
        handle = getattr(user, "screen_name", "unknown") if user else "unknown"
        name = getattr(user, "name", handle) if user else handle
        created = getattr(t, "created_at_datetime", None) or getattr(t, "created_at", "")
        out.append(build_item(
            id_=f"x_{tid}", source="X", author=f"{name} (@{handle})", handle=f"@{handle}", text=text,
            url=f"https://x.com/{handle}/status/{tid}", ts=parse_timestamp(created),
            likes=getattr(t, "favorite_count", 0), retweets=getattr(t, "retweet_count", 0), kws=kws,
        ))
    return out


async def x_search(kw: List[str], date_window: Optional[Tuple[str, str]], count: int):
    global cookie_turn
    pool = get_cookie_pool()
    if not pool:
        raise RuntimeError("no_cookies")
    q = build_x_query(kw, date_window)
    n, last_err = len(pool), None
    for step in range(n):
        idx = (cookie_turn + step) % n
        if X_COOLDOWN.get(idx, 0) > time.time():
            continue
        try:
            client = X_CLIENTS.get(idx)
            if client is None:
                client = Client("en-US")
                client.set_cookies(pool[idx])
                X_CLIENTS[idx] = client
            tweets = await asyncio.wait_for(client.search_tweet(q, "Latest", count=count), timeout=20)
            cookie_turn = idx + 1
            return parse_tweets(tweets, kw)
        except Exception as e:
            last_err = e
            X_CLIENTS.pop(idx, None)
            msg = (str(e) or repr(e)).lower()
            X_COOLDOWN[idx] = time.time() + (900 if ("429" in msg or "rate" in msg) else 300)
            log.warning("X account %d fail: %s", idx, msg[:120])
    raise last_err or RuntimeError("Sab X accounts cooldown me hain")


def prune_live_tweet_store(now_ts: float):
    cutoff = now_ts - 86400
    for tid in [t for t, tw in LIVE_TWEET_STORE.items() if tw.get("ts", 0) < cutoff]:
        LIVE_TWEET_STORE.pop(tid, None)
    if len(LIVE_TWEET_STORE) > 500:
        for tid in sorted(LIVE_TWEET_STORE, key=lambda k: LIVE_TWEET_STORE[k].get("ts", 0))[:len(LIVE_TWEET_STORE) - 500]:
            LIVE_TWEET_STORE.pop(tid, None)


def stored_tweets_for(kw: List[str], now_ts: float) -> List[Dict[str, Any]]:
    prune_live_tweet_store(now_ts)
    want = {k.lower() for k in kw[:7]}
    rows = [t for t in LIVE_TWEET_STORE.values() if want & set(t.get("kws", []))]
    return sorted(rows, key=lambda x: x["ts"], reverse=True)


async def get_x_live(kw: List[str], force: bool):
    if not get_cookie_pool():
        return [], "no_cookies", "Render Environment mein X_AUTH_TOKEN aur X_CT0 add karein"
    key = "x:" + "|".join(sorted(k.lower() for k in kw[:7]))

    async def factory():
        try:
            items = await x_search(kw, None, 20)
            for tw in items:
                old = LIVE_TWEET_STORE.get(tw["id"])
                if old:
                    tw["kws"] = sorted(set(old.get("kws", [])) | set(tw["kws"]))
                LIVE_TWEET_STORE[tw["id"]] = tw
            mark_dirty()
            health_ok("X", len(items))
            return {"status": "live", "error": ""}
        except Exception as e:
            health_fail("X", e)
            return {"status": "warning", "error": f"X temporary error (purane tweets safe hain): {str(e)[:120]}"}

    res = await swr(key, 45, factory, force=force, min_gap=20)
    return stored_tweets_for(kw, time.time()), res["status"], res["error"]


async def get_x_archive(kw: List[str], force: bool, date_window: Tuple[str, str]):
    if not get_cookie_pool():
        return [], "no_cookies", "Render Environment mein X_AUTH_TOKEN aur X_CT0 add karein"
    key = f"xa:{'|'.join(sorted(k.lower() for k in kw[:7]))}:{date_window[0]}"

    async def factory():
        prev = (CACHE.get(key) or {}).get("val", {}).get("items", [])
        try:
            items = await x_search(kw, date_window, 35)
            health_ok("X", len(items))
            return {"items": items or prev, "status": "live", "error": ""}
        except Exception as e:
            health_fail("X", e)
            return {"items": prev, "status": "warning", "error": f"X temporary error: {str(e)[:120]}"}

    res = await swr(key, 600, factory, force=force, min_gap=30)
    return res["items"], res["status"], res["error"]


# ----------------------------------------------------------------------------
# News / papers / YouTube / extra RSS
# ----------------------------------------------------------------------------
async def get_news(kw: List[str], lang: str, date_window: Optional[Tuple[str, str]], force: bool = False):
    top = kw[:5] or ["Rajasthan government", "Bhajanlal Sharma", "Jaipur"]
    kw_part = "(" + " OR ".join(f'"{k}"' if " " in k else k for k in top) + ") Rajasthan"
    q = f"{kw_part} after:{date_window[0]} before:{date_window[1]}" if date_window else f"{kw_part} when:1d"
    hl = "hi&gl=IN&ceid=IN:hi" if lang == "hi" else "en-IN&gl=IN&ceid=IN:en"
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl={hl}"
    key = f"gn:{lang}:{q}"
    name = f"Google News {lang.upper()}"

    async def factory():
        prev = (CACHE.get(key) or {}).get("val", [])
        try:
            r = await HTTP.get(url, headers=HEADERS, timeout=8)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
            items = []
            for e in feed.entries[:20]:
                title = clean_text(e.get("title", ""))
                link = e.get("link", "")
                src = e.get("source", {}).get("title", "") if isinstance(e.get("source"), dict) else ""
                if not src and " - " in title:
                    title, src = [p.strip() for p in title.rsplit(" - ", 1)]
                items.append(build_item(
                    id_=f"gn_{hid(link or title)}", source="NEWS", author=src or "Rajasthan News",
                    text=title, url=link, ts=parse_timestamp(e.get("published", ""))))
            health_ok(name, len(items))
            return items
        except Exception as e:
            health_fail(name, e)
            return prev

    return await swr(key, 900 if date_window else 120, factory, force=force, min_gap=20)


async def _read_rss(name: str, url: str, source: str, limit: int = 12) -> List[Dict[str, Any]]:
    r = await HTTP.get(url, headers=HEADERS, timeout=8)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    out = []
    for e in feed.entries[:limit]:
        title = clean_text(e.get("title", ""))
                if "news.google.com" in url and " - " in title:
            title = title.rsplit(" - ", 1)[0].strip()
        if not title:
            continue
        link = e.get("link", "")
        pub = e.get("published") or e.get("updated") or ""
        out.append(build_item(id_=f"{source.lower()}_{hid(link or title)}", source=source,
                              author=name, text=title, url=link, ts=parse_timestamp(pub)))
    return out


async def get_papers(force: bool):
    async def factory():
        prev = (CACHE.get("papers") or {}).get("val", [])

        async def one(n, u):
            try:
                items = await _read_rss(n, u, "NEWSPAPER")
                health_ok(n, len(items))
                return items
            except Exception as e:
                health_fail(n, e)
                return [i for i in prev if i["author"] == n]

        return [i for b in await asyncio.gather(*(one(n, u) for n, u in NEWSPAPER_FEEDS)) for i in b]

    return await swr("papers", 300, factory, force=force, min_gap=30)


async def get_extra(force: bool):
    if not EXTRA_FEEDS:
        return []

    async def factory():
        prev = (CACHE.get("extra") or {}).get("val", [])

        async def one(n, u):
            src, label = "NEWS", n
            if n.upper().startswith("FB:"):
                src, label = "FACEBOOK", n[3:].strip()
            elif n.upper().startswith("IG:"):
                src, label = "INSTAGRAM", n[3:].strip()
            try:
                items = await _read_rss(label, u, src)
                health_ok(n, len(items))
                return items
            except Exception as e:
                health_fail(n, e)
                return [i for i in prev if i["author"] == label]

        return [i for b in await asyncio.gather(*(one(n, u) for n, u in EXTRA_FEEDS)) for i in b]

    return await swr("extra", 600, factory, force=force, min_gap=60)


async def get_youtube(force: bool):
    async def factory():
        prev = (CACHE.get("yt") or {}).get("val", [])

        async def one(name, cid):
            try:
                r = await HTTP.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}",
                                   headers=HEADERS, timeout=8)
                r.raise_for_status()
                feed = feedparser.parse(r.content)
                out = []
                for e in feed.entries[:8]:
                    vid = str(e.get("yt_videoid", "") or "")
                    link = e.get("link", "")
                    out.append(build_item(
                        id_=f"yt_{vid or hid(link)}", source="YOUTUBE", author=name, text=e.get("title", ""),
                        url=link, ts=parse_timestamp(e.get("published") or e.get("updated")), video_id=vid))
                health_ok(f"YT {name}", len(out))
                return out
            except Exception as e:
                health_fail(f"YT {name}", e)
                return [i for i in prev if i["author"] == name]

        return [i for b in await asyncio.gather(*(one(n, c) for n, c in YOUTUBE_CHANNELS)) for i in b]

    return await swr("yt", 300, factory, force=force, min_gap=30)


async def get_tv(force: bool):
    async def factory():
        yt_items = await get_youtube(False)

        async def probe(vid: str):
            """watch page देखकर पता करो: live है? embed हो सकता है?"""
            try:
                r = await HTTP.get(f"https://www.youtube.com/watch?v={vid}",
                                   headers=YT_LIVE_HEADERS, timeout=7)
                t = r.text
                live = bool(re.search(r'"isLiveNow"\s*:\s*true', t))
                emb = bool(re.search(r'"playableInEmbed"\s*:\s*true', t))
                return live, emb
            except Exception:
                return False, False

        async def one(name, cid):
            vid, embeddable = None, False
            cands = [i["video_id"] for i in sorted(yt_items, key=lambda x: x["ts"], reverse=True)
                     if i.get("author") == name and i.get("video_id")][:6]
            if cands:
                results = await asyncio.gather(*(probe(v) for v in cands))
                for v, (live, emb) in zip(cands, results):
                    if live:
                        vid, embeddable = v, emb
                        break
            if not vid:
                try:
                    r = await HTTP.get(f"https://www.youtube.com/channel/{cid}/live",
                                       headers=YT_LIVE_HEADERS, timeout=7)
                    m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"', r.text)
                    if m:
                        vid = m.group(1)
                        _, embeddable = await probe(vid)
                except Exception as e:
                    health_fail(f"TV {name}", e)
            if vid:
                health_ok(f"TV {name}", 1)
            if vid and embeddable:
                embed = f"https://www.youtube.com/embed/{vid}?autoplay=1&mute=1&rel=0"
            elif vid:
                embed = ""   # embed मना है: सिर्फ़ YouTube पर खोलने का बटन दिखेगा
            else:
                embed = f"https://www.youtube.com/embed/live_stream?channel={cid}&autoplay=1&mute=1"
            return {
                "source": name, "is_live": bool(vid), "embed_url": embed,
                "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else "",
                "watch_url": f"https://www.youtube.com/watch?v={vid}" if vid
                else f"https://www.youtube.com/channel/{cid}/live",
            }

        return list(await asyncio.gather(*(one(n, c) for n, c in YOUTUBE_CHANNELS)))

    return await swr("tv", 180, factory, force=force, min_gap=60)


# ----------------------------------------------------------------------------
# Manual Facebook / Instagram posts
# ----------------------------------------------------------------------------
class ManualPost(BaseModel):
    platform: str            # FACEBOOK | INSTAGRAM | OTHER
    author: str
    text: str
    url: str = ""
    posted_at: str = ""      # "YYYY-MM-DD HH:MM" IST, khaali = abhi
    likes: int = 0
    shares: int = 0


@app.post("/api/manual", dependencies=[Depends(require_key)])
async def add_manual(p: ManualPost):
    plat = p.platform.upper().strip()
    if plat not in ("FACEBOOK", "INSTAGRAM", "OTHER"):
        raise HTTPException(400, "platform FACEBOOK/INSTAGRAM/OTHER hona chahiye")
    text, author = clean_text(p.text)[:1000], clean_text(p.author)[:80]
    if not text or not author:
        raise HTTPException(400, "author aur text zaroori hain")
    ts = time.time()
    if p.posted_at.strip():
        try:
            ts = datetime.strptime(p.posted_at.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=IST).timestamp()
        except ValueError:
            raise HTTPException(400, "posted_at format: YYYY-MM-DD HH:MM")
    item = build_item(id_=f"man_{hid(author, text, str(int(ts)))}", source=plat if plat != "OTHER" else "NEWS",
                      author=author, text=text, url=p.url, ts=min(ts, time.time()),
                      likes=max(0, p.likes), retweets=max(0, p.shares))
    item["manual"] = True
    MANUAL_STORE[item["id"]] = item
    if len(MANUAL_STORE) > 300:
        for k in sorted(MANUAL_STORE, key=lambda x: MANUAL_STORE[x]["ts"])[:50]:
            MANUAL_STORE.pop(k, None)
    mark_dirty()
    return item


@app.delete("/api/manual/{item_id}", dependencies=[Depends(require_key)])
async def delete_manual(item_id: str):
    MANUAL_STORE.pop(item_id, None)
    mark_dirty()
    return {"ok": True}


# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------
@app.get("/")
async def serve_home():
    return FileResponse(Path(__file__).parent / "index.html")


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/ping")
async def ping():
    return {"auth_required": bool(ACCESS_KEY)}


@app.get("/api/check", dependencies=[Depends(require_key)])
async def check():
    return {"ok": True}


def crisis_score(items: List[Dict[str, Any]], now_ts: float, is_archive: bool) -> int:
    pool = items if is_archive else [i for i in items if now_ts - i["ts"] <= 10800]
    if len(pool) < 3:
        return 0
    neg = sum(1 for i in pool if i["sentiment"] == "negative")
    return int(round(100 * (0.55 * (neg / len(pool)) + 0.45 * min(1.0, neg / 15))))


@app.get("/api/feed", dependencies=[Depends(require_key)])
async def api_feed(
    keywords: str = Query(default=",".join(DEFAULT_KEYWORDS)),
    force: bool = Query(default=False),
    date: str = Query(default=""),
):
    kw_list = sanitize_keywords(keywords) or DEFAULT_KEYWORDS
    now_ist = datetime.now(IST)
    now_ts = now_ist.timestamp()
    today_str = now_ist.strftime("%Y-%m-%d")
    date_window = validate_date(date, today_str)
    is_archive = date_window is not None and date_window[0] != today_str
    active_date = date_window[0] if is_archive else today_str
    cutoff = now_ts - 86400

    if is_archive:
        x_res, news_en, news_hi, live_tv = await asyncio.gather(
            get_x_archive(kw_list, force, date_window),
            get_news(kw_list, "en", date_window, force),
            get_news(kw_list, "hi", date_window, force),
            get_tv(False),
        )
        papers, yt_items, extra = [], [], []
        manual = [m for m in MANUAL_STORE.values() if m["date_ist"] == active_date]
    else:
        x_res, news_en, news_hi, papers, yt_items, extra, live_tv = await asyncio.gather(
            get_x_live(kw_list, force),
            get_news(kw_list, "en", None, force),
            get_news(kw_list, "hi", None, force),
            get_papers(force), get_youtube(force), get_extra(force), get_tv(force),
        )
        manual = [m for m in MANUAL_STORE.values() if m["ts"] >= cutoff]

    x_items, x_status, x_error = x_res

    deduped_x, seen_x = [], set()
    for tw in sorted(x_items, key=lambda x: x["ts"], reverse=True):
        if tw["id"] in seen_x or (not is_archive and tw["ts"] < cutoff):
            continue
        seen_x.add(tw["id"])
        deduped_x.append(tw)

    deduped_media, seen_media = [], set()
    for it in sorted(news_hi + news_en + papers + yt_items + extra + manual, key=lambda x: x["ts"], reverse=True):
        if not is_archive and it["ts"] < cutoff:
            continue
        norm = re.sub(r"[^a-zA-Z0-9\u0900-\u097F]+", "", it["text"].lower())[:50]
        k = f"{it['source']}_{norm}" if norm else it["id"]
        if k in seen_media:
            continue
        seen_media.add(k)
        deduped_media.append(it)

    deduped = sorted(deduped_x[:150] + deduped_media[:170], key=lambda x: x["ts"], reverse=True)

    # ---- hourly charts ----
    hours = list(range(24)) if is_archive else list(range(now_ist.hour + 1))
    mk = lambda: {h: 0 for h in hours}  # noqa: E731
    h_pos, h_neg, h_neu, hx_pos, hx_neg, hx_neu = mk(), mk(), mk(), mk(), mk(), mk()
    x_today_count = 0

    def bump(h, s, src):
        nonlocal x_today_count
        if h not in h_pos:
            return
        {"positive": h_pos, "negative": h_neg}.get(s, h_neu)[h] += 1
        if src == "X":
            x_today_count += 1
            {"positive": hx_pos, "negative": hx_neg}.get(s, hx_neu)[h] += 1

    if is_archive:
        for it in deduped:
            bump(it["hour_ist"], it["sentiment"], it["source"])
    else:
        if DAILY_TRACKER["date"] != today_str:
            DAILY_TRACKER["date"], DAILY_TRACKER["items"] = today_str, {}
        for it in deduped:
            if it["date_ist"] == today_str:
                DAILY_TRACKER["items"][it["id"]] = {"hour": it["hour_ist"], "sentiment": it["sentiment"], "source": it["source"]}
        if len(DAILY_TRACKER["items"]) > 1500:
            for k in list(DAILY_TRACKER["items"])[:300]:
                DAILY_TRACKER["items"].pop(k, None)
        mark_dirty()
        for rec in DAILY_TRACKER["items"].values():
            bump(rec.get("hour", 0), rec.get("sentiment", "neutral"), rec.get("source", ""))

    # ---- stats ----
    cnt = {"positive": 0, "negative": 0, "neutral": 0}
    xcnt = {"positive": 0, "negative": 0, "neutral": 0}
    by_src: Dict[str, int] = {}
    hi_total = en_total = 0
    district_stats: Dict[str, Dict[str, int]] = {}
    dept_stats: Dict[str, Dict[str, int]] = {}
    account_stats: Dict[str, Dict[str, Any]] = {}

    for it in deduped:
        s, src = it["sentiment"], it["source"]
        cnt[s] += 1
        by_src[src] = by_src.get(src, 0) + 1
        if src == "X":
            xcnt[s] += 1
        if it["language"] == "Hindi":
            hi_total += 1
        else:
            en_total += 1
        for table, name in ((district_stats, it["district"]), (dept_stats, it["department"])):
            row = table.setdefault(name, {"total": 0, "negative": 0, "positive": 0})
            row["total"] += 1
            if s in ("negative", "positive"):
                row[s] += 1
        acc = account_stats.setdefault(f"{src}::{it['author']}", {
            "author": it["author"], "handle": it.get("handle", ""), "source": src, "total": 0,
            "positive": 0, "negative": 0, "neutral": 0, "likes": 0, "retweets": 0, "last_ts": it["ts"]})
        acc["total"] += 1
        acc[s] += 1
        acc["likes"] += it.get("likes", 0)
        acc["retweets"] += it.get("retweets", 0)
        acc["last_ts"] = max(acc["last_ts"], it["ts"])

    accounts_list = []
    for acc in account_stats.values():
        t = max(acc["total"], 1)
        acc["pos_pct"] = round(acc["positive"] / t * 100, 1)
        acc["neg_pct"] = round(acc["negative"] / t * 100, 1)
        acc["neu_pct"] = round(acc["neutral"] / t * 100, 1)
        acc["stance"] = ("Negative" if acc["negative"] > acc["positive"] else
                         "Positive" if acc["positive"] > acc["negative"] else
                         "Mixed" if acc["negative"] > 0 else "Neutral")
        accounts_list.append(acc)
    accounts_list.sort(key=lambda a: (a["total"], a["negative"]), reverse=True)

       # ---- Handle-wise analysis (sirf X) ----
    tag_rx = re.compile(r"#[\w\u0900-\u097F]+")
    handle_stats: Dict[str, Dict[str, Any]] = {}
    for it in deduped_x:
        hkey = it.get("handle") or it["author"]
        st = handle_stats.setdefault(hkey, {
            "handle": hkey, "name": it["author"], "total": 0, "positive": 0, "negative": 0,
            "neutral": 0, "likes": 0, "retweets": 0, "last_ts": it["ts"],
            "_dept": {}, "_dist": {}, "_tags": {}, "worst": None})
        st["total"] += 1
        st[it["sentiment"]] += 1
        st["likes"] += it.get("likes", 0)
        st["retweets"] += it.get("retweets", 0)
        st["last_ts"] = max(st["last_ts"], it["ts"])
        st["_dept"][it["department"]] = st["_dept"].get(it["department"], 0) + 1
        if it["district"] != "Statewide":
            st["_dist"][it["district"]] = st["_dist"].get(it["district"], 0) + 1
        for tg in {t.lower() for t in tag_rx.findall(it["text"])}:
            st["_tags"][tg] = st["_tags"].get(tg, 0) + 1
        if it["sentiment"] == "negative":
            eng = it.get("likes", 0) + it.get("retweets", 0)
            if st["worst"] is None or eng >= st["worst"]["eng"]:
                st["worst"] = {"text": it["text"][:160], "url": it["url"], "eng": eng}

    def _top(d: Dict[str, int], n: int):
        return [{"name": k, "count": v} for k, v in sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:n]]

    handles_list = []
    for st in handle_stats.values():
        t = max(st["total"], 1)
        st["pos_pct"] = round(st["positive"] / t * 100, 1)
        st["neg_pct"] = round(st["negative"] / t * 100, 1)
        st["neu_pct"] = round(st["neutral"] / t * 100, 1)
        dept = st.pop("_dept")
        dept = {k: v for k, v in dept.items() if k != "General / Admin"} or dept
        st["topics"] = _top(dept, 3)
        st["districts"] = _top(st.pop("_dist"), 2)
        st["hashtags"] = _top(st.pop("_tags"), 3)
        handles_list.append(st)
    handles_list.sort(key=lambda h: (h["total"], h["negative"]), reverse=True)
    handles_list = handles_list[:200]
    total = max(len(deduped), 1)
    pct = lambda n: round(n / total * 100, 1)  # noqa: E731
    sources = [{"name": n, "ok": h["ok"], "age_s": int(now_ts - h["ts"]), "count": h["count"],
                "error": h["error"], "last_ok_s": int(now_ts - h["last_ok"]) if h["last_ok"] else None}
               for n, h in sorted(SOURCE_HEALTH.items())]

    return {
        "server_time": now_ts,
        "updated_at": now_ist.strftime("%d %b %Y, %I:%M:%S %p IST"),
        "mode": "archive" if is_archive else "live",
        "active_date": active_date,
        "today_date": today_str,
        "x_status": x_status,
        "x_error": x_error,
        "count": len(deduped),
        "stats": {
            "total": len(deduped), "x_count": by_src.get("X", 0), "x_today_count": x_today_count,
            "news_count": by_src.get("NEWS", 0) + by_src.get("NEWSPAPER", 0),
            "yt_count": by_src.get("YOUTUBE", 0), "fb_count": by_src.get("FACEBOOK", 0),
            "ig_count": by_src.get("INSTAGRAM", 0),
            "positive": cnt["positive"], "negative": cnt["negative"], "neutral": cnt["neutral"],
            "x_positive": xcnt["positive"], "x_negative": xcnt["negative"], "x_neutral": xcnt["neutral"],
            "positive_pct": pct(cnt["positive"]), "negative_pct": pct(cnt["negative"]),
            "neutral_pct": pct(cnt["neutral"]), "hindi_count": hi_total, "english_count": en_total,
            "crisis_score": crisis_score(deduped, now_ts, is_archive),
        },
        "hourly_chart": {"labels": [f"{h:02d}:00" for h in hours], "positive": [h_pos[h] for h in hours],
                         "negative": [h_neg[h] for h in hours], "neutral": [h_neu[h] for h in hours]},
        "hourly_chart_x": {"labels": [f"{h:02d}:00" for h in hours], "positive": [hx_pos[h] for h in hours],
                           "negative": [hx_neg[h] for h in hours], "neutral": [hx_neu[h] for h in hours]},
        "districts": sorted([{"name": k, **v} for k, v in district_stats.items()],
                            key=lambda x: (x["negative"], x["total"]), reverse=True)[:10],
        "departments": sorted([{"name": k, **v} for k, v in dept_stats.items()],
                              key=lambda x: (x["negative"], x["total"]), reverse=True),
        "accounts": accounts_list,
                "handles": handles_list,
        "sources": sources,
        "live_tv": live_tv,
        "items": deduped,
    }
