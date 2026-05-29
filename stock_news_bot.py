#!/usr/bin/env python3
"""
StockPilot NSE/BSE Filing Bot v3.3
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
v3.3 fixes:
  - Gemini model updated to gemini-2.0-flash (latest free)
  - Gemini tested at startup — shows exact error if failing
  - Fallback to gemini-1.5-flash if 2.0 fails
  - Date filter: only last 7 days
  - All IZMO codes fixed
"""

import os, time, sqlite3, hashlib, json, re, logging, sys
from datetime import datetime, timedelta
import pytz
import requests

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("StockPilot")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID         = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "").strip()
CHECK_INTERVAL  = int(os.environ.get("CHECK_INTERVAL", "300"))
DB_PATH         = os.environ.get("DB_PATH", "filings.db")
IST             = pytz.timezone("Asia/Kolkata")
FILING_MAX_AGE_DAYS = 7

# Gemini models to try in order
GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
]
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_gemini_model = None  # will be set after startup test

def validate_config():
    missing = []
    if not TELEGRAM_TOKEN: missing.append("TELEGRAM_TOKEN")
    if not CHAT_ID:        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        log.error(f"MISSING env vars: {', '.join(missing)}")
        sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# GEMINI STARTUP TEST — find working model
# ─────────────────────────────────────────────────────────────────────────────
def test_gemini():
    global _gemini_model
    if not GEMINI_API_KEY:
        log.warning("GEMINI_API_KEY not set — AI disabled")
        return False

    for model in GEMINI_MODELS:
        url = f"{GEMINI_BASE}/{model}:generateContent?key={GEMINI_API_KEY}"
        try:
            r = requests.post(
                url,
                json={"contents": [{"parts": [{"text": "Reply with just the word: OK"}]}],
                      "generationConfig": {"maxOutputTokens": 5}},
                timeout=15
            )
            if r.ok:
                _gemini_model = model
                log.info(f"Gemini model working: {model} ✅")
                return True
            else:
                log.warning(f"Gemini model {model} failed: HTTP {r.status_code} — {r.text[:200]}")
        except Exception as e:
            log.warning(f"Gemini model {model} error: {e}")

    log.error("All Gemini models failed — AI analysis disabled")
    log.error("Check your GEMINI_API_KEY at aistudio.google.com")
    return False

# ─────────────────────────────────────────────────────────────────────────────
# DATE FILTER
# ─────────────────────────────────────────────────────────────────────────────
DATE_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d-%b-%Y %H:%M:%S",
    "%d-%b-%Y",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
    "%d %b %Y",
]

def parse_date(s):
    if not s: return None
    s = s.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None

def is_recent(date_str, days=FILING_MAX_AGE_DAYS):
    dt = parse_date(date_str)
    if dt is None: return True  # unknown date → send it
    return dt >= datetime.now() - timedelta(days=days)

# ─────────────────────────────────────────────────────────────────────────────
# STOCKS
# ─────────────────────────────────────────────────────────────────────────────
PORTFOLIO = [
    dict(ticker="ADVAIT",     name="Advait Infratech",       nse="ADVAIT",      bse="543259", sector="Infrastructure",     cat="PORTFOLIO"),
    dict(ticker="ANANTRAJ",   name="Anant Raj Ltd",          nse="ANANTRAJ",    bse="515055", sector="Real Estate",         cat="PORTFOLIO"),
    dict(ticker="APOLLO",     name="Apollo Micro Systems",   nse="APOLLOMICRO", bse="543288", sector="Defence Electronics", cat="PORTFOLIO"),
    dict(ticker="BEL",        name="Bharat Electronics",     nse="BEL",         bse="500049", sector="Defence",             cat="PORTFOLIO"),
    dict(ticker="CDSL",       name="CDSL",                   nse="CDSL",        bse="543272", sector="Financial Services",  cat="PORTFOLIO"),
    dict(ticker="HAL",        name="Hindustan Aeronautics",  nse="HAL",         bse="541154", sector="Defence",             cat="PORTFOLIO"),
    dict(ticker="HAPPSTMNDS", name="Happiest Minds",         nse="HAPPSTMNDS",  bse="543227", sector="IT",                  cat="PORTFOLIO"),
    dict(ticker="IFCI",       name="IFCI Ltd",               nse="IFCI",        bse="500106", sector="NBFC",                cat="PORTFOLIO"),
    dict(ticker="INOXINDIA",  name="INOX India",             nse="INOXINDIA",   bse="543716", sector="Industrial Gas",      cat="PORTFOLIO"),
    dict(ticker="IZMO",       name="Izmo Ltd",               nse="IZMO",        bse="532341", sector="Auto Technology",     cat="PORTFOLIO"),
    dict(ticker="KPEL",       name="K.P. Energy",            nse="KPEL",        bse="540698", sector="Renewable Energy",    cat="PORTFOLIO"),
    dict(ticker="NETWEB",     name="Netweb Technologies",    nse="NETWEB",      bse="543920", sector="IT Hardware",         cat="PORTFOLIO"),
    dict(ticker="PENIND",     name="Pen Industries",         nse="PENIND",      bse="523260", sector="Media",               cat="PORTFOLIO"),
    dict(ticker="PGEL",       name="PG Electroplast",        nse="PGEL",        bse="543594", sector="Electronics",         cat="PORTFOLIO"),
    dict(ticker="REMSONSIND", name="Remsons Industries",     nse="REMSONSIND",  bse="517437", sector="Automobile",          cat="PORTFOLIO"),
    dict(ticker="RVNL",       name="Rail Vikas Nigam",       nse="RVNL",        bse="542649", sector="Railways & Infra",    cat="PORTFOLIO"),
]

WATCHLIST = [
    dict(ticker="JAINRESOUR", name="Jain Resource Recycl",  nse=None,          bse="533289", sector="Recycling",        cat="WATCHLIST"),
    dict(ticker="IREDA",      name="Indian Renewable Energy",nse="IREDA",       bse="544124", sector="Renewable Energy", cat="WATCHLIST"),
    dict(ticker="IZMOWATCH",  name="Izmo Ltd (Watch)",       nse="IZMO",        bse="532341", sector="Auto Technology",  cat="WATCHLIST"),
    dict(ticker="ONEGLOBAL",  name="One Global Service",     nse="ONEGLOBAL",   bse=None,     sector="Services",         cat="WATCHLIST"),
    dict(ticker="DOMS",       name="DOMS Industries",        nse="DOMS",        bse="544045", sector="Consumer",         cat="WATCHLIST"),
    dict(ticker="LANCER",     name="Lancer Container",       nse=None,          bse="526807", sector="Packaging",        cat="WATCHLIST"),
]

ALL_STOCKS = PORTFOLIO + WATCHLIST

# ─────────────────────────────────────────────────────────────────────────────
# CLASSIFIER
# ─────────────────────────────────────────────────────────────────────────────
CATEGORIES = {
    "Result":               ("📊 Financial Result",       "HIGH"),
    "Board Meeting":        ("🗓 Board Meeting",           "HIGH"),
    "Dividend":             ("💰 Dividend",                "HIGH"),
    "Bonus":                ("🎁 Bonus Shares",            "HIGH"),
    "Split":                ("✂️ Stock Split",             "HIGH"),
    "Buyback":              ("♻️ Buyback",                 "HIGH"),
    "Merger":               ("🔀 Merger/Acquisition",      "HIGH"),
    "Acquisition":          ("🔀 Merger/Acquisition",      "HIGH"),
    "Rights":               ("📝 Rights Issue",            "HIGH"),
    "Order":                ("🏆 Order/Contract Win",      "HIGH"),
    "Contract":             ("🏆 Order/Contract Win",      "HIGH"),
    "Scheme":               ("📋 Scheme of Arrangement",  "HIGH"),
    "Spurt":                ("📈 Volume Spurt",            "MEDIUM"),
    "AGM":                  ("🏛 AGM/EGM",                "MEDIUM"),
    "EGM":                  ("🏛 AGM/EGM",                "MEDIUM"),
    "Appointment":          ("👤 Board Change",            "MEDIUM"),
    "Cessation":            ("👤 Board Change",            "MEDIUM"),
    "Change in Management": ("👤 Management Change",       "MEDIUM"),
    "Insider":              ("🔍 Insider Trading",         "MEDIUM"),
    "Analyst":              ("📊 Analyst/Investor Meet",   "MEDIUM"),
    "Investor":             ("📊 Analyst/Investor Meet",   "MEDIUM"),
    "Press Release":        ("📰 Press Release",           "MEDIUM"),
    "Update":               ("📢 Business Update",         "MEDIUM"),
    "Litigation":           ("⚖️ Litigation",             "MEDIUM"),
    "General":              ("📢 General Announcement",    "MEDIUM"),
    "Record Date":          ("📅 Record Date",             "MEDIUM"),
    "Allotment":            ("📋 Share Allotment",         "MEDIUM"),
    "Price":                ("📈 Price Movement",          "MEDIUM"),
}

SKIP = [
    "certificate under sebi",
    "trading window",
    "newspaper publication",
    "copy of newspaper",
    "registrar & share transfer",
    "reconciliation of share capital",
    "loss of share certificate",
    "sebi (depositories",
    "compliances-reg.",
    "reg. 74", "reg. 76", "reg. 57", "reg. 40",
]

def classify(title, cat_raw=""):
    combined = (title + " " + cat_raw).lower()
    if any(s in combined for s in SKIP):
        return None, None
    for kw, (label, imp) in CATEGORIES.items():
        if kw.lower() in combined:
            return label, imp
    return "📢 Corporate Filing", "MEDIUM"

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS sent_filings (
        hash TEXT PRIMARY KEY, ticker TEXT,
        title TEXT, source TEXT, sent_at INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS errors (
        id INTEGER PRIMARY KEY AUTOINCREMENT, msg TEXT, ts INTEGER)""")
    conn.execute("DELETE FROM sent_filings WHERE sent_at < ?",
                 (int(time.time()) - 30*86400,))
    conn.commit()
    log.info("Database ready ✅")
    return conn

def _hash(source, ticker, title):
    return hashlib.sha256(
        f"{source}:{ticker}:{title.strip().lower()}".encode()).hexdigest()

def is_dup(conn, source, ticker, title):
    return conn.execute("SELECT 1 FROM sent_filings WHERE hash=?",
                        (_hash(source, ticker, title),)).fetchone() is not None

def mark(conn, source, ticker, title):
    conn.execute("INSERT OR IGNORE INTO sent_filings VALUES (?,?,?,?,?)",
                 (_hash(source, ticker, title), ticker, title[:200],
                  source, int(time.time())))
    conn.commit()

def log_err(conn, msg):
    conn.execute("INSERT INTO errors VALUES (NULL,?,?)",
                 (msg[:500], int(time.time())))
    conn.commit()

# ─────────────────────────────────────────────────────────────────────────────
# NSE SESSION
# ─────────────────────────────────────────────────────────────────────────────
class NSESession:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"),
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer":         "https://www.nseindia.com/",
        })
        self.warmed = False
        self._last = 0

    def warm(self):
        try:
            self.s.get("https://www.nseindia.com/", timeout=15)
            time.sleep(2)
            self.s.get("https://www.nseindia.com/companies-listing/"
                       "corporate-filings-announcements", timeout=12)
            time.sleep(1)
            self.warmed = True
            self._last = time.time()
            log.info("NSE session warmed ✅")
        except Exception as e:
            log.warning(f"NSE warmup: {e}")

    def get(self, url):
        if not self.warmed or time.time() - self._last > 1800:
            self.warm()
        try:
            r = self.s.get(url, timeout=15)
            if r.status_code == 401:
                self.warmed = False
                self.warm()
                r = self.s.get(url, timeout=15)
            return r
        except Exception as e:
            log.debug(f"NSE GET: {e}")
            return None

nse = NSESession()
BSE_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bseindia.com/",
         "Origin": "https://www.bseindia.com"}

# ─────────────────────────────────────────────────────────────────────────────
# FETCHERS
# ─────────────────────────────────────────────────────────────────────────────
def fetch_nse(sym):
    if not sym: return []
    r = nse.get(f"https://www.nseindia.com/api/corporate-announcements"
                f"?index=equities&symbol={sym}")
    if not r or not r.ok: return []
    try:
        out = []
        for a in r.json()[:20]:
            t = (a.get("desc") or a.get("sm_name") or "").strip()
            if not t: continue
            att = a.get("attchmnt") or ""
            out.append(dict(
                title=t,
                link=(f"https://nsearchives.nseindia.com/corporate/xbrl/{att}"
                      if att else
                      f"https://www.nseindia.com/companies-listing/"
                      f"corporate-filings-announcements?symbol={sym}"),
                category=(a.get("subject") or a.get("Categorycode") or ""),
                date=(a.get("sort_date") or a.get("an_dt") or "")
            ))
        return out
    except Exception as e:
        log.debug(f"NSE parse {sym}: {e}")
        return []

def fetch_bse(code):
    if not code: return []
    out = []
    for dur in ["D", "W"]:
        try:
            r = requests.get(
                f"https://api.bseindia.com/BseIndiaAPI/api/"
                f"AnnGetAnnouncementDet/w?scripcd={code}&dur={dur}",
                headers=BSE_H, timeout=15)
            if not r.ok: continue
            for a in (r.json().get("Table") or [])[:20]:
                t = (a.get("HEADLINE") or a.get("NEWSSUB") or "").strip()
                if not t: continue
                att = a.get("ATTACHMENTNAME") or ""
                out.append(dict(
                    title=t,
                    link=(f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att}"
                          if att else
                          f"https://www.bseindia.com/corporates/ann.html?scripcd={code}"),
                    category=(a.get("CATEGORYNAME") or ""),
                    date=(a.get("NEWS_DT") or a.get("DTIME") or "")
                ))
            if dur == "D" and out: break
        except Exception as e:
            log.debug(f"BSE {code}: {e}")
    return out

def fetch_bse_ca(code):
    if not code: return []
    try:
        r = requests.get(
            f"https://api.bseindia.com/BseIndiaAPI/api/"
            f"DefaultData/w?scripcd={code}&type=CA",
            headers=BSE_H, timeout=12)
        if not r.ok: return []
        out = []
        for row in (r.json().get("Table") or [])[:5]:
            p = (row.get("PURPOSE") or "").strip()
            if not p: continue
            ex = row.get("EX_DATE") or row.get("EXDATE") or ""
            rc = row.get("REC_DATE") or ""
            t = p + (f" | Ex-Date: {ex}" if ex else "") + \
                     (f" | Record Date: {rc}" if rc else "")
            out.append(dict(
                title=t,
                link=f"https://www.bseindia.com/stock-share-price/corporate-actions/{code}",
                category="Corporate Action",
                date=ex
            ))
        return out
    except Exception as e:
        log.debug(f"BSE CA {code}: {e}")
        return []

# ─────────────────────────────────────────────────────────────────────────────
# GEMINI AI — with model auto-selection
# ─────────────────────────────────────────────────────────────────────────────
def gemini_analyze(title, company, sector, category):
    global _gemini_model
    if not GEMINI_API_KEY or not _gemini_model:
        return None

    prompt = (
        f"You are an expert Indian stock market analyst.\n\n"
        f"NSE/BSE Filing: \"{title}\"\n"
        f"Company: {company} | Sector: {sector} | Type: {category}\n\n"
        f"Respond ONLY with this JSON (no markdown, no backticks):\n"
        f'{{"summary":"2-3 plain English sentences for retail investor",'
        f'"sentiment":"bullish OR bearish OR neutral",'
        f'"impact":"high OR medium OR low",'
        f'"action":"BUY MORE OR HOLD OR WATCH OR REDUCE OR AVOID",'
        f'"reason":"One sentence why"}}'
    )

    url = f"{GEMINI_BASE}/{_gemini_model}:generateContent?key={GEMINI_API_KEY}"
    try:
        r = requests.post(
            url,
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"temperature": 0.3, "maxOutputTokens": 300}},
            timeout=20
        )
        if not r.ok:
            log.warning(f"Gemini {r.status_code}: {r.text[:150]}")
            return None

        text = (r.json()["candidates"][0]["content"]["parts"][0]["text"]
                .strip())
        text = re.sub(r"```json\n?|```", "", text).strip()
        m = re.search(r"\{[\s\S]+?\}", text)
        if not m:
            return None
        res = json.loads(m.group())
        res["sentiment"] = res.get("sentiment", "neutral").lower()
        res["impact"]    = res.get("impact", "medium").lower()
        res["action"]    = res.get("action", "WATCH").upper()
        if res["sentiment"] not in ["bullish","bearish","neutral"]:
            res["sentiment"] = "neutral"
        if res["impact"] not in ["high","medium","low"]:
            res["impact"] = "medium"
        return res
    except json.JSONDecodeError:
        log.warning("Gemini: could not parse JSON response")
        return None
    except Exception as e:
        log.warning(f"Gemini error: {e}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
# MESSAGE BUILDER
# ─────────────────────────────────────────────────────────────────────────────
SE = {"bullish":"🟢","bearish":"🔴","neutral":"🟡"}
IE = {"high":"🔥","medium":"⚡","low":"💧"}
AE = {"BUY MORE":"🚀","HOLD":"✋","WATCH":"👀","REDUCE":"⚠️","AVOID":"🚫"}

def now_ist():
    return datetime.now(IST).strftime("%d %b %Y · %I:%M %p IST")

def build_msg(stock, source, filing, cat_label, importance, ai):
    ce  = "📊" if stock["cat"] == "PORTFOLIO" else "👁"
    itg = {"HIGH":"🔴 HIGH","MEDIUM":"🟡 MEDIUM","LOW":"🟢 LOW"}.get(importance,"🟡")
    L = [
        f"{'━'*22}",
        f"🏛 <b>{source} OFFICIAL FILING</b>",
        f"{'━'*22}",
        f"{ce} <b>{stock['cat']}</b>  ·  <code>{stock['ticker']}</code>",
        f"🏢 <b>{stock['name']}</b>",
        f"🏭 {stock['sector']}  ·  {itg}",
        f"🏷 {cat_label}",
        "",
        f"📄 <b>{filing['title']}</b>",
        "",
    ]
    if ai:
        L += [
            "🤖 <b>AI Analysis (Gemini)</b>",
            "─"*20,
            f"📝 {ai.get('summary','')}",
            "",
            f"{SE.get(ai['sentiment'],'🟡')} Sentiment: <b>{ai['sentiment'].capitalize()}</b>",
            f"{IE.get(ai['impact'],'⚡')} Impact: <b>{ai['impact'].capitalize()}</b>",
            f"{AE.get(ai['action'],'👀')} Signal: <b>{ai['action']}</b>",
            f"💡 {ai.get('reason','')}",
            "",
        ]
    if filing.get("date"):
        L.append(f"📅 Filed: {filing['date']}")
    L += [
        f"🔗 <a href=\"{filing['link']}\">View on {source}</a>",
        f"⏰ {now_ist()}",
    ]
    return "\n".join(L)

# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────────────────────────
def send_tg(text, retries=3):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(text); return False
    for i in range(retries):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": text[:4000],
                      "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=12)
            if r.ok: return True
            if r.status_code == 429:
                wait = r.json().get("parameters",{}).get("retry_after",30)
                time.sleep(wait); continue
            log.error(f"TG {r.status_code}: {r.text[:100]}")
            return False
        except Exception as e:
            log.error(f"TG error: {e}")
            time.sleep(5)
    return False

# ─────────────────────────────────────────────────────────────────────────────
# PROCESS STOCK
# ─────────────────────────────────────────────────────────────────────────────
def process(stock, conn):
    sent = 0
    all_f = []
    if stock.get("nse"):
        all_f += [("NSE", f) for f in fetch_nse(stock["nse"])]
        time.sleep(0.8)
    if stock.get("bse"):
        all_f += [("BSE", f) for f in fetch_bse(stock["bse"])]
        all_f += [("BSE", f) for f in fetch_bse_ca(stock["bse"])]
        time.sleep(0.5)

    for source, f in all_f:
        if not is_recent(f.get("date", ""), FILING_MAX_AGE_DAYS):
            continue
        cat_label, importance = classify(f["title"], f.get("category",""))
        if cat_label is None: continue
        if is_dup(conn, source, stock["ticker"], f["title"]): continue
        mark(conn, source, stock["ticker"], f["title"])

        log.info(f"  [{source}][{stock['ticker']}][{importance}] {f['title'][:65]}")
        ai  = gemini_analyze(f["title"], stock["name"], stock["sector"], cat_label)
        msg = build_msg(stock, source, f, cat_label, importance, ai)
        if send_tg(msg):
            sent += 1
            time.sleep(1.5)
    return sent

# ─────────────────────────────────────────────────────────────────────────────
# CYCLE + STARTUP
# ─────────────────────────────────────────────────────────────────────────────
def run_cycle(conn):
    log.info(f"━━ Cycle {datetime.now(IST).strftime('%H:%M:%S IST')} ━━")
    total = sum(process(s, conn) for s in ALL_STOCKS)
    log.info(f"━━ Done. {total} sent ━━\n")

def send_startup(gemini_ok):
    model_info = f"✅ {_gemini_model}" if gemini_ok else "❌ Failed — check key at aistudio.google.com"
    msg = (
        f"{'━'*22}\n"
        f"🚀 <b>StockPilot Bot v3.3</b>\n"
        f"{'━'*22}\n"
        f"⏰ {datetime.now(IST).strftime('%d %b %Y · %I:%M %p IST')}\n\n"
        f"📊 Portfolio: {len(PORTFOLIO)} stocks\n"
        f"👁 Watchlist: {len(WATCHLIST)} stocks\n\n"
        f"🤖 Gemini AI: {model_info}\n"
        f"📅 Age filter: last {FILING_MAX_AGE_DAYS} days only\n"
        f"🔄 Interval: every {CHECK_INTERVAL//60} min\n"
        f"{'━'*22}"
    )
    send_tg(msg)

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    validate_config()
    log.info("StockPilot Bot v3.3 starting…")
    conn      = init_db()
    nse.warm()
    gemini_ok = test_gemini()
    send_startup(gemini_ok)

    errs = 0
    while True:
        try:
            run_cycle(conn)
            errs = 0
        except KeyboardInterrupt:
            break
        except Exception as e:
            errs += 1
            log.error(f"Cycle error #{errs}: {e}", exc_info=True)
            log_err(conn, str(e))
            if errs >= 5:
                send_tg(f"⚠️ 5 errors in a row\nLast: {str(e)[:200]}")
                errs = 0
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
