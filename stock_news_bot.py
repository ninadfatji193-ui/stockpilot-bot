#!/usr/bin/env python3
"""
StockPilot NSE/BSE Filing Bot v3.2
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Fixes in v3.2:
  - Date filter: only filings from last 7 days sent
  - Gemini key validation with clear error message
  - Better Gemini error handling
  - Old filings from NSE/BSE are ignored automatically
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
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID         = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "").strip()
CHECK_INTERVAL  = int(os.environ.get("CHECK_INTERVAL", "300"))
DB_PATH         = os.environ.get("DB_PATH", "filings.db")
IST             = pytz.timezone("Asia/Kolkata")

# Only send filings filed within last N days
FILING_MAX_AGE_DAYS = 7

def validate_config():
    missing = []
    if not TELEGRAM_TOKEN: missing.append("TELEGRAM_TOKEN")
    if not CHAT_ID:        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        log.error(f"Missing required env vars: {', '.join(missing)}")
        sys.exit(1)
    if GEMINI_API_KEY:
        log.info("Gemini API key found ✅ — AI analysis enabled")
    else:
        log.warning("GEMINI_API_KEY not set — AI summaries disabled")

# ─────────────────────────────────────────────────────────────────────────────
# DATE FILTER — ignore old filings
# ─────────────────────────────────────────────────────────────────────────────
DATE_FORMATS = [
    "%d-%b-%Y %H:%M:%S",
    "%d-%b-%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
    "%d %b %Y",
    "%b %d, %Y",
]

def parse_filing_date(date_str):
    """Parse filing date string → datetime or None."""
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(date_str[:len(fmt)+2], fmt)
        except ValueError:
            continue
    return None

def is_recent_filing(date_str, max_days=FILING_MAX_AGE_DAYS):
    """Return True if filing is within max_days, or if date unknown (don't skip)."""
    dt = parse_filing_date(date_str)
    if dt is None:
        # Can't parse date — send it (better safe than sorry)
        return True
    cutoff = datetime.now() - timedelta(days=max_days)
    return dt >= cutoff

# ─────────────────────────────────────────────────────────────────────────────
# STOCKS — verified NSE symbols + BSE scrip codes
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
    dict(ticker="JAINRESOUR", name="Jain Resource Recycl",  nse=None,          bse="533289", sector="Recycling",           cat="WATCHLIST"),
    dict(ticker="IREDA",      name="Indian Renewable Energy",nse="IREDA",       bse="544124", sector="Renewable Energy",    cat="WATCHLIST"),
    dict(ticker="IZMOWATCH",  name="Izmo Ltd (Watch)",       nse="IZMO",        bse="532341", sector="Auto Technology",     cat="WATCHLIST"),
    dict(ticker="ONEGLOBAL",  name="One Global Service",     nse="ONEGLOBAL",   bse=None,     sector="Services",            cat="WATCHLIST"),
    dict(ticker="DOMS",       name="DOMS Industries",        nse="DOMS",        bse="544045", sector="Consumer",            cat="WATCHLIST"),
    dict(ticker="LANCER",     name="Lancer Container",       nse=None,          bse="526807", sector="Packaging",           cat="WATCHLIST"),
]

ALL_STOCKS = PORTFOLIO + WATCHLIST

# ─────────────────────────────────────────────────────────────────────────────
# FILING CATEGORIES
# ─────────────────────────────────────────────────────────────────────────────
IMPORTANT_CATEGORIES = {
    "Result":               ("📊 Financial Result",        "HIGH"),
    "Board Meeting":        ("🗓 Board Meeting",            "HIGH"),
    "Dividend":             ("💰 Dividend",                 "HIGH"),
    "Bonus":                ("🎁 Bonus Shares",             "HIGH"),
    "Split":                ("✂️ Stock Split",              "HIGH"),
    "Buyback":              ("♻️ Buyback",                  "HIGH"),
    "Merger":               ("🔀 Merger / Acquisition",     "HIGH"),
    "Acquisition":          ("🔀 Merger / Acquisition",     "HIGH"),
    "Rights":               ("📝 Rights Issue",             "HIGH"),
    "Order":                ("🏆 Order / Contract Win",     "HIGH"),
    "Contract":             ("🏆 Order / Contract Win",     "HIGH"),
    "Scheme":               ("📋 Scheme of Arrangement",   "HIGH"),
    "Spurt":                ("📈 Volume Spurt",             "MEDIUM"),
    "Price":                ("📈 Price Movement",           "MEDIUM"),
    "AGM":                  ("🏛 AGM / EGM",               "MEDIUM"),
    "EGM":                  ("🏛 AGM / EGM",               "MEDIUM"),
    "Appointment":          ("👤 Board Change",             "MEDIUM"),
    "Cessation":            ("👤 Board Change",             "MEDIUM"),
    "Change in Management": ("👤 Management Change",        "MEDIUM"),
    "Insider":              ("🔍 Insider Trading",          "MEDIUM"),
    "Analyst":              ("📊 Analyst Meet",             "MEDIUM"),
    "Investor":             ("📊 Investor Presentation",    "MEDIUM"),
    "Press Release":        ("📰 Press Release",            "MEDIUM"),
    "Update":               ("📢 Business Update",          "MEDIUM"),
    "Litigation":           ("⚖️ Litigation",              "MEDIUM"),
    "General":              ("📢 General Announcement",     "MEDIUM"),
    "Record Date":          ("📅 Record Date",              "MEDIUM"),
    "Allotment":            ("📋 Share Allotment",          "MEDIUM"),
}

SKIP_KEYWORDS = [
    "certificate under sebi",
    "trading window",
    "newspaper publication",
    "copy of newspaper",
    "registrar & share transfer",
    "reconciliation of share capital",
    "loss of share certificate",
    "sebi (depositories and participants)",
    "compliances-reg.",
    "reg. 74",
    "reg. 76",
    "reg. 57",
    "reg. 40",
]

def classify(title, cat_raw=""):
    combined = (title + " " + cat_raw).lower()
    if any(skip in combined for skip in SKIP_KEYWORDS):
        return None, None
    for kw, (label, imp) in IMPORTANT_CATEGORIES.items():
        if kw.lower() in combined:
            return label, imp
    return "📢 Corporate Filing", "MEDIUM"

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent_filings (
            hash    TEXT PRIMARY KEY,
            ticker  TEXT,
            title   TEXT,
            source  TEXT,
            sent_at INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS errors (
            id  INTEGER PRIMARY KEY AUTOINCREMENT,
            msg TEXT,
            ts  INTEGER
        )
    """)
    conn.execute("DELETE FROM sent_filings WHERE sent_at < ?",
                 (int(time.time()) - 30*86400,))
    conn.commit()
    log.info("Database ready ✅")
    return conn

def make_hash(source, ticker, title):
    return hashlib.sha256(
        f"{source}:{ticker}:{title.strip().lower()}".encode()
    ).hexdigest()

def is_duplicate(conn, source, ticker, title):
    return conn.execute(
        "SELECT 1 FROM sent_filings WHERE hash=?",
        (make_hash(source, ticker, title),)
    ).fetchone() is not None

def mark_sent(conn, source, ticker, title):
    conn.execute(
        "INSERT OR IGNORE INTO sent_filings VALUES (?,?,?,?,?)",
        (make_hash(source, ticker, title), ticker, title[:200],
         source, int(time.time()))
    )
    conn.commit()

def log_error(conn, msg):
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
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer":         "https://www.nseindia.com/",
        })
        self.warmed = False
        self._last_warm = 0

    def warm(self):
        try:
            self.s.get("https://www.nseindia.com/", timeout=15)
            time.sleep(2)
            self.s.get(
                "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                timeout=12
            )
            time.sleep(1)
            self.warmed = True
            self._last_warm = time.time()
            log.info("NSE session warmed ✅")
        except Exception as e:
            log.warning(f"NSE warmup failed: {e}")

    def get(self, url):
        if not self.warmed or (time.time() - self._last_warm > 1800):
            self.warm()
        try:
            r = self.s.get(url, timeout=15)
            if r.status_code == 401:
                log.warning("NSE 401 — re-warming")
                self.warmed = False
                self.warm()
                r = self.s.get(url, timeout=15)
            return r
        except Exception as e:
            log.debug(f"NSE GET failed: {e}")
            return None

nse = NSESession()

BSE_H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json, text/plain, */*",
    "Referer":    "https://www.bseindia.com/",
    "Origin":     "https://www.bseindia.com",
}

# ─────────────────────────────────────────────────────────────────────────────
# FETCHERS
# ─────────────────────────────────────────────────────────────────────────────
def fetch_nse(symbol):
    if not symbol:
        return []
    url = (f"https://www.nseindia.com/api/corporate-announcements"
           f"?index=equities&symbol={symbol}")
    r = nse.get(url)
    if not r or not r.ok:
        return []
    try:
        filings = []
        for ann in r.json()[:20]:
            title   = (ann.get("desc") or ann.get("sm_name") or "").strip()
            cat_raw = (ann.get("subject") or ann.get("Categorycode") or "").strip()
            date_s  = ann.get("sort_date") or ann.get("an_dt") or ""
            attach  = ann.get("attchmnt") or ""
            if not title:
                continue
            link = (
                f"https://nsearchives.nseindia.com/corporate/xbrl/{attach}"
                if attach else
                f"https://www.nseindia.com/companies-listing/"
                f"corporate-filings-announcements?symbol={symbol}"
            )
            filings.append(dict(
                title=title, link=link,
                category=cat_raw, date=date_s
            ))
        return filings
    except Exception as e:
        log.debug(f"NSE parse {symbol}: {e}")
        return []

def fetch_bse(bse_code):
    if not bse_code:
        return []
    filings = []
    for dur in ["D", "W"]:
        try:
            url = (f"https://api.bseindia.com/BseIndiaAPI/api/"
                   f"AnnGetAnnouncementDet/w?scripcd={bse_code}&dur={dur}")
            r = requests.get(url, headers=BSE_H, timeout=15)
            if not r.ok:
                continue
            for ann in (r.json().get("Table") or [])[:20]:
                title   = (ann.get("HEADLINE") or ann.get("NEWSSUB") or "").strip()
                cat_raw = (ann.get("CATEGORYNAME") or "").strip()
                date_s  = ann.get("NEWS_DT") or ann.get("DTIME") or ""
                attach  = ann.get("ATTACHMENTNAME") or ""
                if not title:
                    continue
                link = (
                    f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{attach}"
                    if attach else
                    f"https://www.bseindia.com/corporates/ann.html?scripcd={bse_code}"
                )
                filings.append(dict(
                    title=title, link=link,
                    category=cat_raw, date=date_s
                ))
            if dur == "D" and filings:
                break
        except Exception as e:
            log.debug(f"BSE {bse_code}: {e}")
    return filings

def fetch_bse_actions(bse_code):
    if not bse_code:
        return []
    try:
        url = (f"https://api.bseindia.com/BseIndiaAPI/api/"
               f"DefaultData/w?scripcd={bse_code}&type=CA")
        r = requests.get(url, headers=BSE_H, timeout=12)
        if not r.ok:
            return []
        filings = []
        for row in (r.json().get("Table") or [])[:5]:
            purpose = (row.get("PURPOSE") or "").strip()
            if not purpose:
                continue
            ex_date  = row.get("EX_DATE") or row.get("EXDATE") or ""
            rec_date = row.get("REC_DATE") or ""
            title = purpose
            if ex_date:  title += f" | Ex-Date: {ex_date}"
            if rec_date: title += f" | Record Date: {rec_date}"
            link = (f"https://www.bseindia.com/stock-share-price/"
                    f"corporate-actions/{bse_code}")
            filings.append(dict(
                title=title, link=link,
                category="Corporate Action", date=ex_date
            ))
        return filings
    except Exception as e:
        log.debug(f"BSE actions {bse_code}: {e}")
        return []

# ─────────────────────────────────────────────────────────────────────────────
# GEMINI AI
# ─────────────────────────────────────────────────────────────────────────────
GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/"
              "models/gemini-1.5-flash:generateContent")

def gemini_analyze(title, company, sector, category):
    if not GEMINI_API_KEY:
        return None
    prompt = f"""You are an expert Indian stock market analyst helping a retail investor.

Filing: "{title}"
Company: {company}
Sector: {sector}
Filing Type: {category}

Analyze this NSE/BSE official filing. Respond ONLY with this JSON (no markdown, no backticks):
{{
  "summary": "2-3 plain English sentences explaining what this means for a retail investor",
  "sentiment": "bullish OR bearish OR neutral",
  "impact": "high OR medium OR low",
  "action": "BUY MORE OR HOLD OR WATCH OR REDUCE OR AVOID",
  "reason": "One sentence: why this action makes sense right now"
}}

Be direct and specific. Focus on what the investor should DO."""

    try:
        r = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 300
                }
            },
            timeout=20
        )
        if not r.ok:
            log.warning(f"Gemini error {r.status_code}: {r.text[:200]}")
            return None

        resp  = r.json()
        parts = resp.get("candidates",[{}])[0].get("content",{}).get("parts",[{}])
        text  = parts[0].get("text","").strip()
        text  = re.sub(r"```json\n?|```", "", text).strip()

        m = re.search(r"\{[\s\S]+?\}", text)
        if not m:
            log.warning(f"Gemini returned no JSON: {text[:100]}")
            return None

        result = json.loads(m.group())
        result["sentiment"] = result.get("sentiment","neutral").lower()
        result["impact"]    = result.get("impact","medium").lower()
        result["action"]    = result.get("action","WATCH").upper()
        if result["sentiment"] not in ["bullish","bearish","neutral"]:
            result["sentiment"] = "neutral"
        if result["impact"] not in ["high","medium","low"]:
            result["impact"] = "medium"
        return result

    except json.JSONDecodeError as e:
        log.warning(f"Gemini JSON parse error: {e}")
        return None
    except Exception as e:
        log.warning(f"Gemini unexpected error: {e}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────────────────────────
SENT_E = {"bullish":"🟢","bearish":"🔴","neutral":"🟡"}
IMP_E  = {"high":"🔥","medium":"⚡","low":"💧"}
ACT_E  = {
    "BUY MORE":"🚀","HOLD":"✋","WATCH":"👀",
    "REDUCE":"⚠️","AVOID":"🚫"
}

def now_ist():
    return datetime.now(IST).strftime("%d %b %Y · %I:%M %p IST")

def build_message(stock, source, filing, cat_label, importance, ai):
    cat_emoji = "📊" if stock["cat"] == "PORTFOLIO" else "👁"
    imp_tag   = {
        "HIGH":   "🔴 HIGH",
        "MEDIUM": "🟡 MEDIUM",
        "LOW":    "🟢 LOW"
    }.get(importance, "🟡 MEDIUM")

    lines = [
        f"{'━'*22}",
        f"🏛 <b>{source} OFFICIAL FILING</b>",
        f"{'━'*22}",
        f"{cat_emoji} <b>{stock['cat']}</b>  ·  <code>{stock['ticker']}</code>",
        f"🏢 <b>{stock['name']}</b>",
        f"🏭 {stock['sector']}  ·  Priority: {imp_tag}",
        f"🏷 {cat_label}",
        "",
        f"📄 <b>{filing['title']}</b>",
        "",
    ]

    if ai:
        lines += [
            f"🤖 <b>AI Analysis (Gemini)</b>",
            f"{'─'*20}",
            f"📝 {ai.get('summary','')}",
            "",
            f"{SENT_E.get(ai['sentiment'],'🟡')} Sentiment: <b>{ai['sentiment'].capitalize()}</b>",
            f"{IMP_E.get(ai['impact'],'⚡')} Impact: <b>{ai['impact'].capitalize()}</b>",
            f"{ACT_E.get(ai['action'],'👀')} Signal: <b>{ai['action']}</b>",
            f"💡 {ai.get('reason','')}",
            "",
        ]
    else:
        lines += [
            "⚠️ <i>AI analysis unavailable — check GEMINI_API_KEY in Railway</i>",
            "",
        ]

    if filing.get("date"):
        lines.append(f"📅 Filed: {filing['date']}")

    lines += [
        f"🔗 <a href=\"{filing['link']}\">View on {source}</a>",
        f"⏰ {now_ist()}",
    ]
    return "\n".join(lines)

def send_telegram(text, retries=3):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(text)
        return False
    for attempt in range(retries):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={
                    "chat_id":    CHAT_ID,
                    "text":       text[:4000],
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=12
            )
            if r.ok:
                return True
            if r.status_code == 429:
                wait = r.json().get("parameters",{}).get("retry_after", 30)
                log.warning(f"Telegram rate limit — waiting {wait}s")
                time.sleep(wait)
                continue
            log.error(f"Telegram {r.status_code}: {r.text[:150]}")
            return False
        except requests.exceptions.Timeout:
            log.warning(f"Telegram timeout attempt {attempt+1}")
            time.sleep(5)
        except Exception as e:
            log.error(f"Telegram error: {e}")
            return False
    return False

# ─────────────────────────────────────────────────────────────────────────────
# PROCESS ONE STOCK
# ─────────────────────────────────────────────────────────────────────────────
def process_stock(stock, conn):
    sent = 0
    ticker = stock["ticker"]
    all_filings = []

    if stock.get("nse"):
        for f in fetch_nse(stock["nse"]):
            all_filings.append(("NSE", f))
        time.sleep(0.8)

    if stock.get("bse"):
        for f in fetch_bse(stock["bse"]):
            all_filings.append(("BSE", f))
        for f in fetch_bse_actions(stock["bse"]):
            all_filings.append(("BSE", f))
        time.sleep(0.5)

    for source, filing in all_filings:
        title   = filing["title"]
        cat_raw = filing.get("category", "")
        date_s  = filing.get("date", "")

        # ── KEY FIX: skip old filings ──────────────────────────────────
        if not is_recent_filing(date_s, FILING_MAX_AGE_DAYS):
            log.debug(f"  Skipping old filing [{ticker}]: {date_s[:10]} — {title[:50]}")
            continue

        # Classify
        cat_label, importance = classify(title, cat_raw)
        if cat_label is None:
            continue

        # Deduplicate
        if is_duplicate(conn, source, ticker, title):
            continue
        mark_sent(conn, source, ticker, title)

        log.info(f"  [{source}] [{ticker}] [{importance}] {title[:70]}")

        # AI analysis
        ai = gemini_analyze(title, stock["name"], stock["sector"], cat_label)

        # Send
        msg = build_message(stock, source, filing, cat_label, importance, ai)
        if send_telegram(msg):
            sent += 1
            time.sleep(1.5)

    return sent

# ─────────────────────────────────────────────────────────────────────────────
# MAIN CYCLE
# ─────────────────────────────────────────────────────────────────────────────
def run_cycle(conn):
    log.info(f"━━ Cycle {datetime.now(IST).strftime('%H:%M:%S IST')} ━━")
    total = 0
    for stock in ALL_STOCKS:
        try:
            total += process_stock(stock, conn)
        except Exception as e:
            msg = f"Error {stock['ticker']}: {e}"
            log.error(msg)
            log_error(conn, msg)
    log.info(f"━━ Done. {total} alerts sent ━━\n")
    return total

# ─────────────────────────────────────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────────────────────────────────────
def send_startup():
    msg = (
        f"{'━'*22}\n"
        f"🚀 <b>StockPilot Bot v3.2 — Live!</b>\n"
        f"{'━'*22}\n"
        f"⏰ {datetime.now(IST).strftime('%d %b %Y · %I:%M %p IST')}\n\n"
        f"📊 <b>Portfolio ({len(PORTFOLIO)}):</b>\n"
        f"<code>{' · '.join(s['ticker'] for s in PORTFOLIO)}</code>\n\n"
        f"👁 <b>Watchlist ({len(WATCHLIST)}):</b>\n"
        f"<code>{' · '.join(s['ticker'] for s in WATCHLIST)}</code>\n\n"
        f"<b>✅ v3.2 fixes:</b>\n"
        f"  • Old filings filtered (only last {FILING_MAX_AGE_DAYS} days sent)\n"
        f"  • IZMO NSE + BSE codes corrected\n"
        f"  • Gemini error handling improved\n\n"
        f"<b>🤖 AI:</b> {'Google Gemini ✅' if GEMINI_API_KEY else '⚠️ Add GEMINI_API_KEY in Railway Variables'}\n"
        f"<b>🔄 Check interval:</b> every {CHECK_INTERVAL//60} min\n"
        f"<b>📅 Filing age filter:</b> last {FILING_MAX_AGE_DAYS} days only\n"
        f"{'━'*22}\n"
        f"Only fresh, relevant filings from now on. 📡"
    )
    send_telegram(msg)

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def main():
    validate_config()
    log.info("StockPilot Bot v3.2 starting…")
    conn = init_db()
    nse.warm()
    send_startup()

    consecutive_errors = 0
    while True:
        try:
            run_cycle(conn)
            consecutive_errors = 0
        except KeyboardInterrupt:
            log.info("Stopped.")
            break
        except Exception as e:
            consecutive_errors += 1
            log.error(f"Cycle error #{consecutive_errors}: {e}", exc_info=True)
            log_error(conn, msg=str(e))
            if consecutive_errors >= 5:
                send_telegram(
                    f"⚠️ <b>StockPilot Warning</b>\n"
                    f"5 errors in a row.\nLast: {str(e)[:200]}\n"
                    f"Still running — retrying."
                )
                consecutive_errors = 0
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
