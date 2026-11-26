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
    "Jaipur": ["jaipur",
