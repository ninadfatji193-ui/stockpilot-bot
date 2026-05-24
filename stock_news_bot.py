#!/usr/bin/env python3
"""
StockPilot News Bot v2
━━━━━━━━━━━━━━━━━━━━━━
Real-time Indian stock news → Telegram
Sources: NSE Official · BSE Official · Google News · Yahoo Finance · ET Markets
Free • No paid APIs required for core features
"""

import os, time, sqlite3, hashlib, json, re, logging
from datetime import datetime
from urllib.parse import quote_plus

import pytz
import feedparser
import requests

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("StockPilot")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "300"))
DB_PATH        = os.environ.get("DB_PATH", "seen_news.db")
IST            = pytz.timezone("Asia/Kolkata")

# ─────────────────────────────────────────────────────────────────────────────
# STOCKS — nse_sym for NSE API, bse_code for BSE API
# ─────────────────────────────────────────────────────────────────────────────
PORTFOLIO = [
    dict(ticker="ADVAIT",     name="Advait Infratech",        nse_sym="ADVAIT",     bse_code="543259", sector="Infrastructure"),
    dict(ticker="ANANTRAJ",   name="Anant Raj Ltd",           nse_sym="ANANTRAJ",   bse_code="515055", sector="Real Estate"),
    dict(ticker="APOLLOTYRE", name="Apollo Tyres",            nse_sym="APOLLOTYRE", bse_code="500877", sector="Automobile"),
    dict(ticker="BEL",        name="Bharat Electronics",      nse_sym="BEL",        bse_code="500049", sector="Defence"),
    dict(ticker="CDSL",       name="CDSL",                    nse_sym="CDSL",       bse_code="543272", sector="Financial Services"),
    dict(ticker="HAL",        name="Hindustan Aeronautics",   nse_sym="HAL",        bse_code="541154", sector="Defence"),
    dict(ticker="HAPPSTMNDS", name="Happiest Minds",          nse_sym="HAPPSTMNDS", bse_code="543227", sector="IT"),
    dict(ticker="IFCI",       name="IFCI Ltd",                nse_sym="IFCI",       bse_code="500106", sector="NBFC"),
    dict(ticker="INOXINDIA",  name="INOX India",              nse_sym="INOXINDIA",  bse_code="544010", sector="Industrial Gas"),
    dict(ticker="IZMO",       name="Izmo Ltd",                nse_sym=None,         bse_code="532804", sector="Auto Technology"),
    dict(ticker="KPEL",       name="K.P. Energy",             nse_sym="KPEL",       bse_code="540698", sector="Renewable Energy"),
    dict(ticker="NETWEB",     name="Netweb Technologies",     nse_sym="NETWEB",     bse_code="544112", sector="IT Hardware"),
    dict(ticker="PENIND",     name="PENIND",                  nse_sym="PENIND",     bse_code="523260", sector="Media"),
    dict(ticker="PGEL",       name="PG Electroplast",         nse_sym="PGEL",       bse_code="543594", sector="Electronics"),
    dict(ticker="REMSONSIND", name="Remsons Industries",      nse_sym="REMSONSIND", bse_code="517437", sector="Automobile"),
    dict(ticker="RVNL",       name="Rail Vikas Nigam",        nse_sym="RVNL",       bse_code="542649", sector="Railways & Infrastructure"),
]

WATCHLIST = [
    dict(ticker="JAINRESOUR", name="Jain Resource Recycl",   nse_sym=None,         bse_code="533289", sector="Recycling"),
    dict(ticker="IREDA",      name="Indian Renewable Energy", nse_sym="IREDA",      bse_code="544124", sector="Renewable Energy"),
    dict(ticker="DOMS",       name="DOMS Industries",         nse_sym="DOMS",       bse_code="544045", sector="Consumer"),
    dict(ticker="LANCER",     name="Lancer Container",        nse_sym=None,         bse_code="526807", sector="Packaging"),
    dict(ticker="ONEGLOBAL",  name="One Global Service",      nse_sym="ONEGLOBAL",  bse_code=None,     sector="Services"),
]

SECTOR_THEMES = [
    dict(label="Defence Budget & Orders",    keywords=["defence budget","defence order","ministry of defence","HAL order","BEL order","DRDO"]),
    dict(label="Railways & Infra Capex",     keywords=["railway budget","RVNL","rail vikas","infra capex","PM Gati Shakti"]),
    dict(label="Renewable Energy Policy",    keywords=["solar policy","wind energy policy","renewable energy India","green hydrogen","IREDA","MNRE"]),
    dict(label="IT & Technology Outlook",    keywords=["IT sector India","IT hiring","rupee dollar","US recession IT","data center India"]),
    dict(label="SEBI / Exchange Circulars",  keywords=["SEBI notification","SEBI circular","NSE circular","BSE circular","SEBI order"]),
    dict(label="Budget & PLI Schemes",       keywords=["union budget","capex allocation","PLI scheme","Make in India"]),
    dict(label="Auto Sector Trends",         keywords=["auto sector India","EV policy","automobile demand","auto sales","EV subsidy"]),
]

ALL_STOCKS = [dict(cat="PORTFOLIO", **s) for s in PORTFOLIO] + [dict(cat="WATCHLIST", **s) for s in WATCHLIST]

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (hash TEXT PRIMARY KEY, title TEXT, ts INTEGER)")
    conn.execute("DELETE FROM seen WHERE ts < ?", (int(time.time()) - 7*86400,))
    conn.commit()
    return conn

def already_seen(conn, key):
    h = hashlib.md5(key.encode()).hexdigest()
    return conn.execute("SELECT 1 FROM seen WHERE hash=?", (h,)).fetchone() is not None

def mark_seen(conn, key, title=""):
    h = hashlib.md5(key.encode()).hexdigest()
    conn.execute("INSERT OR IGNORE INTO seen VALUES (?,?,?)", (h, title, int(time.time())))
    conn.commit()

# ─────────────────────────────────────────────────────────────────────────────
# NSE SESSION  (NSE blocks plain requests — need browser-like session + cookies)
# ─────────────────────────────────────────────────────────────────────────────
_nse_session = None

def get_nse_session():
    global _nse_session
    if _nse_session:
        return _nse_session
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/",
    })
    try:
        s.get("https://www.nseindia.com/", timeout=12)
        time.sleep(1.5)
        s.get("https://www.nseindia.com/companies-listing/corporate-filings-announcements", timeout=10)
        time.sleep(1)
        _nse_session = s
        log.info("NSE session ready ✅")
    except Exception as e:
        log.warning(f"NSE session warmup failed: {e}")
        _nse_session = s
    return _nse_session

# ─────────────────────────────────────────────────────────────────────────────
# ① NSE CORPORATE ANNOUNCEMENTS  (official exchange filings)
# ─────────────────────────────────────────────────────────────────────────────
NSE_CAT_MAP = {
    "Result": "📊 Financial Result", "Board Meeting": "🗓 Board Meeting",
    "Dividend": "💰 Dividend", "Split": "✂️ Stock Split", "Bonus": "🎁 Bonus Shares",
    "Insider": "🔍 Insider Trading", "AGM": "🏛 AGM/EGM", "Buyback": "♻️ Buyback",
}

def fetch_nse_announcements(nse_sym):
    if not nse_sym:
        return []
    session = get_nse_session()
    url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={nse_sym}"
    try:
        r = session.get(url, timeout=12)
        if not r.ok:
            return []
        items = []
        for ann in r.json()[:15]:
            headline = (ann.get("desc") or ann.get("sm_name") or "").strip()
            if not headline:
                continue
            attach = ann.get("attchmnt") or ""
            link = f"https://nsearchives.nseindia.com/corporate/xbrl/{attach}" if attach else \
                   f"https://www.nseindia.com/companies-listing/corporate-filings-announcements?symbol={nse_sym}"
            cat_raw = ann.get("subject") or ann.get("Categorycode") or "General"
            cat_label = next((v for k, v in NSE_CAT_MAP.items() if k.lower() in cat_raw.lower()), "📢 Corporate Filing")
            items.append((headline, link, ann.get("sort_date",""), cat_label))
        return items
    except Exception as e:
        log.debug(f"NSE ann error {nse_sym}: {e}")
        return []

# ─────────────────────────────────────────────────────────────────────────────
# ② BSE CORPORATE ANNOUNCEMENTS  (official exchange filings)
# ─────────────────────────────────────────────────────────────────────────────
BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
}

def fetch_bse_announcements(bse_code):
    if not bse_code:
        return []
    url = f"https://api.bseindia.com/BseIndiaAPI/api/AnnGetAnnouncementDet/w?scripcd={bse_code}&dur=D"
    try:
        r = requests.get(url, headers=BSE_HEADERS, timeout=12)
        if not r.ok:
            return []
        items = []
        for ann in (r.json().get("Table") or [])[:15]:
            headline = (ann.get("HEADLINE") or ann.get("NEWSSUB") or "").strip()
            if not headline:
                continue
            attach = ann.get("ATTACHMENTNAME") or ""
            link = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{attach}" if attach else \
                   f"https://www.bseindia.com/corporates/ann.html?scripcd={bse_code}"
            cat = ann.get("CATEGORYNAME") or "Corporate Filing"
            items.append((headline, link, ann.get("NEWS_DT",""), f"📋 {cat}"))
        return items
    except Exception as e:
        log.debug(f"BSE ann error {bse_code}: {e}")
        return []

# ─────────────────────────────────────────────────────────────────────────────
# ③ BSE CORPORATE ACTIONS  (dividends, bonus, splits)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_bse_corp_actions(bse_code):
    if not bse_code:
        return []
    url = f"https://api.bseindia.com/BseIndiaAPI/api/DefaultData/w?scripcd={bse_code}&type=CA"
    try:
        r = requests.get(url, headers=BSE_HEADERS, timeout=10)
        if not r.ok:
            return []
        items = []
        for row in (r.json().get("Table") or [])[:5]:
            purpose = (row.get("PURPOSE") or "").strip()
            if not purpose:
                continue
            ex_date = row.get("EX_DATE") or row.get("EXDATE") or ""
            rec_date = row.get("REC_DATE") or ""
            headline = purpose
            if ex_date:  headline += f" | Ex-Date: {ex_date}"
            if rec_date: headline += f" | Record Date: {rec_date}"
            link = f"https://www.bseindia.com/stock-share-price/corporate-actions/{bse_code}"
            items.append((headline, link, ex_date, "🏦 Corporate Action"))
        return items
    except Exception as e:
        log.debug(f"BSE corp action error {bse_code}: {e}")
        return []

# ─────────────────────────────────────────────────────────────────────────────
# ④ NSE EXCHANGE CIRCULARS  (market-wide regulatory notices)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_nse_circulars():
    session = get_nse_session()
    try:
        r = session.get("https://www.nseindia.com/api/circulars", timeout=12)
        if not r.ok:
            return []
        data = r.json()
        rows = data.get("data") or (data if isinstance(data, list) else [])
        items = []
        for c in rows[:10]:
            title = (c.get("subject") or c.get("circular_subject") or "").strip()
            if not title:
                continue
            cid  = c.get("circular_id") or c.get("id") or ""
            link = f"https://www.nseindia.com/regulatory/circulars/{cid}" if cid else "https://www.nseindia.com/regulatory/circulars"
            items.append((title, link, c.get("circular_date",""), "🔔 NSE Circular"))
        return items
    except Exception as e:
        log.debug(f"NSE circulars error: {e}")
        return []

# ─────────────────────────────────────────────────────────────────────────────
# ⑤ RSS FEEDS  (Google News · Yahoo Finance · ET · Moneycontrol)
# ─────────────────────────────────────────────────────────────────────────────
RSS_HDR = {"User-Agent": "Mozilla/5.0 (compatible; StockPilotBot/2.0)"}

def rss_urls_for_stock(stock):
    n = quote_plus(stock["name"])
    t = quote_plus(stock["ticker"])
    return [
        f"https://news.google.com/rss/search?q={n}+NSE&hl=en-IN&gl=IN&ceid=IN:en",
        f"https://news.google.com/rss/search?q={t}+share+NSE&hl=en-IN&gl=IN&ceid=IN:en",
        f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={stock['ticker']}.NS&region=IN&lang=en-IN",
        f"https://news.google.com/rss/search?q={n}+site:economictimes.indiatimes.com&hl=en-IN&gl=IN&ceid=IN:en",
        f"https://news.google.com/rss/search?q={n}+site:moneycontrol.com&hl=en-IN&gl=IN&ceid=IN:en",
    ]

def rss_urls_for_sector(theme):
    return [
        f"https://news.google.com/rss/search?q={quote_plus(kw)}&hl=en-IN&gl=IN&ceid=IN:en"
        for kw in theme["keywords"][:2]
    ]

def fetch_rss(url):
    try:
        feed = feedparser.parse(url, request_headers=RSS_HDR)
        return [(e.get("title","").strip(), e.get("link","").strip(), e.get("published",""))
                for e in feed.entries[:8] if e.get("title") and e.get("link")]
    except Exception as e:
        log.debug(f"RSS error: {e}")
        return []

# ─────────────────────────────────────────────────────────────────────────────
# AI ANALYSIS  (Claude Haiku)
# ─────────────────────────────────────────────────────────────────────────────
SENT_EMOJI = {"bullish":"🟢","bearish":"🔴","neutral":"🟡"}
IMP_EMOJI  = {"high":"🔥","medium":"⚡","low":"💧"}

def ai_analyze(headline, context):
    if not ANTHROPIC_KEY:
        return None
    try:
        res = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": "claude-haiku-4-5-20251001", "max_tokens": 130,
                "system": "Indian stock market analyst. Respond ONLY with raw JSON, no markdown.",
                "messages": [{"role":"user","content":
                    f"News: \"{headline}\"\nContext: {context}\n\n"
                    "JSON only: {\"sentiment\":\"bullish|bearish|neutral\",\"impact\":\"high|medium|low\",\"reason\":\"one sentence\"}"}],
            }, timeout=12,
        )
        if not res.ok: return None
        text = res.json()["content"][0]["text"].strip()
        m = re.search(r"\{.*?\}", text, re.DOTALL)
        if m:
            j = json.loads(m.group())
            j["sentiment"] = j.get("sentiment","neutral").lower()
            j["impact"]    = j.get("impact","medium").lower()
            if j["sentiment"] not in SENT_EMOJI: j["sentiment"] = "neutral"
            if j["impact"]    not in IMP_EMOJI:  j["impact"]    = "medium"
            return j
    except Exception as e:
        log.debug(f"AI error: {e}")
    return None

# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────────────────────────
def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("\n" + text + "\n")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False},
            timeout=10,
        )
        if not r.ok:
            log.error(f"Telegram {r.status_code}: {r.text[:150]}")
            return False
        return True
    except Exception as e:
        log.error(f"Telegram error: {e}")
        return False

def now_ist():
    return datetime.now(IST).strftime("%I:%M %p IST")

def _ai_block(ai):
    if not ai: return ""
    s = SENT_EMOJI.get(ai["sentiment"],"🟡")
    i = IMP_EMOJI.get(ai["impact"],"⚡")
    return f"\n{s} <b>{ai['sentiment'].capitalize()}</b>  ·  {i} {ai['impact'].capitalize()} Impact\n💡 {ai.get('reason','')}\n"

def fmt_exchange(stock, source, title, link, cat, ai):
    ce = "📊" if stock["cat"]=="PORTFOLIO" else "👁"
    return (
        f"🏛 <b>{source} OFFICIAL FILING</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{ce} <b>{stock['cat']}</b> · <code>{stock['ticker']}</code>  |  {stock['name']}\n"
        f"🏭 {stock['sector']}  ·  🏷 <i>{cat}</i>\n\n"
        f"📌 <b>{title}</b>"
        f"{_ai_block(ai)}\n"
        f"🔗 <a href=\"{link}\">View on {source}</a>  ·  ⏰ {now_ist()}"
    )

def fmt_news(stock, title, link, ai):
    ce = "📊" if stock["cat"]=="PORTFOLIO" else "👁"
    return (
        f"📰 <b>NEWS ALERT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{ce} <b>{stock['cat']}</b> · <code>{stock['ticker']}</code>  |  {stock['name']}\n"
        f"🏭 {stock['sector']}\n\n"
        f"📌 <b>{title}</b>"
        f"{_ai_block(ai)}\n"
        f"🔗 <a href=\"{link}\">Read Article</a>  ·  ⏰ {now_ist()}"
    )

def fmt_sector(theme_label, affected, title, link, ai):
    tickers = ", ".join(f"<code>{s['ticker']}</code>" for s in affected[:6])
    return (
        f"🌐 <b>SECTOR / MACRO NEWS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📂 <b>{theme_label}</b>\n"
        f"🎯 Your stocks: {tickers}\n\n"
        f"📌 <b>{title}</b>"
        f"{_ai_block(ai)}\n"
        f"🔗 <a href=\"{link}\">Read Article</a>  ·  ⏰ {now_ist()}"
    )

def fmt_circular(title, link, ai):
    return (
        f"🔔 <b>NSE EXCHANGE CIRCULAR</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📢 Regulatory / Market-wide notice\n\n"
        f"📌 <b>{title}</b>"
        f"{_ai_block(ai)}\n"
        f"🔗 <a href=\"{link}\">Read Circular</a>  ·  ⏰ {now_ist()}"
    )

# ─────────────────────────────────────────────────────────────────────────────
# RELEVANCE FILTERS
# ─────────────────────────────────────────────────────────────────────────────
NOISE = ["cricket","bollywood","weather","recipe","horoscope","travel","fashion","ipl","football","movie"]

def relevant_rss(title, stock):
    tl = title.lower()
    if any(k in tl for k in NOISE): return False
    return any(c in tl for c in [stock["ticker"].lower(), stock["name"].lower().split()[0], stock["name"].lower().split()[-1]])

def relevant_sector(title, theme):
    tl = title.lower()
    if any(k in tl for k in NOISE): return False
    return any(kw.lower() in tl for kw in theme["keywords"])

# ─────────────────────────────────────────────────────────────────────────────
# MAIN CYCLE
# ─────────────────────────────────────────────────────────────────────────────
def run_cycle(conn):
    log.info(f"── Cycle {datetime.now(IST).strftime('%H:%M:%S IST')} ──")
    sent = 0

    for stock in ALL_STOCKS:
        t = stock["ticker"]

        # ① NSE filings
        for title, link, dt, cat in fetch_nse_announcements(stock.get("nse_sym")):
            key = f"nse:{stock.get('nse_sym')}:{title}"
            if already_seen(conn, key): continue
            mark_seen(conn, key, title)
            ai = ai_analyze(title, f"{stock['name']} NSE filing · {stock['sector']}")
            if send_telegram(fmt_exchange(stock,"NSE",title,link,cat,ai)):
                sent += 1; log.info(f"  NSE [{t}] {title[:65]}")
            time.sleep(1.2)
        time.sleep(1)

        # ② BSE filings
        for title, link, dt, cat in fetch_bse_announcements(stock.get("bse_code")):
            key = f"bse:{stock.get('bse_code')}:{title}"
            if already_seen(conn, key): continue
            mark_seen(conn, key, title)
            ai = ai_analyze(title, f"{stock['name']} BSE filing · {stock['sector']}")
            if send_telegram(fmt_exchange(stock,"BSE",title,link,cat,ai)):
                sent += 1; log.info(f"  BSE [{t}] {title[:65]}")
            time.sleep(1.2)
        time.sleep(1)

        # ③ BSE Corporate Actions
        for title, link, dt, cat in fetch_bse_corp_actions(stock.get("bse_code")):
            key = f"bse_ca:{stock.get('bse_code')}:{title}"
            if already_seen(conn, key): continue
            mark_seen(conn, key, title)
            ai = ai_analyze(title, f"{stock['name']} corporate action")
            if send_telegram(fmt_exchange(stock,"BSE",title,link,cat,ai)):
                sent += 1; log.info(f"  Corp action [{t}] {title[:65]}")
            time.sleep(1)

        # ④ RSS News
        for url in rss_urls_for_stock(stock):
            for title, link, pub in fetch_rss(url):
                if already_seen(conn, link): continue
                if not relevant_rss(title, stock):
                    mark_seen(conn, link, title); continue
                mark_seen(conn, link, title)
                ai = ai_analyze(title, f"{stock['name']} · {stock['sector']} · NSE India")
                if ai and ai["sentiment"]=="neutral" and ai["impact"]=="low": continue
                if send_telegram(fmt_news(stock,title,link,ai)):
                    sent += 1; log.info(f"  News [{t}] {title[:65]}")
                time.sleep(1)
            time.sleep(0.5)

    # ⑤ NSE Circulars
    for title, link, dt, cat in fetch_nse_circulars():
        key = f"circular:{title}"
        if already_seen(conn, key): continue
        mark_seen(conn, key, title)
        ai = ai_analyze(title, "NSE exchange circular Indian market regulatory")
        if ai and ai["impact"]=="low": continue
        if send_telegram(fmt_circular(title,link,ai)):
            sent += 1; log.info(f"  NSE Circular: {title[:65]}")
        time.sleep(1)

    # ⑥ Sector Themes
    for theme in SECTOR_THEMES:
        affected = [s for s in ALL_STOCKS if
                    any(kw.lower() in s["sector"].lower() or kw.lower() in s["name"].lower() for kw in theme["keywords"])
                    or any(s["ticker"].lower() in kw.lower() for kw in theme["keywords"])]
        if not affected: continue
        for url in rss_urls_for_sector(theme):
            for title, link, pub in fetch_rss(url):
                if already_seen(conn, link): continue
                if not relevant_sector(title, theme):
                    mark_seen(conn, link, title); continue
                mark_seen(conn, link, title)
                ai = ai_analyze(title, f"India stock market · {theme['label']}")
                if ai and ai["sentiment"]=="neutral" and ai["impact"]=="low": continue
                if send_telegram(fmt_sector(theme["label"],affected,title,link,ai)):
                    sent += 1; log.info(f"  Sector [{theme['label']}]: {title[:55]}")
                time.sleep(1)
            time.sleep(0.5)

    log.info(f"── Done. {sent} alerts sent ──\n")

# ─────────────────────────────────────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────────────────────────────────────
def send_startup():
    now = datetime.now(IST).strftime("%d %b %Y · %I:%M %p IST")
    msg = (
        f"🚀 <b>StockPilot News Bot v2 — Live!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ {now}\n\n"
        f"📊 <b>Portfolio ({len(PORTFOLIO)}):</b> <code>" + ", ".join(s['ticker'] for s in PORTFOLIO) + "</code>\n\n"
        f"👁 <b>Watchlist ({len(WATCHLIST)}):</b> <code>" + ", ".join(s['ticker'] for s in WATCHLIST) + "</code>\n\n"
        f"<b>📡 News Sources:</b>\n"
        f"  🏛 NSE Corporate Announcements (official API)\n"
        f"  🏛 BSE Corporate Announcements (official API)\n"
        f"  🏦 BSE Corporate Actions (dividends/bonus/splits)\n"
        f"  🔔 NSE Exchange Circulars (regulatory)\n"
        f"  📰 Google News · Yahoo Finance · ET Markets · Moneycontrol\n"
        f"  🌐 {len(SECTOR_THEMES)} sector macro themes\n\n"
        f"🤖 AI Analysis: {'✅ ON' if ANTHROPIC_KEY else '⚠️ OFF (set ANTHROPIC_API_KEY)'}\n"
        f"🔄 Refresh every {CHECK_INTERVAL//60} min\n"
        f"━━━━━━━━━━━━━━━━━━━━━\nWatching markets for you. 📡"
    )
    send_telegram(msg)

def main():
    log.info("StockPilot News Bot v2 starting…")
    conn = init_db()
    send_startup()
    while True:
        try:
            run_cycle(conn)
        except KeyboardInterrupt:
            log.info("Stopped."); break
        except Exception as e:
            log.error(f"Cycle error: {e}", exc_info=True)
        log.info(f"Sleeping {CHECK_INTERVAL}s…")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
