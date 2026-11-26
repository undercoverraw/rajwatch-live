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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
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
STATE_KEY = "ciuwatch:state:v4"
STATE_FILE = Path(tempfile.gettempdir()) / "ciuwatch_state_v4.json"
BG_COLLECT_INTERVAL = max(45, int(os.getenv("BG_COLLECT_SECONDS", "60")))


def parse_feed_env(name: str) -> List[Tuple[str, str]]:
    out = []
    for part in os.getenv(name, "").split(";"):
        if "|" in part:
            n, u = [x.strip() for x in part.split("|", 1)]
            if n and u.startswith("http"):
                out.append((n, u))
    return out


YOUTUBE_CHANNELS = [
    ("First India News", "UC_cZYFovNQTsKw1ZLmO_AMA"),
    ("Zee Rajasthan", "UCfnvGSUXKIuaCv-9E9a2UJg"),
    ("News18 Rajasthan", "UC7FlLbNo66YsCEAPuJITiNg"),
]


def _gn_site(domain: str) -> str:
    return f"https://news.google.com/rss/search?q={urllib.parse.quote(f'site:{domain} राजस्थान when:1d')}&hl=hi&gl=IN&ceid=IN:hi"


NEWSPAPER_FEEDS = [
    ("Dainik Bhaskar", _gn_site("bhaskar.com")),
    ("Rajasthan Patrika", _gn_site("patrika.com")),
    ("Zee News Rajasthan", _gn_site("zeenews.india.com")),
] + parse_feed_env("PAPER_FEEDS")
EXTRA_FEEDS = parse_feed_env("EXTRA_RSS")

# ============================================================================
# RAJASTHAN PROMINENT LEADERS & CMO REGISTRY (Tracked Always, Despite Filter!)
# ============================================================================
LEADERS_CONFIG = [
    {"id": "cmo", "name": "CMO Rajasthan", "role": "CMO / Govt", "party": "GOVT", "handle": "@RajCMO",
     "terms": ["rajcmo", "cmo rajasthan", "cmo", "राजस्थान सरकार", "मुख्यमंत्री कार्यालय", "rajasthan govt", "bhajanlal govt", "भजनलाल सरकार"]},
    {"id": "bhajanlal", "name": "Bhajanlal Sharma", "role": "Chief Minister", "party": "BJP", "handle": "@BhajanlalBjp",
     "terms": ["bhajanlal", "bhajan lal", "भजनलाल", "भजन लाल", "cm bhajanlal", "सीएम भजनलाल", "मुख्यमंत्री भजनलाल", "bhajanlalbjp"]},
    {"id": "gehlot", "name": "Ashok Gehlot", "role": "Former CM", "party": "INC", "handle": "@ashokgehlot51",
     "terms": ["ashok gehlot", "gehlot", "अशोक गहलोत", "गहलोत", "ashokgehlot51"]},
    {"id": "pilot", "name": "Sachin Pilot", "role": "Senior INC Leader", "party": "INC", "handle": "@SachinPilot",
     "terms": ["sachin pilot", "सचिन पायलट", "sachinpilot", "पायलट"]},
    {"id": "dotasra", "name": "Govind Singh Dotasra", "role": "PCC Chief", "party": "INC", "handle": "@GovindDotasra",
     "terms": ["dotasra", "govind singh dotasra", "डोटासरा", "गोविंद सिंह डोटासरा", "govinddotasra"]},
    {"id": "diya", "name": "Diya Kumari", "role": "Deputy CM", "party": "BJP", "handle": "@KumariDiya",
     "terms": ["diya kumari", "दिया कुमारी", "दीया कुमारी", "kumaridiya"]},
    {"id": "bairwa", "name": "Prem Chand Bairwa", "role": "Deputy CM", "party": "BJP", "handle": "@DrPremBairwa",
     "terms": ["prem chand bairwa", "premchand bairwa", "bairwa", "प्रेमचंद बैरवा", "प्रेम चंद बैरवा", "drprembairwa"]},
    {"id": "vasundhara", "name": "Vasundhara Raje", "role": "Former CM", "party": "BJP", "handle": "@VasundharaBJP",
     "terms": ["vasundhara raje", "vasundhara", "वसुंधरा राजे", "वसुंधरा", "vasundharabjp"]},
    {"id": "kirodi", "name": "Kirodi Lal Meena", "role": "Cabinet Minister", "party": "BJP", "handle": "@DrKirodilalBJP",
     "terms": ["kirodi lal", "kirodilal", "kirodi", "किरोड़ी लाल", "किरोड़ीलाल", "किरोड़ी", "drkirodilalbjp"]},
    {"id": "jully", "name": "Tikaram Jully", "role": "Leader of Opposition", "party": "INC", "handle": "@TikaRamJullyINC",
     "terms": ["tikaram jully", "tika ram jully", "jully", "टीकाराम जूली", "जूली", "tikaramjullyinc"]},
    {"id": "beniwal", "name": "Hanuman Beniwal", "role": "RLP Chief / MP", "party": "RLP", "handle": "@hanumanbeniwal",
     "terms": ["hanuman beniwal", "beniwal", "हनुमान बेनीवाल", "बेनीवाल", "hanumanbeniwal"]},
    {"id": "dilawar", "name": "Madan Dilawar", "role": "Education Minister", "party": "BJP", "handle": "@madandilawar",
     "terms": ["madan dilawar", "dilawar", "मदन दिलावर", "दिलावर", "madandilawar"]},
    {"id": "rathore", "name": "Madan Rathore", "role": "BJP State President", "party": "BJP", "handle": "@madanrrathore",
     "terms": ["madan rathore", "मदन राठौड़", "madanrrathore"]},
    {"id": "shekhawat", "name": "Gajendra Singh Shekhawat", "role": "Union Minister", "party": "BJP", "handle": "@gssjodhpur",
     "terms": ["gajendra singh shekhawat", "shekhawat", "गजेंद्र सिंह शेखावत", "gssjodhpur"]},
    {"id": "roat", "name": "Rajkumar Roat", "role": "BAP Leader / MP", "party": "BAP", "handle": "@roat_mla",
     "terms": ["rajkumar roat", "राजकुमार रोत", "roat_mla"]},
]
LEADER_X_QUERY_TERMS = [
    "RajCMO", "Bhajanlal", '"Ashok Gehlot"', '"Sachin Pilot"', "Dotasra",
    '"Diya Kumari"', '"Vasundhara Raje"', '"Kirodi Lal"', '"Tikaram Jully"', '"Hanuman Beniwal"',
]

# ============================================================================
# BJP / RAJASTHAN GOVT PERSPECTIVE SENTIMENT ENGINE
# ============================================================================
GOV_BJP_TERMS = [
    "bhajanlal", "bhajan lal", "bhajanlalbjp", "cmo", "rajcmo", "cm", "chief minister",
    "govt", "government", "bjp", "bjp4rajasthan", "bjp4india", "modi", "narendra modi",
    "pm modi", "minister", "diya kumari", "kumaridiya", "bairwa", "prem chand bairwa",
    "vasundhara", "kirodi", "kirodilal", "dilawar", "madan rathore", "shekhawat",
    "double engine", "state govt", "rajasthan govt", "raj govt", "cabinet", "administration",
    "police", "jda", "rpsc", "rssb", "phed", "pwd", "discom", "collector", "sarkar",
    "भजनलाल", "भजन लाल", "मुख्यमंत्री", "सीएम", "सरकार", "राजस्थान सरकार", "भाजपा", "बीजेपी",
    "मोदी", "प्रधानमंत्री", "मंत्री", "मंत्रिमंडल", "प्रशासन", "दिया कुमारी", "दीया कुमारी",
    "बैरवा", "वसुंधरा", "किरोड़ी", "दिलावर", "मदन राठौड़", "शेखावत", "डबल इंजन", "शासन", "सुशासन",
    "पुलिस", "कलेक्टर", "विभाग", "बजट", "योजना",
]

OPP_TERMS = [
    "congress", "inc", "incrajasthan", "incindia", "rahul gandhi", "priyanka gandhi", "kharge",
    "gehlot", "ashok gehlot", "ashokgehlot51", "dotasra", "govinddotasra", "sachin pilot",
    "sachinpilot", "tikaram jully", "tikaramjullyinc", "jully", "opposition", "beniwal",
    "hanumanbeniwal", "rlp", "bap", "rajkumar roat", "roat_mla", "nsui", "iyc", "youth congress",
    "कांग्रेस", "गहलोत", "अशोक गहलोत", "डोटासरा", "गोविंद सिंह डोटासरा", "सचिन पायलट", "पायलट",
    "टीकाराम जूली", "जूली", "विपक्ष", "नेता प्रतिपक्ष", "बेनीवाल", "हनुमान बेनीवाल", "रालोपा",
    "राहुल गांधी", "प्रियंका गांधी", "खड़गे", "राजकुमार रोत", "बीएपी", "एनएसयूआई", "यूथ कांग्रेस",
]

OPP_HANDLE_HINTS = [
    "inc", "congress", "gehlot", "pilot", "dotasra", "jully", "beniwal", "rlp", "roat",
    "nsui", "iyc", "youthcongress", "sewadal", "mahilacongress", "rahulgandhi", "priyankagandhi",
]
BJP_GOV_HANDLE_HINTS = [
    "bjp", "rajcmo", "bhajanlal", "dipr", "kumaridiya", "prembairwa", "kirodilal",
    "madandilawar", "madanrrathore", "vasundhara", "gssjodhpur", "bjym", "narendramodi",
]

STRONG_NEG = [
    "scam", "paper leak", "leak", "protest", "dharna", "ghotala", "bhrashtachar", "lathicharge",
    "corruption", "bribe", "allegation", "fraud", "boycott", "resign", "negligence", "mismanagement",
    "outrage", "shortage", "power cut", "water crisis", "crisis", "unemployment", "inflation",
    "failure", "failed", "jumla", "barbad", "nikamma", "shameful", "disgrace", "collapse",
    "irregularities", "chaos", "furious", "condemn", "incompetent", "worst", "jungle raj", "blackout", "strike",
    "पेपर लीक", "लीक", "धरना", "विरोध", "घोटाला", "भ्रष्टाचार", "लाठीचार्ज", "हंगामा", "नाराज", "नाराजगी",
    "मुर्दाबाद", "प्रदर्शन", "आक्रोश", "गुस्सा", "हड़ताल", "चक्का जाम", "किल्लत", "संकट", "कटौती", "बिजली कटौती",
    "पानी संकट", "लापरवाही", "धांधली", "फर्जीवाड़ा", "ठगी", "रिश्वत", "बेरोजगारी", "महंगाई", "इस्तीफा", "घेराव",
    "शर्मनाक", "बदहाल", "खस्ताहाल", "नाकाम", "नाकामी", "विफल", "विफलता", "निंदा", "आरोप", "जंगलराज",
    "अन्याय", "बर्बाद", "निकम्मा", "खोखले दावे", "फेल", "बहिष्कार", "लचर व्यवस्था", "जुमला", "धोखा", "छलावा",
    "खराब", "बर्बादी", "ठप", "ध्वस्त", "गुंडाराज", "तानाशाही",
]
MOD_NEG = [
    "delay", "delayed", "pending", "complaint", "problem", "trouble", "unsafe", "dispute", "controversy",
    "clash", "violence", "crime", "murder", "rape", "robbery", "traffic jam", "encroachment", "potholes",
    "देरी", "शिकायत", "समस्या", "परेशानी", "विवाद", "बवाल", "झड़प", "अपराध", "हत्या", "लूट", "चोरी",
    "गड्ढे", "जाम", "अतिक्रमण", "गंदगी", "असुविधा", "खराब हालत", "चिंता", "सवाल उठे", "नाराज़", "कमी",
]
INCIDENT_NEU = ["accident", "fire", "road accident", "died", "injured", "हादसा", "सड़क हादसा", "आग", "घायल", "मौत"]
STRONG_POS = [
    "inaugurate", "inaugurated", "launch", "launched", "approved", "approval", "historic", "milestone",
    "record", "award", "awarded", "praise", "praised", "kudos", "congratulations", "saugat", "tohfa", "vikas",
    "relief", "bonus", "subsidy", "grant", "welfare", "free", "investment", "mou", "jobs", "recruitment",
    "vacancy", "boost", "reforms", "thank you", "grateful", "proud", "excellent", "great work", "commendable",
    "successful", "success", "achievement", "progress", "empowerment",
    "उद्घाटन", "लोकार्पण", "शिलान्यास", "सौगात", "तोहफा", "बड़ी सौगात", "विकास", "बधाई", "सफल", "सफलता",
    "बड़ी सफलता", "राहत", "बड़ी राहत", "मंजूरी", "निवेश", "रोजगार", "भर्ती", "नौकरी", "पुरस्कार", "सम्मान",
    "तारीफ", "सराहना", "सराहनीय", "ऐतिहासिक", "रिकॉर्ड", "लाभ", "फायदा", "मुफ्त", "सब्सिडी", "मुआवजा",
    "खुशखबरी", "प्रगति", "कल्याण", "समझौता", "एमओयू", "उपलब्धि", "सहायता", "स्वागत", "धन्यवाद", "आभार",
    "जिंदाबाद", "शानदार", "उत्कृष्ट", "सुशासन", "जनहित", "बढ़ोतरी", "मजबूत", "जनसेवा", "बेहतरीन",
]
MOD_POS = [
    "scheme", "yojana", "budget", "initiative", "support", "upgrade", "digital", "clean", "green",
    "highway", "metro", "hospital", "school", "compensation", "distribution", "benefit", "growth",
    "योजना", "बजट", "पहल", "समर्थन", "डिजिटल", "स्वच्छ", "हाईवे", "मेट्रो", "वितरण", "लाभार्थी", "सुधार",
]

ACTION_POSITIVE_PHRASES = [
    r"(?:भ्रष्टाचार|घोटाल[ेा]|पेपर\s*लीक|नकल|माफिया|अपराध|अपराधियों|अतिक्रमण|लापरवाही|रिश्वत|मिलावट|गैंगस्टर|दोषियों).{0,35}(?:कार्रवाई|सख्त|गिरफ्तार|गिरफ्तारी|पकड़|पकड़ा|ध्वस्त|रोक|लगाम|बर्खास्त|सस्पेंड|निलंबित|ट्रैप|एक्शन|मुक्ति|खत्म|बुलडोजर|कसा\s*शिकंजा)",
    r"(?:सख्त\s*कार्रवाई|बड़ी\s*कार्रवाई|त्वरित\s*कार्रवाई|जीरो\s*टॉलरेंस|zero\s*tolerance|strict\s*action|crackdown|busted|arrested\s*(?:accused|gangster|mafia|culprit|corrupt)|action\s*against\s*(?:corruption|mafia|leak|crime|negligence)|acb\s*trap)",
    r"(?:समस्या|संकट|किल्लत).{0,25}(?:समाधान|निस्तारण|दूर\s*किया|हल|राहत)",
    r"(?:लापरवाही|भ्रष्टाचार).{0,20}(?:बर्दाश्त\s*नहीं|नहीं\s*चलेगी)",
]
SARCASM_NEG_PHRASES = [
    r"(?:कहां|कहाँ)\s*(?:है|गया|गई)\s*(?:विकास|सुशासन|राहत|रोजगार|भर्ती|सरकार|प्रशासन)",
    r"(?:क्या\s*यही\s*है|यही\s*है\s*क्या)\s*(?:सुशासन|विकास|रामराज्य|डबल\s*इंजन|सरकार)",
    r"(?:कब\s*मिलेगी|कब\s*आएगी|कब\s*जागेगी)\s*(?:राहत|भर्ती|नौकरी|सरकार|नींद)",
    r"(?:डबल\s*इंजन).{0,25}(?:फेल|नाकाम|धुआं|ठप|खोखला|बदहाल)",
    r"(?:सिर्फ\s*कागजों|कागजों\s*में|हवा\s*हवाई|खोखले\s*दावे|जवाब\s*दो|शर्म\s*करो|इस्तीफा\s*दो|हाय\s*हाय)",
    r"(?:where\s*is\s*(?:development|governance|relief|jobs)|failed\s*(?:govt|government|cm)|wake\s*up\s*(?:govt|cm|rajcmo)|total\s*failure|hollow\s*promises)",
]
ROUTINE_NEU_PHRASES = [
    r"(?:समीक्षा\s*बैठक|बैठक\s*लेंगे|बैठक\s*की\s*अध्यक्षता|दौरे\s*पर\s*रहेंगे|तबादला\s*सूची|तबादले|मौसम\s*विभाग|तापमान|कार्यक्रम\s*प्रस्तावित|शेड्यूल|आज\s*दोपहर|आज\s*शाम|जन्मदिन\s*की\s*हार्दिक|शुभकामनाएं|जयंती\s*पर\s*नमन|श्रद्धांजलि)",
    r"(?:review\s*meeting|to\s*chair\s*meeting|will\s*visit|transfer\s*list|weather\s*update|scheduled\s*to|happy\s*birthday|tribute\s*to|condolences)",
]

ATTACK = [
    "slams", "targets", "hits out", "questions", "blasts", "accuses", "corners", "criticizes", "attacks",
    "निशाना", "तंज", "आड़े हाथ", "कटघरे में", "हमला बोला", "सवाल उठाए", "घेरा", "बरसे", "पलटवार", "कटाक्ष", "भड़के", "कोसा",
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
    "Politics & CMO": ["cm", "bhajanlal", "cmo", "rajcmo", "congress", "bjp", "minister", "mla", "mp", "election",
                       "zila pramukh", "jila pramukh", "panchayat", "vidhan sabha", "भजनलाल", "मुख्यमंत्री", "कांग्रेस",
                       "भाजपा", "मंत्री", "विधायक", "चुनाव", "जिला प्रमुख", "विधानसभा", "सरकार", "मंत्रिमंडल", "पंचायत"],
    "Education & RPSC": ["rpsc", "reet", "exam", "paper", "school", "college", "university", "teacher", "student", "rssb",
                         "परीक्षा", "पेपर", "स्कूल", "शिक्षक", "छात्र", "भर्ती"],
    "Police & Crime": ["police", "acb", "crime", "arrest", "fir", "murder", "cyber", "gangster",
                       "पुलिस", "एसीबी", "अपराध", "गिरफ्तार", "हत्या", "थाना", "गैंगस्टर"],
    "Water & Energy": ["water", "phed", "electricity", "power", "discom", "solar", "ercp",
                       "पानी", "पेयजल", "बिजली", "ऊर्जा", "सोलर", "नहर", "डिस्कॉम"],
    "Health & Medical": ["hospital", "doctor", "health", "medical", "sms hospital", "disease",
                         "अस्पताल", "डॉक्टर", "स्वास्थ्य", "चिकित्सा", "मरीज"],
    "Roads & Urban (PWD/JDA)": ["road", "highway", "pwd", "jda", "metro", "traffic", "municipal", "nagar nigam",
                                "सड़क", "हाईवे", "मेट्रो", "नगर निगम", "अतिक्रमण", "ट्रैफिक"],
    "Farmers & Rural": ["farmer", "kisan", "crop", "mandi", "msp", "agriculture",
                        "किसान", "फसल", "मंडी", "कृषि", "खाद", "यूरिया"],
}

DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_WCH = r"\w\u0900-\u097F"
_NOT_PREV, _NOT_WORD = rf"(?<![{_WCH}])", rf"(?![{_WCH}])"


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


STRONG_NEG_RX, MOD_NEG_RX, INCIDENT_RX = compile_terms(STRONG_NEG), compile_terms(MOD_NEG), compile_terms(INCIDENT_NEU)
STRONG_POS_RX, MOD_POS_RX = compile_terms(STRONG_POS), compile_terms(MOD_POS)
ATTACK_RX = compile_terms(ATTACK)
GOV_RX, OPP_RX = compile_terms(GOV_BJP_TERMS, full=True), compile_terms(OPP_TERMS, full=True)
ACTION_POS_RX = [re.compile(p, re.I) for p in ACTION_POSITIVE_PHRASES]
SARCASM_NEG_RX = [re.compile(p, re.I) for p in SARCASM_NEG_PHRASES]
ROUTINE_NEU_RX = [re.compile(p, re.I) for p in ROUTINE_NEU_PHRASES]
DISTRICT_RX = {d: compile_terms(t, full=True) for d, t in DISTRICTS.items()}
DEPT_RX = {d: compile_terms(t, full=True) for d, t in DEPARTMENTS.items()}
LEADER_RX = {ld["id"]: compile_terms(ld["terms"], full=True) for ld in LEADERS_CONFIG}

# ============================================================================
# SELF-LEARNING LEXICON + ANTI-WRONG-SUGGESTION GUARDRAIL + AUTO-MAINTENANCE
# ============================================================================
STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "will", "been", "are", "was", "were",
    "not", "but", "you", "your", "his", "her", "our", "their", "has", "had", "into", "over", "after",
    "about", "today", "news", "live", "update", "rajasthan", "jaipur", "india", "hindi", "breaking",
    "है", "हैं", "था", "थी", "थे", "को", "का", "की", "के", "में", "पर", "से", "ने", "और", "या", "भी",
    "तो", "ही", "कर", "किया", "हुआ", "हुई", "हुए", "हो", "गया", "गई", "गए", "रहा", "रही", "रहे",
    "इस", "उस", "इन", "उन", "यह", "वह", "एक", "दो", "लिए", "साथ", "बाद", "आज", "कल", "अब",
    "राजस्थान", "जयपुर", "खबर", "न्यूज़", "लाइव", "अपडेट", "बड़ी", "ताजा",
}
LEARNED_LEXICON: Dict[str, Dict[str, Any]] = {}
ITEM_OVERRIDES: Dict[str, Dict[str, Any]] = {}
FEEDBACK_LOG: List[Dict[str, Any]] = []
LAST_SELF_TUNE_TS: float = 0.0


def extract_learning_tokens(text: str) -> List[str]:
    cleaned = re.sub(r"https?://\S+|[@#]\w+", " ", text or "")
    raw_words = re.findall(r"[a-zA-Z]{3,}|[\u0900-\u097F]{2,}", cleaned.lower())
    words = [w for w in raw_words if w not in STOPWORDS and len(w) <= 24]
    tokens: List[str] = []
    for w in words:
        if len(w) >= 3 and w not in tokens:
            tokens.append(w)
    for i in range(len(words) - 1):
        bg = f"{words[i]} {words[i + 1]}"
        if bg not in tokens:
            tokens.append(bg)
    return tokens[:24]


def compute_learned_delta(text: str) -> Tuple[float, float, List[str]]:
    if not LEARNED_LEXICON:
        return 0.0, 0.0, []
    pol_delta, neu_pull, hits = 0.0, 0.0, []
    for tok in extract_learning_tokens(text):
        rec = LEARNED_LEXICON.get(tok)
        if not rec or int(rec.get("samples", 0)) < 1:
            continue
        p, n, u = float(rec.get("positive", 0.0)), float(rec.get("negative", 0.0)), float(rec.get("neutral", 0.0))
        tot = p + n + u
        if tot < 0.8:
            continue
        rel = min(1.0, int(rec["samples"]) / 3.0)
        spec = 1.15 if " " in tok else 0.75
        if u > max(p, n) and (u / tot) >= 0.55:
            neu_pull += 0.65 * rel * spec
            hits.append(f"learned:{tok}(neu)")
        elif abs(p - n) / tot >= 0.45:
            direction = (p - n) / tot
            pol_delta += direction * 1.15 * rel * spec
            hits.append(f"learned:{tok}({'+' if direction > 0 else '-'})")
    return max(-2.4, min(2.4, pol_delta)), min(1.8, neu_pull), hits[:3]


def _author_camp(author: str, handle: str) -> str:
    ah = f"{author or ''} {handle or ''}".lower()
    if any(h in ah for h in BJP_GOV_HANDLE_HINTS):
        return "BJP_GOVT"
    if any(h in ah for h in OPP_HANDLE_HINTS):
        return "OPPOSITION"
    return "OTHER"


def analyze_sentiment_base(text: str, author: str = "", handle: str = "") -> Dict[str, Any]:
    raw, low = text or "", (text or "").lower()
    reasons: List[str] = []

    camp = _author_camp(author, handle)
    gm = GOV_RX.search(low)
    om = OPP_RX.search(low)
    has_gov = bool(gm) or (camp == "BJP_GOVT")
    has_opp = bool(om) or (camp == "OPPOSITION")

    s_neg, m_neg, inc = STRONG_NEG_RX.findall(low), MOD_NEG_RX.findall(low), INCIDENT_RX.findall(low)
    s_pos, m_pos = STRONG_POS_RX.findall(low), MOD_POS_RX.findall(low)

    neg_score = len(set(s_neg)) * 2.2 + len(set(m_neg)) * 1.0
    pos_score = len(set(s_pos)) * 2.0 + len(set(m_pos)) * 0.9

    # RULE A: Opposition self-appreciation / non-BJP praise -> NEUTRAL
    if camp == "OPPOSITION":
        if not gm and neg_score < 2.0 and not ATTACK_RX.search(low):
            return {
                "label": "neutral", "risk": "INFO", "net": 0.0, "confidence": 91,
                "reasons": ["opp-self-post->neutral"], "camp": camp, "opp_self_praise": True,
            }
        if (gm or ATTACK_RX.search(low) or neg_score >= 1.8) and not any(rx.search(low) for rx in ACTION_POS_RX):
            neg_score += 2.2
            pos_score *= 0.2
            reasons.append("opp-handle-critique")
        else:
            return {
                "label": "neutral", "risk": "INFO", "net": 0.0, "confidence": 86,
                "reasons": ["opp-handle->neutral"], "camp": camp, "opp_self_praise": True,
            }

    if has_opp and not has_gov:
        if ATTACK_RX.search(low) or any(rx.search(low) for rx in SARCASM_NEG_RX) or neg_score >= 2.2:
            neg_score += 1.6
            pos_score = 0.0
            reasons.append("opp-attack-system")
        else:
            return {
                "label": "neutral", "risk": "INFO", "net": 0.0, "confidence": 90,
                "reasons": ["opp-self-praise->neutral"], "camp": camp, "opp_self_praise": True,
            }

    if not has_gov and not has_opp:
        if pos_score > 0 and neg_score < 1.8:
            return {
                "label": "neutral", "risk": "INFO", "net": 0.0, "confidence": 82,
                "reasons": ["non-govt-praise->neutral"], "camp": camp, "opp_self_praise": False,
            }

    # RULE B: Incidents, Govt Action Reversal, Sarcasm, Political Attack Direction
    if inc:
        if has_gov or s_neg:
            neg_score += len(set(inc)) * 1.1
            reasons.append("incident+admin")
        else:
            neg_score += 0.35

    if has_gov and any(rx.search(low) for rx in ACTION_POS_RX):
        neg_score = max(0.0, neg_score - 3.8)
        pos_score += 3.0
        reasons.append("govt-action-reversal")

    if any(rx.search(low) for rx in SARCASM_NEG_RX):
        pos_score = max(0.0, pos_score - 2.8)
        neg_score += 3.6
        reasons.append("sarcasm/rhetoric")

    if ATTACK_RX.search(low):
        if gm and om:
            if gm.start() < om.start():
                pos_score += 2.4
                neg_score = max(0.0, neg_score - 2.6)
                reasons.append("bjp->opp-attack")
            else:
                neg_score += 3.0
                pos_score *= 0.2
                reasons.append("opp->bjp-attack")
        elif om and not gm:
            neg_score += 1.8
            reasons.append("opp-criticism")
        elif gm:
            neg_score += 1.5
            reasons.append("criticism-on-govt")

    if "?" in raw and has_gov and pos_score < 2.5:
        neg_score += 1.0
        reasons.append("questioning-govt")

    if any(rx.search(low) for rx in ROUTINE_NEU_RX) and neg_score < 2.0 and pos_score < 3.0:
        neg_score *= 0.4
        pos_score *= 0.35
        reasons.append("routine/greeting")

    net = pos_score - neg_score
    if net >= 1.6 and not has_gov:
        net = 0.0
        reasons.append("no-bjp-link->neutral")

    if s_neg:
        reasons.append("neg:" + ",".join(list(dict.fromkeys(s_neg))[:2]))
    if s_pos and has_gov:
        reasons.append("pos:" + ",".join(list(dict.fromkeys(s_pos))[:2]))

    if net <= -1.5:
        label, risk = "negative", "ALERT"
    elif net >= 1.6 and has_gov:
        label, risk = "positive", "POSITIVE"
    else:
        label, risk = "neutral", "INFO"

    if label == "neutral":
        conf = int(max(48, min(92, 85 - abs(net) * 18)))
    else:
        conf = int(max(55, min(97, 60 + (abs(net) - 1.5) * 11 - min(pos_score, neg_score) * 4)))

    return {
        "label": label, "risk": risk, "net": round(net, 2), "confidence": conf,
        "reasons": reasons[:4], "camp": camp, "opp_self_praise": False,
    }


def analyze_sentiment_full(text: str, item_id: str = "", author: str = "", handle: str = "") -> Dict[str, Any]:
    base = analyze_sentiment_base(text, author=author, handle=handle)

    if item_id and item_id in ITEM_OVERRIDES:
        ov = ITEM_OVERRIDES[item_id]
        lbl = ov["sentiment"]
        risk = "ALERT" if lbl == "negative" else ("POSITIVE" if lbl == "positive" else "INFO")
        return {
            "sentiment": lbl, "risk": risk, "confidence": 65 if ov.get("flagged") else 99,
            "reason": "User override (⚠ conflict)" if ov.get("flagged") else "User trained",
            "user_corrected": True, "flagged_conflict": bool(ov.get("flagged")), "base": base,
        }

    if base.get("opp_self_praise"):
        return {
            "sentiment": "neutral", "risk": "INFO", "confidence": base["confidence"],
            "reason": " · ".join(base["reasons"][:2]), "user_corrected": False, "flagged_conflict": False, "base": base,
        }

    pol_delta, neu_pull, learned_hits = compute_learned_delta(text)
    net = base["net"] + pol_delta
    has_gov = bool(GOV_RX.search((text or "").lower())) or (base.get("camp") == "BJP_GOVT")

    if neu_pull >= 0.9 and abs(net) < 2.6:
        label, risk = "neutral", "INFO"
    elif net <= -1.5:
        label, risk = "negative", "ALERT"
    elif net >= 1.6 and has_gov:
        label, risk = "positive", "POSITIVE"
    else:
        label, risk = "neutral", "INFO"

    reasons = list(base["reasons"]) + learned_hits
    return {
        "sentiment": label, "risk": risk, "confidence": base["confidence"],
        "reason": " · ".join(reasons[:3]) if reasons else "factual/neutral",
        "user_corrected": False, "flagged_conflict": False, "base": base,
    }


def detect_leaders(text: str, author: str = "", handle: str = "") -> List[str]:
    hay = f"{text or ''} {author or ''} {handle or ''}".lower()
    return [lid for lid, rx in LEADER_RX.items() if rx.search(hay)]


def _best(rx_map: Dict[str, "re.Pattern"], text: str, default: str) -> str:
    low, best, best_n = (text or "").lower(), None, 0
    for name, rx in rx_map.items():
        n = len(rx.findall(low))
        if n > best_n:
            best, best_n = name, n
    return best or default


def detect_district(text: str) -> str:
    return _best(DISTRICT_RX, text, "Statewide")


def detect_department(text: str) -> str:
    return _best(DEPT_RX, text, "General / Admin")


def clean_text(raw: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(raw or ""))).strip()


def detect_language(text: str) -> str:
    return "Hindi" if DEVANAGARI.search(text or "") else "English"


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
        return min((val if val.tzinfo else val.replace(tzinfo=timezone.utc)).timestamp(), now_ts)
    s = str(val).strip()
    if not s:
        return None
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return min((dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp(), now_ts)
        except Exception:
            continue
    try:
        dt = email.utils.parsedate_to_datetime(s)
        return min((dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp(), now_ts)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return min((dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp(), now_ts)
    except Exception:
        return None


def enrich_item(item: Dict[str, Any]) -> Dict[str, Any]:
    text = clean_text(item.get("text", ""))
    res = analyze_sentiment_full(text, item_id=item.get("id", ""), author=item.get("author", ""), handle=item.get("handle", ""))
    dt_ist = datetime.fromtimestamp(float(item["ts"]), tz=IST)
    item.update({
        "text": text, "sentiment": res["sentiment"], "risk": res["risk"],
        "confidence": res["confidence"], "sentiment_reason": res["reason"],
        "user_corrected": res["user_corrected"], "flagged_conflict": res["flagged_conflict"],
        "leaders": detect_leaders(text, item.get("author", ""), item.get("handle", "")),
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
        "ingested_at": time.time(), "likes": int(likes or 0), "retweets": int(retweets or 0),
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
    return None if dt.strftime("%Y-%m-%d") > today_str else (dt.strftime("%Y-%m-%d"), (dt + timedelta(days=1)).strftime("%Y-%m-%d"))


def sanitize_keywords(raw: str) -> List[str]:
    out: List[str] = []
    for k in (raw or "").split(","):
        k = re.sub(r"\s+", " ", re.sub(r'["()]', " ", k)).strip().lstrip("-").strip()
        if k and len(k) <= 40 and ":" not in k and k.upper() not in ("OR", "AND", "NOT"):
            if k.lower() not in [x.lower() for x in out]:
                out.append(k)
    return out[:10]


# ---- State & Persistence ----
HTTP: Optional[httpx.AsyncClient] = None
LIVE_TWEET_STORE: Dict[str, Dict[str, Any]] = {}
LIVE_MEDIA_STORE: Dict[str, Dict[str, Any]] = {}
MANUAL_STORE: Dict[str, Dict[str, Any]] = {}
DAILY_TRACKER: Dict[str, Any] = {"date": "", "items": {}}
ACTIVE_KEYWORDS: List[str] = list(DEFAULT_KEYWORDS)
CACHE: Dict[str, Dict[str, Any]] = {}
LOCKS: Dict[str, asyncio.Lock] = {}
BG_TASKS: set = set()
SOURCE_HEALTH: Dict[str, Dict[str, Any]] = {}
X_CLIENTS: Dict[int, Any] = {}
X_COOLDOWN: Dict[int, Tuple[float, bool]] = {}
cookie_turn, _dirty = 0, False


def mark_dirty():
    global _dirty
    _dirty = True


def health_ok(name: str, count: int):
    now = time.time()
    SOURCE_HEALTH[name] = {"ok": True, "ts": now, "last_ok": now, "count": count, "error": ""}


def health_fail(name: str, err: Any):
    prev = SOURCE_HEALTH.get(name, {})
    SOURCE_HEALTH[name] = {"ok": False, "ts": time.time(), "last_ok": prev.get("last_ok", 0), "count": 0, "error": (str(err) or repr(err))[:140]}


def record_media_items(items: List[Dict[str, Any]], now_ts: float):
    cutoff, changed = now_ts - 86400, False
    for it in items:
        if it.get("ts", 0) < cutoff:
            continue
        old = LIVE_MEDIA_STORE.get(it["id"])
        if old:
            it["ingested_at"] = old.get("ingested_at", it.get("ingested_at", now_ts))
        else:
            changed = True
        LIVE_MEDIA_STORE[it["id"]] = it
    for mid in [k for k, v in LIVE_MEDIA_STORE.items() if v.get("ts", 0) < cutoff]:
        LIVE_MEDIA_STORE.pop(mid, None)
    if len(LIVE_MEDIA_STORE) > 400:
        for mid in sorted(LIVE_MEDIA_STORE, key=lambda k: LIVE_MEDIA_STORE[k].get("ts", 0))[:len(LIVE_MEDIA_STORE) - 400]:
            LIVE_MEDIA_STORE.pop(mid, None)
    if changed:
        mark_dirty()


def record_daily_tracker(items: List[Dict[str, Any]], today_str: str):
    if DAILY_TRACKER["date"] != today_str:
        DAILY_TRACKER["date"], DAILY_TRACKER["items"] = today_str, {}
        mark_dirty()
    changed = False
    for it in items:
        if it.get("date_ist") == today_str:
            rec = {
                "hour": it["hour_ist"], "sentiment": it["sentiment"], "source": it["source"],
                "leaders": it.get("leaders", []), "district": it.get("district", "Statewide"),
                "department": it.get("department", "General / Admin"),
                "hay": f"{it.get('text', '')[:180]} {it.get('author', '')} {it.get('handle', '')}".lower(),
            }
            if DAILY_TRACKER["items"].get(it["id"]) != rec:
                DAILY_TRACKER["items"][it["id"]] = rec
                changed = True
    if len(DAILY_TRACKER["items"]) > 1800:
        for k in list(DAILY_TRACKER["items"])[:300]:
            DAILY_TRACKER["items"].pop(k, None)
    if changed:
        mark_dirty()


def reevaluate_all_stores():
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    updated = []
    for store in (LIVE_TWEET_STORE, LIVE_MEDIA_STORE, MANUAL_STORE):
        for item in store.values():
            enrich_item(item)
            updated.append(item)
    if updated:
        record_daily_tracker(updated, today_str)


def self_tune_lexicon():
    global LAST_SELF_TUNE_TS
    now = time.time()
    to_drop = []
    for tok, rec in LEARNED_LEXICON.items():
        p, n, u = float(rec.get("positive", 0)), float(rec.get("negative", 0)), float(rec.get("neutral", 0))
        tot = p + n + u
        rec["positive"], rec["negative"], rec["neutral"] = round(p * 0.96, 2), round(n * 0.96, 2), round(u * 0.96, 2)
        if tot >= 1.5 and max(p, n, u) / tot < 0.45 and int(rec.get("samples", 0)) < 4:
            to_drop.append(tok)
        elif (p + n + u) < 0.25:
            to_drop.append(tok)
    for tok in to_drop:
        LEARNED_LEXICON.pop(tok, None)
    LAST_SELF_TUNE_TS = now
    if to_drop:
        reevaluate_all_stores()
        mark_dirty()


async def _kv_get() -> Optional[str]:
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            r = await HTTP.post(UPSTASH_URL, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, json=["GET", STATE_KEY], timeout=8)
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
            await HTTP.post(UPSTASH_URL, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, json=["SET", STATE_KEY, val, "EX", 604800], timeout=8)
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
        LEARNED_LEXICON.update(data.get("learned") or {})
        ITEM_OVERRIDES.update(data.get("overrides") or {})
        if isinstance(data.get("feedback_log"), list):
            FEEDBACK_LOG[:] = data["feedback_log"][-100:]
        LIVE_TWEET_STORE.update(data.get("tweets") or {})
        LIVE_MEDIA_STORE.update(data.get("media") or {})
        MANUAL_STORE.update(data.get("manual") or {})
        d = data.get("daily") or {}
        DAILY_TRACKER["date"], DAILY_TRACKER["items"] = d.get("date", ""), d.get("items", {})
        if isinstance(data.get("keywords"), list) and data["keywords"]:
            ACTIVE_KEYWORDS[:] = data["keywords"][:10]
        reevaluate_all_stores()
    except Exception as e:
        log.warning("State load fail: %s", e)


async def save_state(force: bool = False):
    global _dirty
    if not _dirty and not force:
        return
    _dirty = False
    tweets, media = dict(LIVE_TWEET_STORE), dict(LIVE_MEDIA_STORE)
    while True:
        s = json.dumps({
            "tweets": tweets, "media": media, "manual": MANUAL_STORE, "daily": DAILY_TRACKER,
            "keywords": ACTIVE_KEYWORDS, "learned": LEARNED_LEXICON, "overrides": ITEM_OVERRIDES,
            "feedback_log": FEEDBACK_LOG[-100:],
        }, ensure_ascii=False)
        if len(s) < 900_000 or (not tweets and not media):
            break
        if len(media) > 150:
            for k in sorted(media, key=lambda x: media[x].get("ts", 0))[:80]:
                media.pop(k, None)
        elif tweets:
            for k in sorted(tweets, key=lambda x: tweets[x].get("ts", 0))[:100]:
                tweets.pop(k, None)
        else:
            break
    await _kv_set(s)


async def autosave_loop():
    ticks = 0
    while True:
        await asyncio.sleep(90)
        ticks += 1
        try:
            if ticks % 20 == 0:
                self_tune_lexicon()
            await save_state()
        except Exception as e:
            log.warning("autosave: %s", e)


async def background_collector_loop():
    await asyncio.sleep(8)
    while True:
        try:
            kw = list(ACTIVE_KEYWORDS) or list(DEFAULT_KEYWORDS)
            now_ist = datetime.now(IST)
            now_ts, today_str = now_ist.timestamp(), now_ist.strftime("%Y-%m-%d")
            x_res, x_ld, n_en, n_hi, papers, yt, extra = await asyncio.gather(
                get_x_live(kw, False), get_x_leaders_live(False),
                get_news(kw, "en", None, False), get_news(kw, "hi", None, False),
                get_papers(False), get_youtube(False), get_extra(False), return_exceptions=True,
            )
            collected: List[Dict[str, Any]] = []
            for xr in (x_res, x_ld):
                if isinstance(xr, tuple) and isinstance(xr[0], list):
                    collected.extend(xr[0])
            media_batch = [it for res in (n_en, n_hi, papers, yt, extra) if isinstance(res, list) for it in res]
            if media_batch:
                record_media_items(media_batch, now_ts)
                collected.extend(media_batch)
            if collected:
                record_daily_tracker(collected, today_str)
        except Exception as e:
            log.warning("bg collector: %s", e)
        await asyncio.sleep(BG_COLLECT_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global HTTP
    HTTP = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=True)
    await load_state()
    save_task = asyncio.create_task(autosave_loop())
    collect_task = asyncio.create_task(background_collector_loop())
    yield
    save_task.cancel()
    collect_task.cancel()
    await save_state(force=True)
    await HTTP.aclose()


app = FastAPI(title="CIU Watch", lifespan=lifespan)


async def require_key(x_access_key: str = Header(default=""), key: str = Query(default="")):
    if ACCESS_KEY and not secrets.compare_digest((x_access_key or key or "").encode(), ACCESS_KEY.encode()):
        await asyncio.sleep(1)
        raise HTTPException(status_code=401, detail="invalid key")


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


async def swr(key: str, ttl: float, factory: Callable[[], Awaitable[Any]], force: bool = False, min_gap: float = 10.0):
    ent, lock = CACHE.get(key), LOCKS.setdefault(key, asyncio.Lock())
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


# ---- X Fetchers ----
def get_cookie_pool():
    pool = []
    for s in ["", "_2", "_3"]:
        auth = os.getenv(f"X_AUTH_TOKEN{s}", "").strip().strip('"').strip("'")
        ct0 = os.getenv(f"X_CT0{s}", "").strip().strip('"').strip("'")
        if auth and ct0:
            pool.append({"auth_token": auth, "ct0": ct0})
    return pool


def build_x_query(kw: List[str], date_window: Optional[Tuple[str, str]], raw_terms: bool = False) -> str:
    joined = " OR ".join(kw) if raw_terms else " OR ".join(f'"{k}"' if " " in k else k for k in kw[:7])
    q = f"({joined}) -filter:retweets"
    return f"{q} since:{date_window[0]} until:{date_window[1]}" if date_window else q


def parse_tweets(tweets, kw: List[str]) -> List[Dict[str, Any]]:
    out, kws = [], [k.lower().strip('"') for k in kw[:10]]
    for t in tweets or []:
        text, tid = getattr(t, "text", "") or "", str(getattr(t, "id", "") or "")
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


async def x_search(kw: List[str], date_window: Optional[Tuple[str, str]], count: int, force: bool = False, raw_terms: bool = False):
    global cookie_turn
    pool = get_cookie_pool()
    if not pool:
        raise RuntimeError("no_cookies")
    q, n, last_err, now = build_x_query(kw, date_window, raw_terms=raw_terms), len(pool), None, time.time()
    for step in range(n):
        idx = (cookie_turn + step) % n
        cd_until, is_rl = X_COOLDOWN.get(idx, (0.0, False))
        if cd_until > now and (is_rl or not force):
            continue
        try:
            client = X_CLIENTS.get(idx)
            if client is None:
                client = Client("en-US")
                client.set_cookies(pool[idx])
                X_CLIENTS[idx] = client
            tweets = await asyncio.wait_for(client.search_tweet(q, "Latest", count=count), timeout=20)
            X_COOLDOWN.pop(idx, None)
            cookie_turn = idx + 1
            return parse_tweets(tweets, kw)
        except Exception as e:
            last_err = e
            X_CLIENTS.pop(idx, None)
            msg = (str(e) or repr(e)).lower()
            is_rl = "429" in msg or "rate" in msg
            X_COOLDOWN[idx] = (time.time() + (600 if is_rl else 45), is_rl)
            log.warning("X account %d fail: %s", idx, msg[:120])
    raise last_err or RuntimeError("Sab X accounts cooldown me hain")


def prune_live_tweet_store(now_ts: float):
    cutoff = now_ts - 86400
    for tid in [t for t, tw in LIVE_TWEET_STORE.items() if tw.get("ts", 0) < cutoff]:
        LIVE_TWEET_STORE.pop(tid, None)
    if len(LIVE_TWEET_STORE) > 700:
        for tid in sorted(LIVE_TWEET_STORE, key=lambda k: LIVE_TWEET_STORE[k].get("ts", 0))[:len(LIVE_TWEET_STORE) - 700]:
            LIVE_TWEET_STORE.pop(tid, None)


def stored_tweets_for(kw: List[str], now_ts: float, include_leaders: bool = True) -> List[Dict[str, Any]]:
    prune_live_tweet_store(now_ts)
    want = {k.lower() for k in kw[:7]}
    rows = [t for t in LIVE_TWEET_STORE.values() if not t.get("kws") or (want & set(t.get("kws", []))) or (include_leaders and bool(t.get("leaders")))]
    return sorted(rows, key=lambda x: x["ts"], reverse=True)


def _merge_x_items(items: List[Dict[str, Any]]):
    for tw in items:
        old = LIVE_TWEET_STORE.get(tw["id"])
        if old:
            tw["kws"] = sorted(set(old.get("kws", [])) | set(tw.get("kws", [])))
            tw["ingested_at"] = old.get("ingested_at", tw["ingested_at"])
        LIVE_TWEET_STORE[tw["id"]] = tw
    if items:
        mark_dirty()


async def get_x_live(kw: List[str], force: bool):
    if not get_cookie_pool():
        return [], "no_cookies", "Render Environment mein X_AUTH_TOKEN aur X_CT0 add karein"
    key = "x:" + "|".join(sorted(k.lower() for k in kw[:7]))

    async def factory():
        try:
            items = await x_search(kw, None, 20, force=force)
            _merge_x_items(items)
            health_ok("X", len(items))
            return {"status": "live", "error": ""}
        except Exception as e:
            health_fail("X", e)
            return {"status": "warning", "error": f"X temporary error (purane tweets safe hain): {str(e)[:120]}"}

    res = await swr(key, 45, factory, force=force, min_gap=15)
    return stored_tweets_for(kw, time.time(), include_leaders=True), res["status"], res["error"]


async def get_x_leaders_live(force: bool = False):
    if not get_cookie_pool():
        return [], "no_cookies", ""

    async def factory():
        try:
            items = await x_search(LEADER_X_QUERY_TERMS, None, 25, force=force, raw_terms=True)
            _merge_x_items(items)
            health_ok("X Leaders", len(items))
            return {"status": "live", "error": ""}
        except Exception as e:
            health_fail("X Leaders", e)
            return {"status": "warning", "error": str(e)[:100]}

    res = await swr("x:leaders_stream", 110, factory, force=force, min_gap=45)
    return sorted([t for t in LIVE_TWEET_STORE.values() if t.get("leaders")], key=lambda x: x["ts"], reverse=True), res["status"], res["error"]


async def get_x_archive(kw: List[str], force: bool, date_window: Tuple[str, str]):
    if not get_cookie_pool():
        return [], "no_cookies", "Render Environment mein X_AUTH_TOKEN aur X_CT0 add karein"
    key = f"xa:{'|'.join(sorted(k.lower() for k in kw[:7]))}:{date_window[0]}"

    async def factory():
        prev = (CACHE.get(key) or {}).get("val", {}).get("items", [])
        try:
            items = await x_search(kw, date_window, 35, force=force)
            health_ok("X", len(items))
            return {"items": items or prev, "status": "live", "error": ""}
        except Exception as e:
            health_fail("X", e)
            return {"items": prev, "status": "warning", "error": f"X temporary error: {str(e)[:120]}"}

    res = await swr(key, 600, factory, force=force, min_gap=20)
    return res["items"], res["status"], res["error"]


# ---- News / Papers / YouTube / Live TV ----
async def get_news(kw: List[str], lang: str, date_window: Optional[Tuple[str, str]], force: bool = False):
    top = kw[:5] or ["Rajasthan government", "Bhajanlal Sharma", "Jaipur"]
    kw_part = "(" + " OR ".join(f'"{k}"' if " " in k else k for k in top) + ") Rajasthan"
    q = f"{kw_part} after:{date_window[0]} before:{date_window[1]}" if date_window else f"{kw_part} when:1d"
    hl = "hi&gl=IN&ceid=IN:hi" if lang == "hi" else "en-IN&gl=IN&ceid=IN:en"
    url, key, name = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl={hl}", f"gn:{lang}:{q}", f"Google News {lang.upper()}"

    async def factory():
        prev = (CACHE.get(key) or {}).get("val", [])
        try:
            r = await HTTP.get(url, headers=HEADERS, timeout=8)
            r.raise_for_status()
            items = []
            for e in feedparser.parse(r.content).entries[:20]:
                title, link = clean_text(e.get("title", "")), e.get("link", "")
                src = e.get("source", {}).get("title", "") if isinstance(e.get("source"), dict) else ""
                if not src and " - " in title:
                    title, src = [p.strip() for p in title.rsplit(" - ", 1)]
                items.append(build_item(id_=f"gn_{hid(link or title)}", source="NEWS", author=src or "Rajasthan News", text=title, url=link, ts=parse_timestamp(e.get("published", ""))))
            health_ok(name, len(items))
            return items
        except Exception as e:
            health_fail(name, e)
            return prev

    return await swr(key, 900 if date_window else 120, factory, force=force, min_gap=20)


async def _read_rss(name: str, url: str, source: str, limit: int = 12) -> List[Dict[str, Any]]:
    r = await HTTP.get(url, headers=HEADERS, timeout=8)
    r.raise_for_status()
    out = []
    for e in feedparser.parse(r.content).entries[:limit]:
        title = clean_text(e.get("title", ""))
        if "news.google.com" in url and " - " in title:
            title = title.rsplit(" - ", 1)[0].strip()
        if title:
            link, pub = e.get("link", ""), e.get("published") or e.get("updated") or ""
            out.append(build_item(id_=f"{source.lower()}_{hid(link or title)}", source=source, author=name, text=title, url=link, ts=parse_timestamp(pub)))
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
            src, label = ("FACEBOOK", n[3:].strip()) if n.upper().startswith("FB:") else (("INSTAGRAM", n[3:].strip()) if n.upper().startswith("IG:") else ("NEWS", n))
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
                r = await HTTP.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}", headers=HEADERS, timeout=8)
                r.raise_for_status()
                out = []
                for e in feedparser.parse(r.content).entries[:8]:
                    vid, link = str(e.get("yt_videoid", "") or ""), e.get("link", "")
                    out.append(build_item(id_=f"yt_{vid or hid(link)}", source="YOUTUBE", author=name, text=e.get("title", ""), url=link, ts=parse_timestamp(e.get("published") or e.get("updated")), video_id=vid))
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
        async def one(name: str, cid: str):
            vid, is_live = None, False
            try:
                r = await HTTP.get(f"https://www.youtube.com/channel/{cid}/live", headers=YT_LIVE_HEADERS, timeout=7)
                m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"', r.text) or re.search(r'"videoId":"([A-Za-z0-9_-]{11})"[^}]{0,300}"isLive(?:Now)?":true', r.text)
                if m:
                    vid, is_live = m.group(1), True
            except Exception:
                pass
            if not vid:
                try:
                    r2 = await HTTP.get(f"https://www.youtube.com/channel/{cid}/streams", headers=YT_LIVE_HEADERS, timeout=7)
                    m_live = re.search(r'"videoId":"([A-Za-z0-9_-]{11})".{0,1800}?"(?:style|overlayStyle)":"LIVE"', r2.text)
                    if m_live:
                        vid, is_live = m_live.group(1), True
                    else:
                        m_any = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', r2.text)
                        if m_any:
                            vid = m_any.group(1)
                except Exception as e:
                    health_fail(f"TV {name}", e)
            if not vid:
                ch_vids = [it for it in sorted(yt_items, key=lambda x: x["ts"], reverse=True) if it.get("author") == name and len(it.get("video_id", "")) == 11]
                for it in ch_vids:
                    if re.search(r"\blive\b|लाइव", it.get("text", ""), re.I):
                        vid, is_live = it["video_id"], True
                        break
                if not vid and ch_vids:
                    vid = ch_vids[0]["video_id"]
            if vid:
                health_ok(f"TV {name}", 1)
                return {"source": name, "is_live": is_live, "embed_url": f"https://www.youtube.com/embed/{vid}?autoplay=1&mute=1&rel=0",
                        "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", "watch_url": f"https://www.youtube.com/watch?v={vid}"}
            return {"source": name, "is_live": False, "embed_url": "", "thumb": "", "watch_url": f"https://www.youtube.com/channel/{cid}/live"}
        return list(await asyncio.gather(*(one(n, c) for n, c in YOUTUBE_CHANNELS)))
    return await swr("tv", 300, factory, force=force, min_gap=45)


# ---- Self-Learning Suggestion & Anti-Wrong-Mark Endpoints ----
class FeedbackReq(BaseModel):
    item_id: str
    text: str = ""
    author: str = ""
    handle: str = ""
    target_sentiment: str
    confirm_override: bool = False


def _find_item(item_id: str) -> Optional[Dict[str, Any]]:
    for store in (LIVE_TWEET_STORE, LIVE_MEDIA_STORE, MANUAL_STORE):
        if item_id in store:
            return store[item_id]
    return None


@app.post("/api/feedback", dependencies=[Depends(require_key)])
async def submit_feedback(req: FeedbackReq):
    target = req.target_sentiment.strip().lower()
    if target not in ("positive", "negative", "neutral"):
        raise HTTPException(400, "target_sentiment positive/negative/neutral hona chahiye")
    item = _find_item(req.item_id)
    text = clean_text(item["text"] if item else req.text)
    author = item.get("author", "") if item else req.author
    handle = item.get("handle", "") if item else req.handle
    if not text:
        raise HTTPException(404, "Post nahi mila")

    base = analyze_sentiment_base(text, author=author, handle=handle)
    base_label, base_conf = base["label"], base["confidence"]
    opp_self_conflict = bool(base.get("opp_self_praise")) and target == "positive"
    is_opposite = (base_label == "negative" and target == "positive") or (base_label == "positive" and target == "negative")
    suspicious = opp_self_conflict or (is_opposite and base_conf >= 72)

    if suspicious and not req.confirm_override:
        warn_msg = (
            "AI Guardrail Warning: Yeh post Congress/Opposition ka self-praise lag raha hai — BJP tracker ke hisaab se yeh NEUTRAL hona chahiye, Positive nahi. Phir bhi Positive mark karna hai?"
            if opp_self_conflict else
            f"AI Guardrail Warning: Is post mein strong {base_label.upper()} signals hain ({', '.join(base['reasons'][:2]) or 'clear polarity'}, {base_conf}% confidence). Agar galti se click hua ho to Cancel karein; pakka {target.upper()} hai to Confirm karein."
        )
        return {"status": "warning", "requires_confirm": True, "base_label": base_label, "base_confidence": base_conf, "message": warn_msg}

    ITEM_OVERRIDES[req.item_id] = {"sentiment": target, "by_user": True, "flagged": suspicious, "ts": time.time()}
    if len(ITEM_OVERRIDES) > 600:
        for k in sorted(ITEM_OVERRIDES, key=lambda x: ITEM_OVERRIDES[x].get("ts", 0))[:100]:
            ITEM_OVERRIDES.pop(k, None)

    weight = 0.2 if suspicious else 1.0
    learned_tokens = extract_learning_tokens(text)
    if not opp_self_conflict:
        for tok in learned_tokens[:12]:
            rec = LEARNED_LEXICON.setdefault(tok, {"positive": 0.0, "negative": 0.0, "neutral": 0.0, "samples": 0, "quarantined": 0})
            rec[target] = round(float(rec.get(target, 0.0)) + weight, 2)
            if suspicious:
                rec["quarantined"] = int(rec.get("quarantined", 0)) + 1
                if rec["quarantined"] >= 2:
                    rec["samples"] = int(rec.get("samples", 0)) + 1
            else:
                rec["samples"] = int(rec.get("samples", 0)) + 1

    if len(LEARNED_LEXICON) > 500:
        for wk in sorted(LEARNED_LEXICON, key=lambda k: LEARNED_LEXICON[k].get("samples", 0))[:100]:
            LEARNED_LEXICON.pop(wk, None)

    FEEDBACK_LOG.append({"ts": time.time(), "item_id": req.item_id, "snippet": text[:100], "from": base_label, "to": target, "flagged": suspicious, "tokens": learned_tokens[:4]})
    if len(FEEDBACK_LOG) > 100:
        del FEEDBACK_LOG[:-100]

    reevaluate_all_stores()
    mark_dirty()
    return {
        "status": "overridden_quarantined" if suspicious else "learned",
        "flagged": suspicious, "learned_tokens": learned_tokens[:5],
        "message": (
            f"Post {target.upper()} mein shift ho gaya (AI conflict tha isliye global model ko safe/quarantine rakha gaya)."
            if suspicious else
            f"AI ne seekh liya! Post {target.upper()} mein shift ho gaya aur agle scans mein aise posts automatically {target.upper()} honge."
        ),
    }


@app.get("/api/learning", dependencies=[Depends(require_key)])
async def get_learning_stats():
    top_tokens = sorted(
        [{"token": k, **v} for k, v in LEARNED_LEXICON.items() if v.get("samples", 0) > 0],
        key=lambda x: (x["samples"], x["positive"] + x["negative"] + x["neutral"]), reverse=True,
    )[:40]
    return {
        "total_learned_tokens": len(LEARNED_LEXICON),
        "total_overrides": len(ITEM_OVERRIDES),
        "last_self_tune": LAST_SELF_TUNE_TS,
        "top_tokens": top_tokens,
        "recent_feedback": list(reversed(FEEDBACK_LOG[-20:])),
    }


@app.delete("/api/learning/{token}", dependencies=[Depends(require_key)])
async def delete_learned_token(token: str):
    LEARNED_LEXICON.pop(token.lower().strip(), None)
    reevaluate_all_stores()
    mark_dirty()
    return {"ok": True}


# ---- Manual Posts & Utility Routes ----
class ManualPost(BaseModel):
    platform: str
    author: str
    text: str
    url: str = ""
    posted_at: str = ""
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
                      author=author, text=text, url=p.url, ts=min(ts, time.time()), likes=max(0, p.likes), retweets=max(0, p.shares))
    item["manual"] = True
    MANUAL_STORE[item["id"]] = item
    if len(MANUAL_STORE) > 300:
        for k in sorted(MANUAL_STORE, key=lambda x: MANUAL_STORE[x]["ts"])[:50]:
            MANUAL_STORE.pop(k, None)
    record_daily_tracker([item], datetime.now(IST).strftime("%Y-%m-%d"))
    mark_dirty()
    return item


@app.delete("/api/manual/{item_id}", dependencies=[Depends(require_key)])
async def delete_manual(item_id: str):
    MANUAL_STORE.pop(item_id, None)
    mark_dirty()
    return {"ok": True}


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


@app.get("/api/pulse", dependencies=[Depends(require_key)])
async def pulse(since: float = Query(default=0.0), keywords: str = Query(default=",".join(DEFAULT_KEYWORDS))):
    now_ts = time.time()
    x_items = stored_tweets_for(sanitize_keywords(keywords) or DEFAULT_KEYWORDS, now_ts, include_leaders=True)
    new_x = sum(1 for it in x_items if it.get("ingested_at", it.get("ts", 0)) > since)
    new_media = sum(1 for it in list(LIVE_MEDIA_STORE.values()) + list(MANUAL_STORE.values())
                    if it.get("ingested_at", it.get("ts", 0)) > since and it.get("ts", 0) >= (now_ts - 86400))
    return {"server_time": now_ts, "new_x": new_x, "new_media": new_media, "new_total": new_x + new_media}


@app.get("/api/custom-graph", dependencies=[Depends(require_key)])
async def custom_graph(q: str = Query(default=""), source: str = Query(default="ALL"), leader: str = Query(default="")):
    hours = list(range(datetime.now(IST).hour + 1))
    pos, neg, neu = {h: 0 for h in hours}, {h: 0 for h in hours}, {h: 0 for h in hours}
    ql, src_f, ld_f = q.strip().lower(), source.upper().strip(), leader.strip().lower()
    qc = re.sub(r"\s+", "", ql)
    matched = 0
    for rec in DAILY_TRACKER.get("items", {}).values():
        rsrc = rec.get("source", "")
        if (src_f == "X" and rsrc != "X") or (src_f == "MEDIA" and rsrc == "X"):
            continue
        if ld_f and ld_f not in (rec.get("leaders") or []):
            continue
        if ql:
            hay = rec.get("hay", "")
            if ql not in hay and (len(qc) < 3 or qc not in hay.replace(" ", "")):
                continue
        h = rec.get("hour", 0)
        if h in pos:
            matched += 1
            s = rec.get("sentiment", "neutral")
            (pos if s == "positive" else (neg if s == "negative" else neu))[h] += 1
    return {
        "matched": matched,
        "totals": {"positive": sum(pos.values()), "negative": sum(neg.values()), "neutral": sum(neu.values())},
        "chart": {"labels": [f"{h:02d}:00" for h in hours], "positive": [pos[h] for h in hours], "negative": [neg[h] for h in hours], "neutral": [neu[h] for h in hours]},
    }


def crisis_score(items: List[Dict[str, Any]], now_ts: float, is_archive: bool) -> int:
    pool = items if is_archive else [i for i in items if now_ts - i["ts"] <= 10800]
    if len(pool) < 3:
        return 0
    neg = sum(1 for i in pool if i["sentiment"] == "negative")
    return int(round(100 * (0.55 * (neg / len(pool)) + 0.45 * min(1.0, neg / 15))))


@app.get("/api/feed", dependencies=[Depends(require_key)])
async def api_feed(keywords: str = Query(default=",".join(DEFAULT_KEYWORDS)), force: bool = Query(default=False), date: str = Query(default="")):
    kw_list = sanitize_keywords(keywords) or DEFAULT_KEYWORDS
    now_ist = datetime.now(IST)
    now_ts, today_str = now_ist.timestamp(), now_ist.strftime("%Y-%m-%d")
    date_window = validate_date(date, today_str)
    is_archive = date_window is not None and date_window[0] != today_str
    active_date, cutoff = (date_window[0] if is_archive else today_str), now_ts - 86400

    if not is_archive and kw_list:
        ACTIVE_KEYWORDS[:] = kw_list[:10]

    if is_archive:
        x_res, news_en, news_hi, live_tv = await asyncio.gather(
            get_x_archive(kw_list, force, date_window), get_news(kw_list, "en", date_window, force),
            get_news(kw_list, "hi", date_window, force), get_tv(False),
        )
        x_ld_items, media_pool = [], news_hi + news_en + [m for m in MANUAL_STORE.values() if m["date_ist"] == active_date]
    else:
        x_res, x_ld_res, news_en, news_hi, papers, yt_items, extra, live_tv = await asyncio.gather(
            get_x_live(kw_list, force), get_x_leaders_live(False),
            get_news(kw_list, "en", None, force), get_news(kw_list, "hi", None, force),
            get_papers(force), get_youtube(force), get_extra(force), get_tv(force),
        )
        x_ld_items = x_ld_res[0] if isinstance(x_ld_res, tuple) else []
        fresh_media = news_hi + news_en + papers + yt_items + extra
        record_media_items(fresh_media, now_ts)
        media_pool = list(LIVE_MEDIA_STORE.values()) + fresh_media + [m for m in MANUAL_STORE.values() if m["ts"] >= cutoff]

    x_items, x_status, x_error = x_res

    deduped_x, seen_x = [], set()
    for tw in sorted(x_items + x_ld_items, key=lambda x: x["ts"], reverse=True):
        if tw["id"] not in seen_x and (is_archive or tw["ts"] >= cutoff):
            seen_x.add(tw["id"])
            deduped_x.append(tw)

    deduped_media, seen_media = [], set()
    for it in sorted(media_pool, key=lambda x: x["ts"], reverse=True):
        if not is_archive and it["ts"] < cutoff:
            continue
        norm = re.sub(r"[^a-zA-Z0-9\u0900-\u097F]+", "", it["text"].lower())[:50]
        k = f"{it['source']}_{norm}" if norm else it["id"]
        if k not in seen_media:
            seen_media.add(k)
            deduped_media.append(it)

    deduped = sorted(deduped_x[:220] + deduped_media[:200], key=lambda x: x["ts"], reverse=True)

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
        record_daily_tracker(deduped, today_str)
        for rec in DAILY_TRACKER["items"].values():
            bump(rec.get("hour", 0), rec.get("sentiment", "neutral"), rec.get("source", ""))

    cnt, xcnt, by_src = {"positive": 0, "negative": 0, "neutral": 0}, {"positive": 0, "negative": 0, "neutral": 0}, {}
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
        hi_total += 1 if it["language"] == "Hindi" else 0
        en_total += 0 if it["language"] == "Hindi" else 1
        for table, name in ((district_stats, it["district"]), (dept_stats, it["department"])):
            row = table.setdefault(name, {"total": 0, "negative": 0, "positive": 0})
            row["total"] += 1
            if s in ("negative", "positive"):
                row[s] += 1
        acc = account_stats.setdefault(f"{src}::{it['author']}", {
            "author": it["author"], "handle": it.get("handle", ""), "source": src, "total": 0,
            "positive": 0, "negative": 0, "neutral": 0, "likes": 0, "retweets": 0, "last_ts": it["ts"],
        })
        acc["total"] += 1
        acc[s] += 1
        acc["likes"] += it.get("likes", 0)
        acc["retweets"] += it.get("retweets", 0)
        acc["last_ts"] = max(acc["last_ts"], it["ts"])

    accounts_list = []
    for acc in account_stats.values():
        t = max(acc["total"], 1)
        acc["pos_pct"], acc["neg_pct"], acc["neu_pct"] = round(acc["positive"] / t * 100, 1), round(acc["negative"] / t * 100, 1), round(acc["neutral"] / t * 100, 1)
        acc["stance"] = "Negative" if acc["negative"] > acc["positive"] else ("Positive" if acc["positive"] > acc["negative"] else ("Mixed" if acc["negative"] > 0 else "Neutral"))
        accounts_list.append(acc)
    accounts_list.sort(key=lambda a: (a["total"], a["negative"]), reverse=True)

    tag_rx = re.compile(r"#[\w\u0900-\u097F]+")
    def _top(d: Dict[str, int], n: int):
        return [{"name": k, "count": v} for k, v in sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:n]]

    handle_stats: Dict[str, Dict[str, Any]] = {}
    for it in deduped_x:
        hkey = it.get("handle") or it["author"]
        st = handle_stats.setdefault(hkey, {"handle": hkey, "name": it["author"], "total": 0, "positive": 0, "negative": 0, "neutral": 0,
                                            "likes": 0, "retweets": 0, "last_ts": it["ts"], "_dept": {}, "_dist": {}, "_tags": {}, "worst": None})
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

    handles_list = []
    for st in handle_stats.values():
        t = max(st["total"], 1)
        st["pos_pct"], st["neg_pct"], st["neu_pct"] = round(st["positive"] / t * 100, 1), round(st["negative"] / t * 100, 1), round(st["neutral"] / t * 100, 1)
        dept = st.pop("_dept")
        st["topics"] = _top({k: v for k, v in dept.items() if k != "General / Admin"} or dept, 3)
        st["districts"], st["hashtags"] = _top(st.pop("_dist"), 2), _top(st.pop("_tags"), 3)
        handles_list.append(st)
    handles_list.sort(key=lambda h: (h["total"], h["negative"]), reverse=True)

    # ---- Internet-Wide Prominent Leaders Buzz (Always calculated across X + Media, despite user filter!) ----
    leader_map = {
        ld["id"]: {
            "id": ld["id"], "name": ld["name"], "role": ld["role"], "party": ld["party"], "handle": ld["handle"],
            "x_total": 0, "x_positive": 0, "x_negative": 0, "x_neutral": 0,
            "media_total": 0, "total_buzz": 0, "total_positive": 0, "total_negative": 0, "total_neutral": 0,
            "likes": 0, "retweets": 0, "_dept": {}, "_tags": {}, "_h_pos": mk(), "_h_neg": mk(), "_h_neu": mk(), "latest_tweet": None,
        }
        for ld in LEADERS_CONFIG
    }
    for it in deduped:
        for lid in it.get("leaders") or []:
            row = leader_map.get(lid)
            if not row:
                continue
            row["total_buzz"] += 1
            row[f"total_{it['sentiment']}"] += 1
            if it["source"] == "X":
                row["x_total"] += 1
                row[f"x_{it['sentiment']}"] += 1
                row["likes"] += it.get("likes", 0)
                row["retweets"] += it.get("retweets", 0)
                row["_dept"][it["department"]] = row["_dept"].get(it["department"], 0) + 1
                for tg in {t.lower() for t in tag_rx.findall(it["text"])}:
                    row["_tags"][tg] = row["_tags"].get(tg, 0) + 1
                if row["latest_tweet"] is None:
                    row["latest_tweet"] = {"author": it["author"], "text": it["text"][:160], "sentiment": it["sentiment"], "url": it["url"], "time_str": it["time_str"]}
                if is_archive and it["hour_ist"] in row["_h_pos"]:
                    s = it["sentiment"]
                    row["_h_pos" if s == "positive" else ("_h_neg" if s == "negative" else "_h_neu")][it["hour_ist"]] += 1
            else:
                row["media_total"] += 1

    if not is_archive:
        for rec in DAILY_TRACKER.get("items", {}).values():
            if rec.get("source") != "X":
                continue
            h, s = rec.get("hour", 0), rec.get("sentiment", "neutral")
            bk = "_h_pos" if s == "positive" else ("_h_neg" if s == "negative" else "_h_neu")
            for lid in rec.get("leaders") or []:
                if lid in leader_map and h in leader_map[lid][bk]:
                    leader_map[lid][bk][h] += 1

    leaders_list = []
    for row in leader_map.values():
        xt = max(row["x_total"], 1)
        row["x_pos_pct"] = round(row["x_positive"] / xt * 100, 1) if row["x_total"] else 0.0
        row["x_neg_pct"] = round(row["x_negative"] / xt * 100, 1) if row["x_total"] else 0.0
        row["x_neu_pct"] = round(row["x_neutral"] / xt * 100, 1) if row["x_total"] else 0.0
        dept = row.pop("_dept")
        row["topics"] = _top({k: v for k, v in dept.items() if k != "General / Admin"} or dept, 3)
        row["hashtags"] = _top(row.pop("_tags"), 3)
        hp, hn, hu = row.pop("_h_pos"), row.pop("_h_neg"), row.pop("_h_neu")
        row["hourly_x"] = {"labels": [f"{h:02d}:00" for h in hours], "positive": [hp[h] for h in hours], "negative": [hn[h] for h in hours], "neutral": [hu[h] for h in hours]}
        leaders_list.append(row)
    leaders_list.sort(key=lambda x: (x["total_buzz"], x["x_total"]), reverse=True)

    total = max(len(deduped), 1)
    pct = lambda n: round(n / total * 100, 1)  # noqa: E731
    sources = [{"name": n, "ok": h["ok"], "age_s": int(now_ts - h["ts"]), "count": h["count"], "error": h["error"],
                "last_ok_s": int(now_ts - h["last_ok"]) if h["last_ok"] else None} for n, h in sorted(SOURCE_HEALTH.items())]

    return {
        "server_time": now_ts, "updated_at": now_ist.strftime("%d %b %Y, %I:%M:%S %p IST"),
        "mode": "archive" if is_archive else "live", "active_date": active_date, "today_date": today_str,
        "x_status": x_status, "x_error": x_error, "count": len(deduped),
        "learning": {"tokens": len(LEARNED_LEXICON), "overrides": len(ITEM_OVERRIDES)},
        "stats": {
            "total": len(deduped), "x_count": by_src.get("X", 0), "x_today_count": x_today_count,
            "news_count": by_src.get("NEWS", 0) + by_src.get("NEWSPAPER", 0),
            "yt_count": by_src.get("YOUTUBE", 0), "fb_count": by_src.get("FACEBOOK", 0), "ig_count": by_src.get("INSTAGRAM", 0),
            "positive": cnt["positive"], "negative": cnt["negative"], "neutral": cnt["neutral"],
            "x_positive": xcnt["positive"], "x_negative": xcnt["negative"], "x_neutral": xcnt["neutral"],
            "positive_pct": pct(cnt["positive"]), "negative_pct": pct(cnt["negative"]), "neutral_pct": pct(cnt["neutral"]),
            "hindi_count": hi_total, "english_count": en_total, "crisis_score": crisis_score(deduped, now_ts, is_archive),
        },
        "hourly_chart": {"labels": [f"{h:02d}:00" for h in hours], "positive": [h_pos[h] for h in hours], "negative": [h_neg[h] for h in hours], "neutral": [h_neu[h] for h in hours]},
        "hourly_chart_x": {"labels": [f"{h:02d}:00" for h in hours], "positive": [hx_pos[h] for h in hours], "negative": [hx_neg[h] for h in hours], "neutral": [hx_neu[h] for h in hours]},
        "districts": sorted([{"name": k, **v} for k, v in district_stats.items()], key=lambda x: (x["negative"], x["total"]), reverse=True)[:10],
        "departments": sorted([{"name": k, **v} for k, v in dept_stats.items()], key=lambda x: (x["negative"], x["total"]), reverse=True),
        "accounts": accounts_list, "handles": handles_list[:200], "leaders": leaders_list,
        "sources": sources, "live_tv": live_tv, "items": deduped,
    }
