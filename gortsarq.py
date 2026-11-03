# -*- coding: utf-8 -*-
"""
gortsarq.am -> Telegram («Բարախոլկա Երևան»). Աղբյուրը՝ միայն գործարք կայքը։

 1. Հրապարակում հերթի գլխից՝ FIFO։
 2. Մեկ անգամ հրապարակվածը չի կրկնվում։
 3. Տեքստում հղում, backtick, աստղանիշ կամ աղբ տող չի մնում։
 4. Կայքը չբացվելիս ոչինչ չի փոխվում։
"""
import os
import re
import json
import time
import urllib.parse

import requests

from fetcher import translate, script, has_cyr, is_translit_hy, led_add, led_has

try:
    from fetcher import to_md
except ImportError:
    def to_md(html, url=""):
        return html

GT = chr(62)

TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
JINA_KEY = os.environ.get("JINA_KEY", "")

SITE        = "gortsarq"
STATE       = "state-gortsarq.json"
LED         = "published.json"
QUEUE_MAX   = 8000
FRESH_DAYS  = 60
PER_CAT     = 2000
MAX_SECONDS = 1500
MAX_PAGES   = 40
MAX_TRIES   = 4
MAX_POSTS   = 1
SEND_TRIES  = 3
MAXP        = 9 * 1024 * 1024

BASE = "https://gortsarq.am"
GROUP_BOOST = "https://t.me/boost/erevan03_baraxolka"

HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "ru,hy;q=0.9,en;q=0.8",
}

BLOCKED = ("just a moment", "performing security verification", "checking your browser",
           "enable javascript", "captcha", "are you a human", "cloudflare",
           "attention required", "access denied", "ddos-guard")

PROXIES = (
    "https://api.allorigins.win/raw",
    "https://api.codetabs.com/v1/proxy",
    "https://corsproxy.io/?url=",
    "https://api.cors.lol/?url=",
)

CATS = [
    "265c-2-ansharj-guyq",
    "1796c-pahestamaser",
    "1875c-kendaniner",
    "1904c-elektronika-hy",
    "2063c-tun-ev-aygi",
    "2116c-noradzevutyun-ev-och",
    "2295c-mankakan-ashxarh",
    "2328c-hobbi-hangist-ev-sport",
    "2377c-transport",
    "1658c-biznes-i-uslugi-ru",
    "2034c-arenda-i-prokat-ru",
    "1485c-rabota-ru",
]

ITEM_RE    = re.compile(r"""https://gortsarq\.am/ru/(\d+)p-[^)\]\s"'""" + GT + r"""]+""")
ITEM_ID_RE = re.compile(r"/(\d+)p-")
PHOTO_RE   = re.compile(r"""https://gortsarq\.am/images/detailed/\d+/[^)\]\s"'""" + GT + r"""]+""")
DATE_RE    = re.compile(r"\d{2}\.\d{2}\.\d{4}")
PRICE_RE   = re.compile(r"([\d][\d ,\.]*)\s*(֏|\$|€)")
MASK_RE    = re.compile(r"\(?\s*0?\s*x{2,}[\s\-x]*x{2,}\s*\)?", re.I)

HEAD_FALLBACK = "Նոր հայտարարություն"

JUNK = ("image", "logo", "аватар", "ավատար", "размещено", "обновлено",
        "տեղադրված", "թարմացվել", "информация", "категория", "статус",
        "просмотр", "favorite", "поделиться")

def today_str():
    return time.strftime("%d.%m.%Y", time.gmtime(time.time() + 4 * 3600))

def scrub(text):
    t = MASK_RE.sub(" ", text or "")
    t = re.sub(r"\s*([,.;:!?])\s*", r"\1 ", t)
    t = re.sub(r"^[\s,.;:!?\-]+", "", t)
    return " ".join(t.split()).strip(" ,.;:!?-\"")

def clean(text):
    """Հեռացնում է հղումները, backtick-ը, աստղանիշը, աղբ տողերը։"""
    t = text or ""
    for sep in ("](", "] ("):
        guard = 0
        while sep in t and guard < 50:
            guard += 1
            i = t.index(sep)
            j = t.find(")", i)
            if j == -1:
                t = t[:i]
                break
            t = t[:i] + " " + t[j + 1:]
    t = t.replace("[", " ").replace("]", " ")
    t = t.replace("**", " ").replace("`", " ").replace("_", " ")
    words = [w for w in t.split() if not w.lower().startswith(("http", "www."))]
    t = " ".join(words)
    t = t.replace("gortsarq.am", " ")
    return scrub(t)

def junk(text):
    low = (text or "").lower()
    return any(j in low for j in JUNK)

def looks_place(text):
    t = text or ""
    if junk(t):
        return False
    if "2026" in t or "2025" in t:
        return False
    if ":" in t and len(t) > 30:
        return False
    if len(t) > 60:
        return False
    return True

def last_days(n):
    out = set()
    base = time.time() + 4 * 3600
    for k in range(n + 1):
        out.add(time.strftime("%d.%m.%Y", time.gmtime(base - k * 24 * 3600)))
    return out

def list_url(cat, page):
    return f"{BASE}/ru/{cat}/" if page == 1 else f"{BASE}/ru/{cat}/?page={page}"

def good(text):
    if not text or len(text) < 1500:
        return False
    low = text.lower()
    return not any(b in low for b in BLOCKED)

def via_proxy(sess, proxy, url, headers, timeout):
    if proxy.endswith("="):
        return sess.get(proxy + urllib.parse.quote(url, safe=""),
                        headers=headers, timeout=timeout)
    return sess.get(proxy, params={"url": url}, headers=headers, timeout=timeout)

_FAILS = [0]

def fetch(sess, url):
    if _FAILS[0] >= 8:
        return ""
    try:
        r = sess.get(url, headers=HDRS, timeout=12)
        if r.status_code == 200 and good(r.text):
            _FAILS[0] = 0
            return to_md(r.text, url)
    except Exception:
        pass
    h = dict(HDRS)
    h["x-engine"] = "browser"
    if JINA_KEY:
        h["Authorization"] = "Bearer " + JINA_KEY
    try:
        r = sess.get("https://r.jina.ai/" + url, headers=h, timeout=20)
        if r.status_code == 200 and good(r.text):
            _FAILS[0] = 0
            return r.text
    except Exception:
        pass
    for p in PROXIES[:2]:
        try:
            r = via_proxy(sess, p, url, HDRS, 15)
            if r.status_code == 200 and good(r.text):
                _FAILS[0] = 0
                return to_md(r.text, url)
        except Exception:
            pass
    _FAILS[0] += 1
    print("  չկարդացվեց:", url)
    return ""

def site_up(sess):
    return bool(fetch(sess, BASE + "/ru/"))

_THUMBS_DESC = ("1024/1024", "800/800", "480/480", "320/320", "240/240")

def thumb_urls(u):
    out = []
    if "/images/detailed/" in u:
        tail = u.split("/images/detailed/", 1)[1]
        for size in _THUMBS_DESC:
            out.append(BASE + "/images/thumbnails/" + size + "/detailed/" + tail)
    out.append(u)
    return out

def relay_urls(u):
    bare = u.split("://", 1)[-1]
    return [f"https://images.weserv.nl/?url={bare}",
            f"https://images.weserv.nl/?url={bare}&w=800",
            f"https://wsrv.nl/?url={bare}"]

def img_ok(r):
    b = r.content or b""
    if r.status_code != 200 or len(b) < 800:
        return False
    return not b.lstrip()[:1] == b"<"

def bytes_from(sess, url, ref):
    if _FAILS[0] >= 12:
        return None
    h = dict(HDRS)
    h["Referer"] = ref or BASE + "/"
    h["Accept"] = "image/avif,image/webp,image/jpeg,image/*,*/*;q=0.8"
    try:
        r = sess.get(url, headers=h, timeout=20)
        if img_ok(r):
            _FAILS[0] = 0
            return r.content
    except Exception:
        pass
    for p in PROXIES[:2]:
        try:
            r = via_proxy(sess, p, url, h, 25)
            if img_ok(r):
                return r.content
        except Exception:
            pass
    for p in relay_urls(url)[:1]:
        try:
            r = sess.get(p, headers=h, timeout=25)
            if img_ok(r):
                return r.content
        except Exception:
            pass
    _FAILS[0] += 1
    return None

def photo_bytes(sess, url, ref):
    for u in thumb_urls(url):
        b = bytes_from(sess, u, ref)
        if b and len(b) <= MAXP:
            return b
    print("  նկարը չներբեռնվեց կամ չափից մեծ է:", url)
    return None

def get_items(sess, deadline):
    ok_dates = last_days(FRESH_DAYS)
    found = {}
    for cat in CATS:
        per_cat = 0
        for page in range(1, MAX_PAGES + 1):
            if time.time() > deadline:
                print("  сбор: время вышло")
                return found
            md = fetch(sess, list_url(cat, page))
            if not md:
                break
            n = 0
            fresh = 0
            for m in ITEM_RE.finditer(md):
                iid = m.group(1)
                path = m.group(0)[len(BASE):].split("]")[0].split(")")[0]
                tail = md[m.end():m.end() + 300]
                d = DATE_RE.search(tail)
                date = d.group(0) if d else ""
                if date and date in fresh_days:
                    pass
                if date and date in ok_days:
                    pass
                continue
    return items

def main():
    return 0
