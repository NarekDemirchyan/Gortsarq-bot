# -*- coding: utf-8 -*-
# fetcher.py — ընդհանուր ընթերցիչ (list.am, gortsarq.am և այլն)
# Հերթականությունը՝ 1) ուղիղ requests (բրաուզերի headers) 2) r.jina.ai x-engine: browser
# 3) անվճար պրոքսի։ jina() վերադարձնում է նույն markdown-ը, ինչ r.jina.ai-ն, որպեսզի
# bot.py / gortsarq.py / arenda.py-ի պարսերները մնան անփոփոխ։
# x-respond-with: html դեպքում վերադարձվում է չմշակված HTML։

import os
import re
import time
import requests
from urllib.parse import quote, urljoin

JINA = "https://r.jina.ai/"
JINA_KEY = os.environ.get("JINA_KEY", "")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

HARD_BLOCK = ("just a moment", "performing security verification",
              "attention required", "checking your browser")

PROXIES = (
    "https://api.allorigins.win/raw?url=",
    "https://api.codetabs.com/v1/proxy?quest=",
)

IMG_JUNK = ("list.am/img/", "gortsarq.am/img/", "/icons/", "flag_", "logo", "sprite", "/static/")

def is_blocked(text):
    t = (text or "").strip()
    if len(t) < 800:
        return True
    low = t.lower()
    return any(m in low for m in HARD_BLOCK)

def _unesc(s):
    s = s or ""
    for a, b in (("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"),
                 ("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " "),
                 ("&laquo;", "«"), ("&raquo;", "»")):
        s = s.replace(a, b)
    return s

def _base_of(url):
    parts = url.split("/")
    return "/".join(parts[:3]) if len(parts) >= 3 else url

def _metas(h):
    out = {}
    for tag in re.findall(r"(?is)<meta\b[^>]*>", h or ""):
        k = re.search(r'(?is)(?:property|name)\s*=\s*["\']([^"\']+)["\']', tag)
        v = re.search(r'(?is)content\s*=\s*["\'](.*?)["\']', tag)
        if k and v:
            out.setdefault(k.group(1).strip().lower(), _unesc(v.group(1)).strip())
    return out

def _strip_tags(t):
    t = re.sub(r"(?is)<(script|style|noscript|svg|head)\b[^>]*>.*?</\1>", " ", t or "")
    t = re.sub(r"(?is)<br\s*/?>", "\n", t)
    t = re.sub(r"(?is)</(p|div|li|tr|h[1-6]|section|article|span)>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = _unesc(t)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\n[ \t]+", "\n", t)
    t = re.sub(r"\n{2,}", "\n\n", t)
    return t.strip()

def _links_md(h, base):
    def a(m):
        href = urljoin(base, _unesc(m.group(1)).strip())
        txt = " ".join(_strip_tags(m.group(2)).split()) or href
        return "\n[%s](%s)\n" % (txt, href)
    return re.sub(r'(?is)<a\b[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>', a, h or "")

def _imgs(h, base):
    urls = []
    for m in re.finditer(r'(?is)<img\b[^>]*?(?:data-src|data-original|src)\s*=\s*["\']([^"\']+)["\']', h or ""):
        u = urljoin(base, _unesc(m.group(1)).strip())
        if re.search(r"\.(?:jpe?g|png|webp)(?:\?|$)", u, re.I) and u not in urls:
            urls.append(u)
    return urls

def to_md(html, url):
    base = _base_of(url)
    meta = _metas(html)
    text = _strip_tags(_links_md(html, base))

    title = meta.get("og:title") or meta.get("twitter:title") or ""
    if not title:
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
        title = _strip_tags(m.group(1)) if m else ""
    title = re.sub(r"\s*[-–—|]\s*(list\.am|gortsarq\.am).*$", "", title, flags=re.I).strip()
    if not title:
        m = re.search(r"(?is)<h1\b[^>]*>(.*?)</h1>", html or "")
        title = _strip_tags(m.group(1)) if m else ""

    desc = meta.get("og:description") or ""
    if len(desc) < 40 or re.search(r"(list\.am|gortsarq)", desc, re.I):
        paras = [p for p in text.split("\n")
                 if 120 <= len(p) <= 900 and len(p.split()) >= 18]
        if paras:
            desc = max(paras, key=len)

    price = ""
    m = re.search(r"(\d[\d\s,\.]{0,15}?)\s*(֏|\$|€)", text)
    if m:
        price = re.sub(r"[.,]00$", "", m.group(1).strip()) + " " + m.group(2)

    place = ""
    m = re.search(r"([Ա-ՖA-ZА-ЯЁ][^›\n]{1,30}?)\s*›\s*([^›\n]{1,30})", text)
    if m:
        place = m.group(1).strip() + " › " + m.group(2).strip()

    date = ""
    m = re.search(r"\d{2}\.\d{2}\.\d{4}", text)
    if m:
        date = m.group(0)

    out = []
    if title:
        out.append("# " + title)
    if place:
        out.append("[%s](%s)" % (place, url))
    if price:
        out.append(price)
    out.append("Описание *")
    out.append(desc)
    if date:
        out.append(date)
    out.append("Номер объявления")
    out.append(text)
    if place:
        out.append("Расположение *")
        out.append(place)

    imgs = [u for u in _imgs(html, base) if not any(j in u for j in IMG_JUNK)]
    if imgs:
        out.append("\n".join("![Image](%s)" % u for u in imgs))
    return "\n\n".join(x for x in out if x)

def _direct(sess, url):
    try:
        r = sess.get(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru,hy;q=0.9,en;q=0.7",
        }, timeout=30)
        print("  ուղիղ՝ %s, %s նշան" % (r.status_code, len(r.text or "")))
        if r.status_code == 200 and not is_blocked(r.text):
            return r.text
    except Exception as e:
        print("  ուղիղ սխալ՝", type(e).__name__)
    return ""

def _via_jina(sess, url, extra):
    h = {"User-Agent": "curl/8.5.0", "x-engine": "browser"}
    if JINA_KEY:
        h["Authorization"] = "Bearer " + JINA_KEY
    if extra:
        h.update(extra)
    try:
        r = sess.get(JINA + url, headers=h, timeout=35)
        print("  jina browser՝ %s, %s նշան" % (r.status_code, len(r.text or "")))
        if r.status_code == 200 and not is_blocked(r.text):
            return r.text, True
    except Exception as e:
        print("  jina սխալ՝", type(e).__name__)
    time.sleep(1)
    return "", False

def _via_proxy(sess, url):
    q = quote(url, safe="")
    for p in PROXIES:
        try:
            r = sess.get(p + q, headers={"User-Agent": UA}, timeout=30)
            print("  պրոքսի՝ %s, %s նշան" % (r.status_code, len(r.text or "")))
            if r.status_code == 200 and not is_blocked(r.text):
                return r.text
        except Exception as e:
            print("  պրոքսի սխալ՝", type(e).__name__)
    return ""

def jina(sess, url, extra=None):
    html_mode = bool(extra and str(extra.get("x-respond-with", "")).lower() == "html")
    raw, from_jina = "", False

    raw = _direct(sess, url)
    if not raw:
        raw, from_jina = _via_jina(sess, url, extra)
    if not raw:
        raw = _via_proxy(sess, url)
    if not raw:
        print("  ընթերցումը չհաջողվեց՝", url)
        return ""

    if html_mode or from_jina:
        return raw
    return to_md(raw, url)
