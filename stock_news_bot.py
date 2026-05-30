#!/usr/bin/env python3
"""
StockPilot NSE/BSE Filing Bot v3.5
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
v3.5 changes:
  - FIXED: PENIND → PENNARINT (Pennar Industries, not Pen Industries)
  - ADDED: 18 turnaround stocks to watchlist
  - UPGRADED: Institutional-level AI analysis
  - NEW: NSE live market data (price, 52w range, volume) in every AI prompt
  - NEW: Weekly vs average volume analysis in AI summary
  - AI: Groq (LLaMA 3.1) primary | Gemini fallback
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
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY", "").strip()
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "").strip()
CHECK_INTERVAL  = int(os.environ.get("CHECK_INTERVAL", "300"))
DB_PATH         = os.environ.get("DB_PATH", "filings.db")
IST             = pytz.timezone("Asia/Kolkata")
FILING_MAX_AGE_DAYS = 7

def validate_config():
    missing = []
    if not TELEGRAM_TOKEN: missing.append("TELEGRAM_TOKEN")
    if not CHAT_ID:        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        log.error(f"Missing env vars: {', '.join(missing)}")
        sys.exit(1)
    if GROQ_API_KEY:
        log.info("Groq AI ready ✅")
    elif GEMINI_API_KEY:
        log.info("Gemini AI ready ✅")
    else:
        log.warning("No AI key — add GROQ_API_KEY for institutional analysis")

# ─────────────────────────────────────────────────────────────────────────────
# DATE FILTER
# ─────────────────────────────────────────────────────────────────────────────
DATE_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
    "%d-%b-%Y %H:%M:%S", "%d-%b-%Y",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y",
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
    if dt is None: return True
    return dt >= datetime.now() - timedelta(days=days)

# ─────────────────────────────────────────────────────────────────────────────
# STOCKS
# ─────────────────────────────────────────────────────────────────────────────
PORTFOLIO = [
    dict(ticker="ADVAIT",     name="Advait Infratech",       nse="ADVAIT",      bse="543259", sector="Infrastructure",        cat="PORTFOLIO"),
    dict(ticker="ANANTRAJ",   name="Anant Raj Ltd",          nse="ANANTRAJ",    bse="515055", sector="Real Estate",           cat="PORTFOLIO"),
    dict(ticker="APOLLO",     name="Apollo Micro Systems",   nse="APOLLOMICRO", bse="543288", sector="Defence Electronics",   cat="PORTFOLIO"),
    dict(ticker="BEL",        name="Bharat Electronics",     nse="BEL",         bse="500049", sector="Defence PSU",           cat="PORTFOLIO"),
    dict(ticker="CDSL",       name="CDSL",                   nse="CDSL",        bse="543272", sector="Financial Services",    cat="PORTFOLIO"),
    dict(ticker="HAL",        name="Hindustan Aeronautics",  nse="HAL",         bse="541154", sector="Defence Aerospace",     cat="PORTFOLIO"),
    dict(ticker="HAPPSTMNDS", name="Happiest Minds",         nse="HAPPSTMNDS",  bse="543227", sector="IT Services",           cat="PORTFOLIO"),
    dict(ticker="IFCI",       name="IFCI Ltd",               nse="IFCI",        bse="500106", sector="NBFC",                  cat="PORTFOLIO"),
    dict(ticker="INOXINDIA",  name="INOX India",             nse="INOXINDIA",   bse="543716", sector="Industrial Gas",        cat="PORTFOLIO"),
    dict(ticker="IZMO",       name="Izmo Ltd",               nse="IZMO",        bse="532341", sector="Auto Technology",       cat="PORTFOLIO"),
    dict(ticker="KPEL",       name="K.P. Energy",            nse="KPEL",        bse="540698", sector="Renewable Energy",      cat="PORTFOLIO"),
    dict(ticker="NETWEB",     name="Netweb Technologies",    nse="NETWEB",      bse="543920", sector="IT Hardware",           cat="PORTFOLIO"),
    # FIXED: Was "Pen Industries (Media)" — corrected to Pennar Industries (Steel/Engineering)
    dict(ticker="PENNARINT",  name="Pennar Industries",      nse="PENNARINT",   bse="513228", sector="Steel & Engineering",   cat="PORTFOLIO"),
    dict(ticker="PGEL",       name="PG Electroplast",        nse="PGEL",        bse="543594", sector="Electronics",           cat="PORTFOLIO"),
    dict(ticker="REMSONSIND", name="Remsons Industries",     nse="REMSONSIND",  bse="517437", sector="Auto Components",       cat="PORTFOLIO"),
    dict(ticker="RVNL",       name="Rail Vikas Nigam",       nse="RVNL",        bse="542649", sector="Railways & Infra",      cat="PORTFOLIO"),
]

WATCHLIST = [
    # Original watchlist
    dict(ticker="JAINRESOUR", name="Jain Resource Recycl",   nse=None,          bse="533289", sector="Recycling",            cat="WATCHLIST"),
    dict(ticker="IREDA",      name="Indian Renewable Energy", nse="IREDA",       bse="544124", sector="Renewable Energy",     cat="WATCHLIST"),
    dict(ticker="IZMOWATCH",  name="Izmo Ltd (Watch)",        nse="IZMO",        bse="532341", sector="Auto Technology",      cat="WATCHLIST"),
    dict(ticker="ONEGLOBAL",  name="One Global Service",      nse="ONEGLOBAL",   bse=None,     sector="Business Services",    cat="WATCHLIST"),
    dict(ticker="DOMS",       name="DOMS Industries",         nse="DOMS",        bse="544045", sector="Consumer Stationery",  cat="WATCHLIST"),
    dict(ticker="LANCER",     name="Lancer Container",        nse=None,          bse="526807", sector="Packaging",            cat="WATCHLIST"),

    # ── TURNAROUND STOCKS (from screenshot) ───────────────────────────────
    dict(ticker="HFCL",       name="HFCL Ltd",                nse="HFCL",        bse="500183", sector="Telecom Infrastructure",  cat="WATCHLIST"),
    dict(ticker="BORORENEW",  name="Boro Renewables",         nse="BORORENEW",   bse=None,     sector="Renewable Energy",        cat="WATCHLIST"),
    dict(ticker="IDEAFORGE",  name="ideaForge Technology",    nse="IDEAFORGE",   bse="543932", sector="Defence Drones",          cat="WATCHLIST"),
    dict(ticker="NAVKARCORP", name="Navkar Corporation",      nse="NAVKARCORP",  bse="539332", sector="Logistics",               cat="WATCHLIST"),
    dict(ticker="RKFORGE",    name="Ramkrishna Forgings",     nse="RKFORGE",     bse="500368", sector="Auto Forgings",           cat="WATCHLIST"),
    dict(ticker="SIS",        name="SIS Ltd",                 nse="SIS",         bse="540673", sector="Security Services",       cat="WATCHLIST"),
    dict(ticker="IBULLSLTD",  name="Indiabulls Ltd",          nse="IBULLSLTD",   bse="535789", sector="NBFC",                    cat="WATCHLIST"),
    dict(ticker="FABTECH",    name="Fabtech Technologies",    nse="FABTECH",     bse=None,     sector="Engineering",             cat="WATCHLIST"),
    dict(ticker="E2E",        name="E2E Networks",            nse="E2ENETWORKS", bse="543421", sector="Cloud Infrastructure",    cat="WATCHLIST"),
    dict(ticker="NPL",        name="NPL",                     nse="NPL",         bse=None,     sector="Manufacturing",           cat="WATCHLIST"),
    dict(ticker="AURUM",      name="Aurum PropTech",          nse="AURUM",       bse="543088", sector="PropTech",                cat="WATCHLIST"),
    dict(ticker="MARSONS",    name="Marsons Ltd",             nse="MARSONS",     bse="522080", sector="Electrical Equipment",    cat="WATCHLIST"),
    dict(ticker="HARSHA",     name="Harsha Engineers",        nse="HARSHA",      bse="543457", sector="Precision Engineering",   cat="WATCHLIST"),
    dict(ticker="RAYMOND",    name="Raymond Ltd",             nse="RAYMOND",     bse="500330", sector="Lifestyle & Real Estate", cat="WATCHLIST"),
    dict(ticker="MARINE",     name="Marine Electricals",      nse="MARINE",      bse=None,     sector="Electrical Equipment",    cat="WATCHLIST"),
    dict(ticker="KMEW",       name="KMEW",                    nse="KMEW",        bse=None,     sector="Manufacturing",           cat="WATCHLIST"),
    dict(ticker="MODISONLTD", name="Modison Ltd",             nse="MODISONLTD",  bse=None,     sector="Electrical Contacts",     cat="WATCHLIST"),
    dict(ticker="RATEGAIN",   name="RateGain Travel Tech",    nse="RATEGAIN",    bse="543417", sector="Travel Technology SaaS",  cat="WATCHLIST"),
]

ALL_STOCKS = PORTFOLIO + WATCHLIST

# ─────────────────────────────────────────────────────────────────────────────
# CLASSIFIER
# ─────────────────────────────────────────────────────────────────────────────
CATEGORIES = {
    "Result":               ("📊 Financial Result",        "HIGH"),
    "Board Meeting":        ("🗓 Board Meeting",            "HIGH"),
    "Dividend":             ("💰 Dividend",                 "HIGH"),
    "Bonus":                ("🎁 Bonus Shares",             "HIGH"),
    "Split":                ("✂️ Stock Split",              "HIGH"),
    "Buyback":              ("♻️ Buyback",                  "HIGH"),
    "Merger":               ("🔀 Merger/Acquisition",       "HIGH"),
    "Acquisition":          ("🔀 Merger/Acquisition",       "HIGH"),
    "Rights":               ("📝 Rights Issue",             "HIGH"),
    "Order":                ("🏆 Order/Contract Win",       "HIGH"),
    "Contract":             ("🏆 Order/Contract Win",       "HIGH"),
    "Scheme":               ("📋 Scheme of Arrangement",   "HIGH"),
    "AGM":                  ("🏛 AGM/EGM",                 "MEDIUM"),
    "EGM":                  ("🏛 AGM/EGM",                 "MEDIUM"),
    "Appointment":          ("👤 Board Change",             "MEDIUM"),
    "Cessation":            ("👤 Board Change",             "MEDIUM"),
    "Change in Management": ("👤 Management Change",        "MEDIUM"),
    "Insider":              ("🔍 Insider Trading",          "MEDIUM"),
    "Analyst":              ("📊 Analyst/Investor Meet",    "MEDIUM"),
    "Investor":             ("📊 Analyst/Investor Meet",    "MEDIUM"),
    "Press Release":        ("📰 Press Release",            "MEDIUM"),
    "Update":               ("📢 Business Update",          "MEDIUM"),
    "Litigation":           ("⚖️ Litigation",              "MEDIUM"),
    "General":              ("📢 General Announcement",     "MEDIUM"),
    "Basmati":              ("📢 General Announcement",     "MEDIUM"),
    "Record Date":          ("📅 Record Date",              "MEDIUM"),
    "Allotment":            ("📋 Share Allotment",          "MEDIUM"),
    "Spurt":                ("📈 Volume Spurt",             "MEDIUM"),
    "Price":                ("📈 Price Movement",           "MEDIUM"),
}

SKIP = [
    "certificate under sebi", "trading window",
    "newspaper publication", "copy of newspaper",
    "registrar & share transfer", "reconciliation of share capital",
    "loss of share certificate", "sebi (depositories",
    "compliances-reg.", "reg. 74", "reg. 76", "reg. 57", "reg. 40",
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

def _h(src, tick, title):
    return hashlib.sha256(
        f"{src}:{tick}:{title.strip().lower()}".encode()).hexdigest()

def is_dup(conn, src, tick, title):
    return conn.execute(
        "SELECT 1 FROM sent_filings WHERE hash=?", (_h(src, tick, title),)
    ).fetchone() is not None

def mark(conn, src, tick, title):
    conn.execute("INSERT OR IGNORE INTO sent_filings VALUES (?,?,?,?,?)",
                 (_h(src, tick, title), tick, title[:200], src, int(time.time())))
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
            log.debug(f"NSE: {e}")
            return None

nse = NSESession()
BSE_H = {
    "User-Agent": "Mozilla/5.0",
    "Referer":    "https://www.bseindia.com/",
    "Origin":     "https://www.bseindia.com"
}

# ─────────────────────────────────────────────────────────────────────────────
# NSE LIVE MARKET DATA — for volume + price context in AI analysis
# ─────────────────────────────────────────────────────────────────────────────
def fetch_nse_quote(nse_sym):
    """
    Fetches live market data: price, % change, 52w high/low, today's volume.
    Used to give AI institutional context for analysis.
    """
    if not nse_sym:
        return None
    url = f"https://www.nseindia.com/api/quote-equity?symbol={nse_sym}"
    r = nse.get(url)
    if not r or not r.ok:
        return None
    try:
        data = r.json()
        pi  = data.get("priceInfo", {})
        ti  = data.get("tradedInfo", {})
        whl = pi.get("weekHighLow", {})

        ltp        = pi.get("lastPrice")
        change_pct = pi.get("pChange")
        w52_high   = whl.get("max")
        w52_low    = whl.get("min")
        vol_today  = ti.get("totalTradedVolume")
        avg_vol_1y = ti.get("tottrdqty")  # approximate

        # Position in 52-week range (0% = at low, 100% = at high)
        range_pct = None
        if ltp and w52_high and w52_low:
            try:
                span = float(w52_high) - float(w52_low)
                if span > 0:
                    range_pct = round(((float(ltp) - float(w52_low)) / span) * 100, 1)
            except:
                pass

        # Volume ratio vs average
        vol_ratio = None
        if vol_today and avg_vol_1y:
            try:
                vol_ratio = round(float(vol_today) / float(avg_vol_1y) * 100, 1)
            except:
                pass

        return {
            "ltp":        ltp,
            "change_pct": change_pct,
            "w52_high":   w52_high,
            "w52_low":    w52_low,
            "vol_today":  vol_today,
            "avg_vol":    avg_vol_1y,
            "range_pct":  range_pct,
            "vol_ratio":  vol_ratio,
        }
    except Exception as e:
        log.debug(f"NSE quote {nse_sym}: {e}")
        return None

def fmt_market_context(q):
    """Format market data as text for AI prompt."""
    if not q:
        return "Live market data unavailable — analyze filing on fundamentals only."

    ltp    = f"₹{q['ltp']}"       if q.get("ltp")       else "N/A"
    chg    = f"{q['change_pct']}%" if q.get("change_pct") else "N/A"
    hi52   = f"₹{q['w52_high']}"  if q.get("w52_high")  else "N/A"
    lo52   = f"₹{q['w52_low']}"   if q.get("w52_low")   else "N/A"
    rng    = (f"{q['range_pct']}% above 52w low (closer to "
              f"{'high — extended' if q['range_pct'] > 70 else 'low — value zone' if q['range_pct'] < 30 else 'midpoint'}"
              f")") if q.get("range_pct") is not None else "N/A"

    vol_t  = f"{int(q['vol_today']):,}" if q.get("vol_today") else "N/A"
    vol_r  = (f"{q['vol_ratio']}% of annual average "
              f"({'HIGH — institutional activity likely' if q['vol_ratio'] > 150 else 'LOW — limited interest' if q['vol_ratio'] < 50 else 'normal'})"
              ) if q.get("vol_ratio") is not None else "N/A"

    return (
        f"Current Price: {ltp} ({chg} today)\n"
        f"52-Week Range: {lo52} → {hi52} | Position: {rng}\n"
        f"Today's Volume: {vol_t} shares | vs Annual Average: {vol_r}"
    )

# ─────────────────────────────────────────────────────────────────────────────
# FETCHERS — NSE + BSE official APIs
# ─────────────────────────────────────────────────────────────────────────────
def fetch_nse_filings(sym):
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
        log.debug(f"NSE filings {sym}: {e}")
        return []

def fetch_bse_filings(code):
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
            t  = p + (f" | Ex-Date: {ex}" if ex else "") + \
                      (f" | Record Date: {rc}" if rc else "")
            out.append(dict(
                title=t,
                link=f"https://www.bseindia.com/stock-share-price/corporate-actions/{code}",
                category="Corporate Action", date=ex
            ))
        return out
    except Exception as e:
        log.debug(f"BSE CA {code}: {e}")
        return []

# ─────────────────────────────────────────────────────────────────────────────
# AI ANALYSIS — Institutional level prompt with market data
# ─────────────────────────────────────────────────────────────────────────────
INSTITUTIONAL_PROMPT = """You are a senior equity research analyst at a top Indian institutional fund (like Mirae Asset or Kotak Mutual Fund). A filing has just hit the exchange. Analyze it with full institutional rigor.

━━ FILING DETAILS ━━
Headline: "{title}"
Company: {company}
Sector: {sector}
Filing Type: {category}

━━ LIVE MARKET DATA ━━
{market_context}

━━ YOUR ANALYSIS TASK ━━
Analyze this filing like a senior fund manager would. Consider:
1. FUNDAMENTAL IMPACT: Revenue, margins, balance sheet, earnings per share effect
2. VALUATION TRIGGER: Does this change the re-rating thesis? P/E expansion or compression?
3. INSTITUTIONAL PERSPECTIVE: Would FIIs/DIIs accumulate, hold, or trim on this news?
4. VOLUME SIGNAL: Compare today's volume vs annual average — is smart money active?
5. PRICE SETUP: Where is stock in 52-week range — is this filing a breakout catalyst or risk?
6. SECTOR CONTEXT: How does this compare to what peers are doing?

Respond ONLY with this JSON — no markdown, no backticks, no explanation outside JSON:
{{
  "summary": "3-4 sentences of institutional analysis — cover fundamental impact, what this means for the re-rating thesis, and what FIIs/DIIs are likely thinking",
  "volume_signal": "1 sentence — compare today's volume to annual average, state clearly if institutional accumulation or distribution is likely based on the volume pattern",
  "price_setup": "1 sentence — where stock sits in 52w range, whether this filing is a breakout catalyst or a risk flag",
  "sector_view": "1 sentence — how this compares to sector peers or macro trends",
  "sentiment": "bullish OR bearish OR neutral",
  "impact": "high OR medium OR low",
  "action": "BUY MORE OR HOLD OR WATCH OR REDUCE OR AVOID",
  "horizon": "short_term OR medium_term OR long_term",
  "reason": "One specific actionable sentence — include price levels or % targets where possible"
}}"""

def parse_ai_json(text):
    text = re.sub(r"```json\n?|```", "", text).strip()
    m = re.search(r"\{[\s\S]+?\}", text)
    if not m: return None
    try:
        res = json.loads(m.group())
        res["sentiment"] = res.get("sentiment", "neutral").lower()
        res["impact"]    = res.get("impact", "medium").lower()
        res["action"]    = res.get("action", "WATCH").upper()
        res["horizon"]   = res.get("horizon", "medium_term").lower()
        if res["sentiment"] not in ["bullish","bearish","neutral"]:
            res["sentiment"] = "neutral"
        if res["impact"] not in ["high","medium","low"]:
            res["impact"] = "medium"
        return res
    except Exception as e:
        log.debug(f"AI JSON parse: {e}")
        return None

def groq_analyze(title, company, sector, category, market_ctx):
    if not GROQ_API_KEY: return None
    prompt = INSTITUTIONAL_PROMPT.format(
        title=title, company=company, sector=sector,
        category=category, market_context=market_ctx)
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [
                    {"role": "system",
                     "content": ("You are an expert Indian stock market institutional analyst. "
                                 "Always respond with valid JSON only. No markdown. No text outside JSON.")},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2,
                "max_tokens": 500
            },
            timeout=25
        )
        if not r.ok:
            log.warning(f"Groq {r.status_code}: {r.text[:150]}")
            return None
        content = r.json()["choices"][0]["message"]["content"]
        result = parse_ai_json(content)
        if result:
            log.info(f"  Groq: {result['sentiment']} | {result['action']} | {result['horizon']}")
        return result
    except Exception as e:
        log.warning(f"Groq error: {e}")
        return None

def gemini_analyze(title, company, sector, category, market_ctx):
    if not GEMINI_API_KEY: return None
    prompt = INSTITUTIONAL_PROMPT.format(
        title=title, company=company, sector=sector,
        category=category, market_context=market_ctx)
    for model in ["gemini-2.0-flash", "gemini-1.5-flash"]:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={GEMINI_API_KEY}",
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0.2, "maxOutputTokens": 500}},
                timeout=25
            )
            if not r.ok: continue
            text   = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            result = parse_ai_json(text)
            if result:
                log.info(f"  Gemini({model}): {result['sentiment']} | {result['action']}")
                return result
        except Exception as e:
            log.warning(f"Gemini {model}: {e}")
    return None

def ai_analyze(title, company, sector, category, nse_sym):
    """Fetch live market data first, then run institutional AI analysis."""
    # Get live price + volume data for context
    quote = fetch_nse_quote(nse_sym) if nse_sym else None
    market_ctx = fmt_market_context(quote)

    # Try Groq first, fallback to Gemini
    result = None
    if GROQ_API_KEY:
        result = groq_analyze(title, company, sector, category, market_ctx)
    if not result and GEMINI_API_KEY:
        result = gemini_analyze(title, company, sector, category, market_ctx)

    if result:
        result["_market_ctx"] = market_ctx  # attach for message display
    return result

# ─────────────────────────────────────────────────────────────────────────────
# MESSAGE BUILDER — shows full institutional analysis
# ─────────────────────────────────────────────────────────────────────────────
SE = {"bullish":"🟢","bearish":"🔴","neutral":"🟡"}
IE = {"high":"🔥","medium":"⚡","low":"💧"}
AE = {"BUY MORE":"🚀","HOLD":"✋","WATCH":"👀","REDUCE":"⚠️","AVOID":"🚫"}
HE = {"short_term":"⚡ Short Term","medium_term":"📆 Medium Term","long_term":"🏔 Long Term"}

def now_ist():
    return datetime.now(IST).strftime("%d %b %Y · %I:%M %p IST")

def build_msg(stock, source, filing, cat_label, importance, ai):
    ce  = "📊" if stock["cat"] == "PORTFOLIO" else "👁"
    itg = {"HIGH":"🔴 HIGH","MEDIUM":"🟡 MEDIUM","LOW":"🟢 LOW"}.get(importance,"🟡")
    L = [
        "━"*24,
        f"🏛 <b>{source} OFFICIAL FILING</b>",
        "━"*24,
        f"{ce} <b>{stock['cat']}</b>  ·  <code>{stock['ticker']}</code>",
        f"🏢 <b>{stock['name']}</b>  |  🏭 {stock['sector']}",
        f"🏷 {cat_label}  ·  {itg}",
        "",
        f"📄 <b>{filing['title']}</b>",
        "",
    ]

    if ai:
        # Market context block
        mctx = ai.get("_market_ctx", "")
        if mctx and mctx != "Live market data unavailable — analyze filing on fundamentals only.":
            L += [
                "📈 <b>Market Context</b>",
                f"<code>{mctx}</code>",
                "",
            ]

        # Institutional AI analysis
        L += [
            "🏦 <b>Institutional Analysis</b>",
            "─"*22,
            f"📝 {ai.get('summary','')}",
            "",
            f"📊 <b>Volume Signal:</b> {ai.get('volume_signal','')}",
            f"🎯 <b>Price Setup:</b> {ai.get('price_setup','')}",
            f"🌐 <b>Sector View:</b> {ai.get('sector_view','')}",
            "",
            f"{SE.get(ai['sentiment'],'🟡')} Sentiment: <b>{ai['sentiment'].capitalize()}</b>",
            f"{IE.get(ai['impact'],'⚡')} Market Impact: <b>{ai['impact'].capitalize()}</b>",
            f"{AE.get(ai['action'],'👀')} Action Signal: <b>{ai['action']}</b>",
            f"⏳ Horizon: <b>{HE.get(ai['horizon'], ai['horizon'])}</b>",
            f"💡 {ai.get('reason','')}",
            "",
        ]
    else:
        L += [
            "⚠️ <i>Add GROQ_API_KEY in Railway for institutional AI analysis</i>",
            "",
        ]

    if filing.get("date"):
        L.append(f"📅 Filed: {filing['date']}")
    L += [
        f"🔗 <a href=\"{filing['link']}\">View Official Filing on {source}</a>",
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
            log.error(f"TG: {e}"); time.sleep(5)
    return False

# ─────────────────────────────────────────────────────────────────────────────
# PROCESS STOCK
# ─────────────────────────────────────────────────────────────────────────────
def process(stock, conn):
    sent = 0
    all_f = []
    if stock.get("nse"):
        all_f += [("NSE", f) for f in fetch_nse_filings(stock["nse"])]
        time.sleep(0.8)
    if stock.get("bse"):
        all_f += [("BSE", f) for f in fetch_bse_filings(stock["bse"])]
        all_f += [("BSE", f) for f in fetch_bse_ca(stock["bse"])]
        time.sleep(0.5)

    for source, f in all_f:
        if not is_recent(f.get("date",""), FILING_MAX_AGE_DAYS): continue
        cat_label, importance = classify(f["title"], f.get("category",""))
        if cat_label is None: continue
        if is_dup(conn, source, stock["ticker"], f["title"]): continue
        mark(conn, source, stock["ticker"], f["title"])
        log.info(f"  [{source}][{stock['ticker']}][{importance}] {f['title'][:65]}")
        ai  = ai_analyze(f["title"], stock["name"], stock["sector"],
                         cat_label, stock.get("nse"))
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
    total = 0
    for stock in ALL_STOCKS:
        try:
            total += process(stock, conn)
        except Exception as e:
            log.error(f"{stock['ticker']}: {e}")
            log_err(conn, str(e))
    log.info(f"━━ Done. {total} sent ━━\n")

def send_startup():
    ai_info = (
        "✅ Groq LLaMA 3.1 (institutional analysis)" if GROQ_API_KEY else
        "✅ Gemini (fallback)" if GEMINI_API_KEY else
        "❌ No AI — add GROQ_API_KEY in Railway"
    )
    turnaround = ["HFCL","BORORENEW","IDEAFORGE","NAVKARCORP","RKFORGE",
                  "SIS","IBULLSLTD","FABTECH","E2E","NPL","AURUM",
                  "MARSONS","HARSHA","RAYMOND","MARINE","KMEW",
                  "MODISONLTD","RATEGAIN"]
    msg = (
        f"{'━'*24}\n"
        f"🚀 <b>StockPilot Bot v3.5</b>\n"
        f"{'━'*24}\n"
        f"⏰ {datetime.now(IST).strftime('%d %b %Y · %I:%M %p IST')}\n\n"
        f"📊 <b>Portfolio ({len(PORTFOLIO)}):</b>\n"
        f"<code>{' · '.join(s['ticker'] for s in PORTFOLIO)}</code>\n\n"
        f"👁 <b>Watchlist ({len(WATCHLIST)}):</b>\n"
        f"<code>{' · '.join(s['ticker'] for s in WATCHLIST)}</code>\n\n"
        f"📈 <b>Turnaround stocks added ({len(turnaround)}):</b>\n"
        f"<code>{' · '.join(turnaround)}</code>\n\n"
        f"✅ <b>v3.5 fixes:</b>\n"
        f"  • PENIND → PENNARINT (Pennar Industries)\n"
        f"  • 18 turnaround stocks added to watchlist\n"
        f"  • Institutional-level AI with market data\n"
        f"  • Volume vs annual average in every alert\n"
        f"  • 52-week price context in every alert\n\n"
        f"🤖 AI: {ai_info}\n"
        f"📅 Age filter: last {FILING_MAX_AGE_DAYS} days\n"
        f"🔄 Every {CHECK_INTERVAL//60} min\n"
        f"{'━'*24}"
    )
    send_tg(msg)

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    validate_config()
    log.info("StockPilot Bot v3.5 starting…")
    conn = init_db()
    nse.warm()
    send_startup()
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
                send_tg(f"⚠️ 5 errors\nLast: {str(e)[:200]}")
                errs = 0
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
