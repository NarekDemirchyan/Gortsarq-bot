# -*- coding: utf-8 -*-
import os, re, json, time, requests

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID_AUTO", "")

STATE      = "state_auto.json"
INTERVAL   = 0            # публикуем на каждом запуске
QUEUE_MAX  = 2000         # максимум объявлений в очереди
FRESH_DAYS = 7            # берём объявления за последние 7 суток
RESET_ONCE = True
TEST       = False
ONLY_PRIVATE = True       # только частные продавцы
FORGET_SEEN  = True       # забыть просмотренные, чтобы очередь наполнилась

JINA = "https://r.jina.ai/"
BASE = "https://auto.am"
SITEMAPS = [f"{BASE}/sitemaps/offers-{i}.xml" for i in range(1, 11)]
HDRS = {"User-Agent": "curl/8.5.0"}
# для картинок — браузерный заголовок, иначе сайт отдаёт заглушку
IMG_HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "ru,hy;q=0.9,en;q=0.8",
}

DATE_RE = re.compile(r"<lastmod>(\d{4}-\d{2}-\d{2})")
BLOCKED = ("just a moment", "attention required", "enable javascript",
           "checking your browser", "cloudflare")

PHOTO_RE = re.compile(
    r"https://auto\.am/static/offers/(\d+)/(?:[ms]-)?([0-9a-f]{8,})\.(webp|jpg|jpeg|png)",
    re.I)


def is_blocked(text):
    t = (text or "").strip()
    return len(t) < 800 or any(m in t.lower() for m in BLOCKED)


def jina(sess, url, extra=None):
    h = dict(HDRS)
    if extra:
        h.update(extra)
    for attempt in range(4):
        try:
            r = sess.get(JINA + url, headers=h, timeout=60)
            if r.status_code == 200 and not is_blocked(r.text):
                return r.text
            print(f"  jina {r.status_code} / защита, попытка {attempt + 1}")
        except Exception as e:
            print(f"  jina ошибка: {type(e).__name__}: {e}")
        time.sleep(10 * (attempt + 1))
    return ""


def fetch_sitemap(sess, url):
    try:
        r = sess.get(url, headers=HDRS, timeout=60)
        if r.status_code == 200 and "<urlset" in r.text:
            return r.text
    except Exception as e:
        print("  sitemap ошибка:", type(e).__name__)
    return jina(sess, url)


def today_iso():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 4 * 3600))


def last_days(n):
    out = set()
    base = time.time() + 4 * 3600
    for k in range(n + 1):
        out.add(time.strftime("%Y-%m-%d", time.gmtime(base - k * 24 * 3600)))
    return out


def translate(sess, text):
    text = (text or "").strip()
    if not text:
        return text
    try:
        r = sess.get("https://translate.googleapis.com/translate_a/single",
                     params={"client": "gtx", "sl": "auto", "tl": "ru",
                             "dt": "t", "q": text[:2000]}, timeout=30)
        res = "".join(p[0] for p in r.json()[0] if p and p[0]).strip()
        if res:
            return res
    except Exception as e:
        print("  перевод недоступен:", type(e).__name__)
    return text


def has_cyr(s):
    return bool(re.search(r"[А-Яа-яЁё]", s or ""))

PHOTO_RE = re.compile(
    r"https://auto\.am/static/offers/(\d+)/(?:[ms]-)?([0-9a-f]{8,})\.(webp|jpg|jpeg|png)",
    re.I)


def is_blocked(text):
    t = (text or "").strip()
    return len(t) < 800 or any(m in t.lower() for m in BLOCKED)


def jina(sess, url, extra=None):
    h = dict(HDRS)
    if extra:
        h.update(extra)
    for attempt in range(4):
        try:
            r = sess.get(JINA + url, headers=h, timeout=60)
            if r.status_code == 200 and not is_blocked(r.text):
                return r.text
            print(f"  jina {r.status_code} / защита, попытка {attempt + 1}")
        except Exception as e:
            print(f"  jina ошибка: {type(e).__name__}: {e}")
        time.sleep(10 * (attempt + 1))
    return ""


def fetch_sitemap(sess, url):
    try:
        r = sess.get(url, headers=HDRS, timeout=60)
        if r.status_code == 200 and "<urlset" in r.text:
            return r.text
    except Exception as e:
        print("  sitemap ошибка:", type(e).__name__)
    return jina(sess, url)


def today_iso():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 4 * 3600))


def last_days(n):
    out = set()
    base = time.time() + 4 * 3600
    for k in range(n + 1):
        out.add(time.strftime("%Y-%m-%d", time.gmtime(base - k * 24 * 3600)))
    return out


def translate(sess, text):
    text = (text or "").strip()
    if not text:
        return text
    try:
        r = sess.get("https://translate.googleapis.com/translate_a/single",
                     params={"client": "gtx", "sl": "auto", "tl": "ru",
                             "dt": "t", "q": text[:2000]}, timeout=30)
        res = "".join(p[0] for p in r.json()[0] if p and p[0]).strip()
        if res:
            return res
    except Exception as e:
        print("  перевод недоступен:", type(e).__name__)
    return text


def has_cyr(s):
    return bool(re.search(r"[А-Яа-яЁё]", s or ""))
    return len(t) < 800 or any(m in t.lower() for m in BLOCKED)


def jina(sess, url, extra=None):
    h = dict(HDRS)
    if extra:
        h.update(extra)
    for attempt in range(4):
        try:
            r = sess.get(JINA + url, headers=h, timeout=60)
            if r.status_code == 200 and not is_blocked(r.text):
                return r.text
            print(f"  jina {r.status_code} / защита, попытка {attempt + 1}")
        except Exception as e:
            print(f"  jina ошибка: {type(e).__name__}: {e}")
        time.sleep(10 * (attempt + 1))
    return ""


def fetch_sitemap(sess, url):
    try:
        r = sess.get(url, headers=HDRS, timeout=60)
        if r.status_code == 200 and "<urlset" in r.text:
            return r.text
    except Exception as e:
        print("  sitemap ошибка:", type(e).__name__)
    return jina(sess, url)


def today_iso():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 4 * 3600))


def last_days(n):
    out = set()
    base = time.time() + 4 * 3600
    for k in range(n + 1):
        out.add(time.strftime("%Y-%m-%d", time.gmtime(base - k * 24 * 3600)))
    return out


def get_ids(sess):
    items = []
    seen = set()
    for sm in SITEMAPS:
        xml = fetch_sitemap(sess, sm)
        for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
            m = re.search(r"auto\.am/(?:ru/|en/)?offer/(\d+)", block)
            if not m:
                continue
            iid = m.group(1)
            if iid in seen:
                continue
            seen.add(iid)
            d = DATE_RE.search(block)
            items.append((iid, d.group(1) if d else ""))
        print(f"  {sm.rsplit('/', 1)[-1]}: всего {len(items)} объявлений")
        time.sleep(5)
    return items

