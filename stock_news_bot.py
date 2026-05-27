#!/usr/bin/env python3
"""
StockPilot NSE/BSE Filing Bot v3
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Source: NSE + BSE OFFICIAL FILINGS ONLY
AI: Google Gemini (Free — 1M tokens/day)
Delivery: Telegram
Zero noise. Only what matters.
"""

import os, time, sqlite3, hashlib, json, re, logging, sys
from datetime import datetime
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
# CONFIGURATION — set as Railway environment variables
# ─────────────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID         = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "").strip()
CHECK_INTERVAL  = int(os.environ.get("CHECK_INTERVAL", "300"))  # 5 min default
DB_PATH         = os.environ.get("DB_PATH", "filings.db")
IST             = pytz.timezone("Asia/Kolkata")

# Validate required credentials on startup
def validate_config():
    missing = []
    if not TELEGRAM_TOKEN: missing.append("TELEGRAM_TOKEN")
    if not CHAT_ID:        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        log.error(f"Missing required env vars: {', '.join(missing)}")
        sys.exit(1)
    if not GEMINI_API_KEY:
        log.warning("GEMINI_API_KEY not set — AI summaries disabled. Get free key at aistudio.google.com")

# ─────────────────────────────────────────────────────────────────────────────
# YOUR STOCKS  (NSE symbol + BSE scrip code + company details)
# ─────────────────────────────────────────────────────────────────────────────
PORTFOLIO = [
    dict(ticker="ADVAIT",        name="Advait Infratech",         nse="ADVAIT",        bse="543259", sector="Infrastructure",       cat="PORTFOLIO"),
    dict(ticker="ANANTRAJ",      name="Anant Raj Ltd",            nse="ANANTRAJ",      bse="515055", sector="Real Estate",           cat="PORTFOLIO"),
    dict(ticker="APOLLO",        name="Apollo Micro Systems",      nse="APOLLOMICRO",   bse="543288", sector="Defence Electronics",   cat="PORTFOLIO"),
    dict(ticker="BEL",           name="Bharat Electronics",        nse="BEL",           bse="500049", sector="Defence",               cat="PORTFOLIO"),
    dict(ticker="CDSL",          name="CDSL",                      nse="CDSL",          bse="543272", sector="Financial Services",     cat="PORTFOLIO"),
    dict(ticker="HAL",           name="Hindustan Aeronautics",     nse="HAL",           bse="541154", sector="Defence",               cat="PORTFOLIO"),
    dict(ticker="HAPPSTMNDS",    name="Happiest Minds",            nse="HAPPSTMNDS",    bse="543227", sector="IT",                    cat="PORTFOLIO"),
    dict(ticker="IFCI",          name="IFCI Ltd",                  nse="IFCI",          bse="500106", sector="NBFC",                  cat="PORTFOLIO"),
    dict(ticker="INOXINDIA",     name="INOX India",                nse="INOXINDIA",     bse="544010", sector="Industrial Gas",        cat="PORTFOLIO"),
    dict(ticker="IZMO",          name="Izmo Ltd",                  nse=None,            bse="532804", sector="Auto Technology",       cat="PORTFOLIO"),
    dict(ticker="KPEL",          name="K.P. Energy",               nse="KPEL",          bse="540698", sector="Renewable Energy",      cat="PORTFOLIO"),
    dict(ticker="NETWEB",        name="Netweb Technologies",       nse="NETWEB",        bse="544112", sector="IT Hardware",           cat="PORTFOLIO"),
    dict(ticker="PENIND",        name="Pen Industries",            nse="PENIND",        bse="523260", sector="Media",                 cat="PORTFOLIO"),
    dict(ticker="PGEL",          name="PG Electroplast",           nse="PGEL",          bse="543594", sector="Electronics",           cat="PORTFOLIO"),
    dict(ticker="REMSONSIND",    name="Remsons Industries",        nse="REMSONSIND",    bse="517437", sector="Automobile",            cat="PORTFOLIO"),
    dict(ticker="RVNL",          name="Rail Vikas Nigam",          nse="RVNL",          bse="542649", sector="Railways & Infra",      cat="PORTFOLIO"),
]

WATCHLIST = [
    dict(ticker="JAINRESOUR",   name="Jain Resource Recycl",      nse=None,            bse="533289", sector="Recycling",             cat="WATCHLIST"),
    dict(ticker="IREDA",        name="Indian Renewable Energy",   nse="IREDA",         bse="544124", sector="Renewable Energy",      cat="WATCHLIST"),
    dict(ticker="IZMOWATCH",    name="Izmo Ltd",                  nse=None,            bse="532804", sector="Auto Technology",       cat="WATCHLIST"),
    dict(ticker="ONEGLOBAL",    name="One Global Service",        nse="ONEGLOBAL",     bse=None,     sector="Services",              cat="WATCHLIST"),
    dict(ticker="DOMS",         name="DOMS Industries",           nse="DOMS",          bse="544045", sector="Consumer",              cat="WATCHLIST"),
    dict(ticker="LANCER",       name="Lancer Container",          nse=None,            bse="526807", sector="Packaging",             cat="WATCHLIST"),
]

ALL_STOCKS = PORTFOLIO + WATCHLIST

# Filing categories we care about — skip purely routine ones
IMPORTANT_CATEGORIES = {
    # High importance — always send
    "Result":                    ("📊 Financial Result",          "HIGH"),
    "Board Meeting":             ("🗓 Board Meeting",             "HIGH"),
    "Dividend":                  ("💰 Dividend",                  "HIGH"),
    "Bonus":                     ("🎁 Bonus Shares",              "HIGH"),
    "Split":                     ("✂️ Stock Split",               "HIGH"),
    "Buyback":                   ("♻️ Buyback",                   "HIGH"),
    "Merger":                    ("🔀 Merger/Acquisition",        "HIGH"),
    "Acquisition":               ("🔀 Merger/Acquisition",        "HIGH"),
    "Amalgamation":              ("🔀 Amalgamation",              "HIGH"),
    "Scheme":                    ("📋 Scheme of Arrangement",     "HIGH"),
    "Rights":                    ("📝 Rights Issue",              "HIGH"),
    "Order":                     ("🏆 Order/Contract Win",        "HIGH"),
    "Contract":                  ("🏆 Order/Contract Win",        "HIGH"),
    "Basmati":                   ("📢 General Announcement",      "MEDIUM"),
    "General":                   ("📢 General Announcement",      "MEDIUM"),
    "Spurt":                     ("📈 Volume Spurt",              "MEDIUM"),
    "Price":                     ("📈 Price Movement",            "MEDIUM"),
    "AGM":                       ("🏛 AGM/EGM",                  "MEDIUM"),
    "EGM":                       ("🏛 AGM/EGM",                  "MEDIUM"),
    "Appointment":               ("👤 Board Change",              "MEDIUM"),
    "Cessation":                 ("👤 Board Change",              "MEDIUM"),
    "Change in Management":      ("👤 Management Change",         "MEDIUM"),
    "Insider":                   ("🔍 Insider Trading",           "MEDIUM"),
    "Analyst":                   ("📊 Analyst Meet",              "MEDIUM"),
    "Investor":                  ("📊 Investor Presentation",     "MEDIUM"),
    "Press Release":             ("📰 Press Release",             "MEDIUM"),
    "Update":                    ("📢 Business Update",           "MEDIUM"),
    "Litigation":                ("⚖️ Litigation",               "MEDIUM"),
    "Compliances":               ("📋 Compliance Filing",         "LOW"),
    "Certificate":               ("📋 Compliance Filing",         "LOW"),
    "Trading Window":            ("🪟 Trading Window",            "LOW"),
    "Newspaper":                 ("📰 Newspaper Publication",     "LOW"),
    "Registrar":                 ("📋 Admin Update",              "LOW"),
    "Record Date":               ("📅 Record Date",               "MEDIUM"),
    "Allotment":                 ("📋 Share Allotment",           "MEDIUM"),
}

# Skip these entirely — pure routine with zero decision value
SKIP_CATEGORIES = [
    "certificate",
    "trading window",
    "newspaper publication",
    "registrar",
    "depository",
    "sebi (depositories",
    "shareholders may want",
    "compliance",
    "reconciliation",
    "loss of share",
]

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE — never send duplicate filings
# ─────────────────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent_filings (
            hash     TEXT PRIMARY KEY,
            ticker   TEXT,
            title    TEXT,
            source   TEXT,
            sent_at  INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS errors (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            msg      TEXT,
            ts       INTEGER
        )
    """)
    # Clean filings older than 30 days
    conn.execute("DELETE FROM sent_filings WHERE sent_at < ?", (int(time.time()) - 30*86400,))
    conn.commit()
    log.info("Database initialised ✅")
    return conn

def make_hash(source: str, ticker: str, title: str) -> str:
    key = f"{source}:{ticker}:{title.strip().lower()}"
    return hashlib.sha256(key.encode()).hexdigest()

def is_duplicate(conn, source, ticker, title) -> bool:
    h = make_hash(source, ticker, title)
    return conn.execute("SELECT 1 FROM sent_filings WHERE hash=?", (h,)).fetchone() is not None

def mark_sent(conn, source, ticker, title):
    h = make_hash(source, ticker, title)
    conn.execute(
        "INSERT OR IGNORE INTO sent_filings VALUES (?,?,?,?,?)",
        (h, ticker, title[:200], source, int(time.time()))
    )
    conn.commit()

def log_error(conn, msg):
    conn.execute("INSERT INTO errors VALUES (NULL,?,?)", (msg[:500], int(time.time())))
    conn.commit()

# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY CLASSIFIER
# ─────────────────────────────────────────────────────────────────────────────
def classify_filing(title: str, category_raw: str = "") -> tuple:
    """Returns (label, importance) or (None, None) if should be skipped."""
    combined = (title + " " + category_raw).lower()

    # Skip check first
    if any(skip in combined for skip in SKIP_CATEGORIES):
        return None, None

    # Match importance categories
    for keyword, (label, importance) in IMPORTANT_CATEGORIES.items():
        if keyword.lower() in combined:
            return label, importance

    # Default — send with medium importance
    return "📢 Corporate Filing", "MEDIUM"

# ─────────────────────────────────────────────────────────────────────────────
# NSE SESSION — NSE requires browser-like session to avoid 401/403
# ─────────────────────────────────────────────────────────────────────────────
class NSESession:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer":         "https://www.nseindia.com/",
            "Connection":      "keep-alive",
            "sec-fetch-site":  "same-origin",
            "sec-fetch-mode":  "cors",
        })
        self.warmed = False

    def warm(self):
        """Visit NSE homepage to get valid cookies."""
        try:
            self.session.get("https://www.nseindia.com/", timeout=15)
            time.sleep(2)
            self.session.get(
                "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                timeout=12
            )
            time.sleep(1)
            self.warmed = True
            log.info("NSE session warmed ✅")
        except Exception as e:
            log.warning(f"NSE warmup failed: {e}")

    def get(self, url, **kwargs):
        if not self.warmed:
            self.warm()
        try:
            r = self.session.get(url, timeout=15, **kwargs)
            if r.status_code == 401:
                # Session expired — re-warm
                self.warmed = False
                self.warm()
                r = self.session.get(url, timeout=15, **kwargs)
            return r
        except Exception as e:
            log.debug(f"NSE GET failed {url}: {e}")
            return None

nse_session = NSESession()

# ─────────────────────────────────────────────────────────────────────────────
# BSE HEADERS
# ─────────────────────────────────────────────────────────────────────────────
BSE_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.bseindia.com/",
    "Origin":          "https://www.bseindia.com",
}

# ─────────────────────────────────────────────────────────────────────────────
# NSE FILINGS FETCHER
# ─────────────────────────────────────────────────────────────────────────────
def fetch_nse_filings(nse_sym: str) -> list:
    """
    Returns list of dicts:
    {title, link, category, date}
    """
    if not nse_sym:
        return []
    url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={nse_sym}"
    r = nse_session.get(url)
    if not r or not r.ok:
        log.debug(f"NSE {nse_sym}: HTTP {r.status_code if r else 'None'}")
        return []
    try:
        data = r.json()
        filings = []
        for ann in data[:20]:
            title    = (ann.get("desc") or ann.get("sm_name") or "").strip()
            cat_raw  = (ann.get("subject") or ann.get("Categorycode") or "").strip()
            date_str = ann.get("sort_date") or ann.get("an_dt") or ""
            attach   = ann.get("attchmnt") or ""
            if not title:
                continue
            if attach:
                link = f"https://nsearchives.nseindia.com/corporate/xbrl/{attach}"
            else:
                link = f"https://www.nseindia.com/companies-listing/corporate-filings-announcements?symbol={nse_sym}"
            filings.append(dict(title=title, link=link, category=cat_raw, date=date_str))
        return filings
    except Exception as e:
        log.debug(f"NSE parse error {nse_sym}: {e}")
        return []

# ─────────────────────────────────────────────────────────────────────────────
# BSE ANNOUNCEMENTS FETCHER
# ─────────────────────────────────────────────────────────────────────────────
def fetch_bse_filings(bse_code: str) -> list:
    if not bse_code:
        return []
    # Fetch both today (D) and week (W) to avoid missing filings
    filings = []
    for dur in ["D", "W"]:
        url = (
            f"https://api.bseindia.com/BseIndiaAPI/api/AnnGetAnnouncementDet/w"
            f"?scripcd={bse_code}&dur={dur}"
        )
        try:
            r = requests.get(url, headers=BSE_HEADERS, timeout=15)
            if not r.ok:
                continue
            rows = r.json().get("Table") or []
            for ann in rows[:20]:
                title   = (ann.get("HEADLINE") or ann.get("NEWSSUB") or "").strip()
                cat_raw = (ann.get("CATEGORYNAME") or "").strip()
                date_str = ann.get("NEWS_DT") or ann.get("DTIME") or ""
                attach  = ann.get("ATTACHMENTNAME") or ""
                if not title:
                    continue
                if attach:
                    link = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{attach}"
                else:
                    link = f"https://www.bseindia.com/corporates/ann.html?scripcd={bse_code}"
                filings.append(dict(title=title, link=link, category=cat_raw, date=date_str))
            if dur == "D" and filings:
                break  # today's filings found — no need for week
        except Exception as e:
            log.debug(f"BSE parse error {bse_code}: {e}")
    return filings

# ─────────────────────────────────────────────────────────────────────────────
# BSE CORPORATE ACTIONS (dividends, bonus, splits — separate API)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_bse_corp_actions(bse_code: str) -> list:
    if not bse_code:
        return []
    url = f"https://api.bseindia.com/BseIndiaAPI/api/DefaultData/w?scripcd={bse_code}&type=CA"
    try:
        r = requests.get(url, headers=BSE_HEADERS, timeout=12)
        if not r.ok:
            return []
        filings = []
        for row in (r.json().get("Table") or [])[:5]:
            purpose  = (row.get("PURPOSE") or "").strip()
            if not purpose:
                continue
            ex_date  = row.get("EX_DATE") or row.get("EXDATE") or ""
            rec_date = row.get("REC_DATE") or ""
            title = purpose
            if ex_date:  title += f" | Ex-Date: {ex_date}"
            if rec_date: title += f" | Record Date: {rec_date}"
            link = f"https://www.bseindia.com/stock-share-price/corporate-actions/{bse_code}"
            filings.append(dict(title=title, link=link, category="Corporate Action", date=ex_date))
        return filings
    except Exception as e:
        log.debug(f"BSE corp actions {bse_code}: {e}")
        return []

# ─────────────────────────────────────────────────────────────────────────────
# GEMINI AI — FREE, no credit card, 1M tokens/day
# ─────────────────────────────────────────────────────────────────────────────
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"

def gemini_analyze(filing_title: str, company: str, sector: str, category: str) -> dict:
    """
    Returns {
        summary: str,        # 2-3 line plain english summary
        sentiment: str,      # bullish / bearish / neutral
        impact: str,         # high / medium / low
        action: str,         # BUY MORE / HOLD / WATCH / SELL / AVOID
        reason: str          # 1 line decision reason
    }
    Returns None if Gemini not configured.
    """
    if not GEMINI_API_KEY:
        return None

    prompt = f"""You are an expert Indian stock market analyst helping a retail investor make decisions.

Filing: "{filing_title}"
Company: {company}
Sector: {sector}
Filing Type: {category}

Analyze this NSE/BSE official filing and respond ONLY with this exact JSON (no markdown, no backticks):
{{
  "summary": "2-3 plain English sentences explaining what this filing means for a retail investor",
  "sentiment": "bullish OR bearish OR neutral",
  "impact": "high OR medium OR low",
  "action": "BUY MORE OR HOLD OR WATCH OR REDUCE OR AVOID",
  "reason": "One sentence: why this action makes sense right now"
}}

Be specific, direct, and helpful. Focus on what the investor should DO."""

    try:
        r = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 300,
                }
            },
            timeout=15
        )
        if not r.ok:
            log.debug(f"Gemini error {r.status_code}: {r.text[:200]}")
            return None

        text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Strip markdown if present
        text = re.sub(r"```json\n?", "", text)
        text = re.sub(r"```", "", text).strip()
        # Extract JSON
        m = re.search(r"\{[\s\S]+?\}", text)
        if m:
            result = json.loads(m.group())
            # Validate & normalise
            result["sentiment"] = result.get("sentiment", "neutral").lower().strip()
            result["impact"]    = result.get("impact", "medium").lower().strip()
            result["action"]    = result.get("action", "WATCH").upper().strip()
            if result["sentiment"] not in ["bullish","bearish","neutral"]:
                result["sentiment"] = "neutral"
            if result["impact"] not in ["high","medium","low"]:
                result["impact"] = "medium"
            return result
        return None
    except Exception as e:
        log.debug(f"Gemini parse error: {e}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
# MESSAGE FORMATTER
# ─────────────────────────────────────────────────────────────────────────────
SENTIMENT_EMOJI = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}
IMPACT_EMOJI    = {"high": "🔥", "medium": "⚡", "low": "💧"}
ACTION_EMOJI    = {
    "BUY MORE": "🚀", "HOLD": "✋", "WATCH": "👀",
    "REDUCE": "⚠️", "AVOID": "🚫"
}

def now_ist() -> str:
    return datetime.now(IST).strftime("%d %b %Y · %I:%M %p IST")

def format_filing_message(stock: dict, source: str, filing: dict,
                           cat_label: str, importance: str, ai: dict) -> str:
    cat_emoji = "📊" if stock["cat"] == "PORTFOLIO" else "👁"
    imp_tag   = {"HIGH": "🔴 HIGH", "MEDIUM": "🟡 MEDIUM", "LOW": "🟢 LOW"}.get(importance, "🟡 MEDIUM")

    lines = [
        f"{'━'*22}",
        f"🏛 <b>{'NSE' if source == 'NSE' else 'BSE'} OFFICIAL FILING</b>",
        f"{'━'*22}",
        f"{cat_emoji} <b>{stock['cat']}</b>  ·  <code>{stock['ticker']}</code>",
        f"🏢 <b>{stock['name']}</b>  |  🏭 {stock['sector']}",
        f"🏷 {cat_label}  ·  📌 Priority: {imp_tag}",
        "",
        f"📄 <b>Filing:</b> {filing['title']}",
        "",
    ]

    if ai:
        s_e = SENTIMENT_EMOJI.get(ai["sentiment"], "🟡")
        i_e = IMPACT_EMOJI.get(ai["impact"], "⚡")
        a_e = ACTION_EMOJI.get(ai["action"], "👀")

        lines += [
            f"🤖 <b>AI Analysis (Gemini)</b>",
            f"{'─'*20}",
            f"📝 {ai.get('summary', '')}",
            "",
            f"{s_e} <b>Sentiment:</b> {ai['sentiment'].capitalize()}",
            f"{i_e} <b>Market Impact:</b> {ai['impact'].capitalize()}",
            f"{a_e} <b>Action Signal:</b> <b>{ai['action']}</b>",
            f"💡 {ai.get('reason', '')}",
            "",
        ]
    else:
        lines += ["⚠️ <i>AI analysis unavailable (set GEMINI_API_KEY for insights)</i>", ""]

    if filing.get("date"):
        lines.append(f"📅 Filed: {filing['date']}")

    lines += [
        f"🔗 <a href=\"{filing['link']}\">View Official Filing on {source}</a>",
        f"⏰ {now_ist()}",
        f"{'━'*22}",
    ]
    return "\n".join(lines)

# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM SENDER with retry
# ─────────────────────────────────────────────────────────────────────────────
def send_telegram(text: str, retries: int = 3) -> bool:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(text)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, timeout=12)
            if r.ok:
                return True
            if r.status_code == 429:  # Rate limit
                retry_after = r.json().get("parameters", {}).get("retry_after", 30)
                log.warning(f"Telegram rate limit — sleeping {retry_after}s")
                time.sleep(retry_after)
                continue
            if r.status_code == 400:
                # Message too long — truncate and retry
                payload["text"] = text[:3800] + "\n\n<i>[Message truncated]</i>"
                continue
            log.error(f"Telegram {r.status_code}: {r.text[:200]}")
            return False
        except requests.exceptions.Timeout:
            log.warning(f"Telegram timeout (attempt {attempt+1})")
            time.sleep(5)
        except Exception as e:
            log.error(f"Telegram error: {e}")
            return False
    return False

# ─────────────────────────────────────────────────────────────────────────────
# MAIN PROCESSING CYCLE
# ─────────────────────────────────────────────────────────────────────────────
def process_stock(stock: dict, conn) -> int:
    """Process one stock — returns number of alerts sent."""
    sent = 0
    ticker = stock["ticker"]

    # Collect filings from both NSE and BSE
    sources = []
    if stock.get("nse"):
        for f in fetch_nse_filings(stock["nse"]):
            sources.append(("NSE", f))
        time.sleep(0.8)  # Respectful rate limiting for NSE

    if stock.get("bse"):
        for f in fetch_bse_filings(stock["bse"]):
            sources.append(("BSE", f))
        for f in fetch_bse_corp_actions(stock["bse"]):
            sources.append(("BSE", f))
        time.sleep(0.5)

    for source, filing in sources:
        title = filing["title"]
        cat_raw = filing.get("category", "")

        # Classify — skip routine filings
        cat_label, importance = classify_filing(title, cat_raw)
        if cat_label is None:
            continue  # Skip routine filings

        # Deduplication
        if is_duplicate(conn, source, ticker, title):
            continue

        # Mark as seen immediately to prevent duplicates across NSE+BSE
        mark_sent(conn, source, ticker, title)

        log.info(f"  [{source}] [{ticker}] [{importance}] {title[:70]}")

        # AI analysis via Gemini
        ai = gemini_analyze(title, stock["name"], stock["sector"], cat_label)

        # Format and send
        msg = format_filing_message(stock, source, filing, cat_label, importance, ai)
        if send_telegram(msg):
            sent += 1
            time.sleep(1.5)  # Avoid Telegram flood limits

    return sent

def run_cycle(conn) -> int:
    now_str = datetime.now(IST).strftime("%H:%M:%S IST")
    log.info(f"━━ Cycle start {now_str} ━━")
    total_sent = 0

    for stock in ALL_STOCKS:
        try:
            n = process_stock(stock, conn)
            total_sent += n
        except Exception as e:
            msg = f"Error processing {stock['ticker']}: {e}"
            log.error(msg)
            log_error(conn, msg)

    log.info(f"━━ Cycle done. {total_sent} alerts sent ━━\n")
    return total_sent

# ─────────────────────────────────────────────────────────────────────────────
# STARTUP MESSAGE
# ─────────────────────────────────────────────────────────────────────────────
def send_startup_message():
    port_list  = " · ".join(s["ticker"] for s in PORTFOLIO)
    watch_list = " · ".join(s["ticker"] for s in WATCHLIST)
    now_str    = datetime.now(IST).strftime("%d %b %Y · %I:%M %p IST")

    msg = (
        f"{'━'*22}\n"
        f"🚀 <b>StockPilot Filing Bot v3</b>\n"
        f"{'━'*22}\n"
        f"⏰ {now_str}\n\n"
        f"📊 <b>Portfolio ({len(PORTFOLIO)} stocks):</b>\n"
        f"<code>{port_list}</code>\n\n"
        f"👁 <b>Watchlist ({len(WATCHLIST)} stocks):</b>\n"
        f"<code>{watch_list}</code>\n\n"
        f"<b>📡 Data Sources:</b>\n"
        f"  🏛 NSE Corporate Announcements (official API)\n"
        f"  🏛 BSE Corporate Announcements (official API)\n"
        f"  🏦 BSE Corporate Actions (dividends/bonus/splits)\n\n"
        f"<b>🧠 AI Engine:</b> {'✅ Google Gemini (Free)' if GEMINI_API_KEY else '⚠️ Disabled — add GEMINI_API_KEY'}\n\n"
        f"<b>🚫 Filtered Out:</b> All news websites, RSS feeds,\n"
        f"routine compliance filings, duplicate alerts\n\n"
        f"<b>✅ You will ONLY receive:</b>\n"
        f"  • NSE/BSE official filings\n"
        f"  • With AI summary + sentiment\n"
        f"  • With BUY/HOLD/WATCH/REDUCE signal\n\n"
        f"🔄 Checking every {CHECK_INTERVAL//60} min\n"
        f"{'━'*22}\n"
        f"Watching your stocks. 📡"
    )
    send_telegram(msg)

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def main():
    validate_config()
    log.info("StockPilot Filing Bot v3 starting…")
    conn = init_db()

    # Warm NSE session before first cycle
    nse_session.warm()

    send_startup_message()

    consecutive_errors = 0
    while True:
        try:
            run_cycle(conn)
            consecutive_errors = 0
        except KeyboardInterrupt:
            log.info("Stopped by user.")
            break
        except Exception as e:
            consecutive_errors += 1
            log.error(f"Cycle error #{consecutive_errors}: {e}", exc_info=True)
            log_error(conn, str(e))
            if consecutive_errors >= 5:
                send_telegram(
                    "⚠️ <b>StockPilot Bot Warning</b>\n"
                    f"5 consecutive errors. Last: {str(e)[:200]}\n"
                    "Bot will keep retrying."
                )
                consecutive_errors = 0

        log.info(f"Sleeping {CHECK_INTERVAL}s until next check…")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
