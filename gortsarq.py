# -*- coding: utf-8 -*-
"""
gortsarq.am -> Telegram («Барахолка Երևան»).

Порядок работы одного запуска:
  1. Публикуем из уже собранной очереди — один пост, быстро.
  2. Добираем объявления за последнюю неделю, но не дольше MAX_SECONDS.
  3. Состояние сохраняется после каждой ступени.

Раздел листается, пока на странице есть свежее за FRESH_DAYS суток и пока идут
новые ссылки. MAX_PAGES — предохранитель, обычно до него не доходит: в списке
сначала самые свежие, поэтому страница без свежих означает, что дальше старое.

Страница объявления читается один раз — markdown хватает и для заголовка, и для
цены, и для фото. Чтение идёт прямым запросом первым, дальше Jina и открытые
прокси — по одной попытке. HTML переводим в markdown через fetcher.to_md, под
который написаны парсеры.

Один id может выйти снова: если продавец обновил объявление позже, чем оно
выходило в группе, оно публикуется заново. У gortsarq на карточке видна только
дата, поэтому сравниваются даты, а не часы.

Закрытые телефоны вида «(0x xxx xxxx)» вырезаем. Заголовок и описание переводим
на русский, не вышло — публикуем оригинал. Инфинитив вроде «Одеваться» заголовком
не ставим. Армянское слово латиницей не публикуем. Без фото объявление пропускаем.
Очередь перед публикацией перемешивается, чтобы разделы шли вперемешку.

Знаки «<» и «>» в разборе страниц набраны кодом символа.
"""
import os
import re
import json
import time
import random

import requests

from fetcher import translate, script, has_cyr, is_translit_hy, led_add

try:
    from fetcher import to_md
except ImportError:
    def to_md(html, url=""):
        return html

GT = chr(62)

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

SITE        = "gortsarq"
STATE       = "state-gortsarq.json"
INTERVAL    = 0              # ритм задаёт расписание (run.py)
QUEUE_MAX   = 2000           # максимум объявлений в очереди
FRESH_DAYS  = 7              # берём объявления за последнюю неделю
PER_CAT     = 2000           # потолок ссылок на раздел
MAX_PAGES   = 20             # предохранитель по страницам раздела
MAX_SECONDS = 15 * 60        # сколько секунд отдаём сбору, остальное — запас
RESET_ONCE  = False          # однократный сброс очереди (уже сделан)
TEST        = False

BASE = "https://gortsarq.am"

HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "ru,hy;q=0.9,en;q=0.8",
}

BLOCKED = ("just a moment", "performing security verification", "checking your browser",
           "enable javascript", "captcha", "are you a human")

CATS = [
    "265c-2-ansharj-guyq",          # Անշարժ գույք
    "1796c-pahestamaser",           # Պահեստամասեր
    "1875c-kendaniner",             # Կենդանիներ
    "1904c-elektronika-hy",         # Էլեկտրոնիկա
    "2063c-tun-ev-aygi",            # Տուն և այգի
    "2116c-noradzevutyun-ev-och",   # Նորաձևություն և ոճ
    "2295c-mankakan-ashxarh",       # Մանկական աշխարհ
    "2328c-hobbi-hangist-ev-sport", # Հոբբի, հանգիստ և սպորտ
]

ITEM_RE    = re.compile(r"""https://gortsarq\.am/ru/(\d+)p-[^)\]\s"'""" + GT + r"""]+""")
ITEM_ID_RE = re.compile(r"/(\d+)p-")
PHOTO_RE   = re.compile(r"""https://gortsarq\.am/images/detailed/\d+/[^)\]\s"'""" + GT + r"""]+""")
DATE_RE    = re.compile(r"\d{2}\.\d{2}\.\d{4}")

# закрытый телефон: (0x xxx xxxx) и любые похожие наборы «x»
MASK_RE    = re.compile(r"\(?\s*0?\s*x{2,}[\s\-x]*x{2,}\s*\)?", re.I)

def today_str():
    return time.strftime("%d.%m.%Y", time.gmtime(time.time() + 4 * 3600))

def scrub(text):
    """Убираем закрытые телефоны и лишние пробелы."""
    return " ".join(MASK_RE.sub(" ", text or "").split())

def last_days(n):
    out = set()
    base = time.time() + 4 * 3600
    for k in range(n + 1):
        out.add(time.strftime("%d.%m.%Y", time.gmtime(base - k * 24 * 3600)))
    return out

def list_url(cat, page):
    return f"{BASE}/ru/{cat}/" if page == 1 else f"{BASE}/ru/{cat}/?page={page}"

# ---------- сеть ----------

def good(text):
    """Страница пригодна: не защита и не пустышка."""
    if not text or len(text) < 1500:
        return False
    low = text.lower()
    return not any(b in low for b in BLOCKED)

def fetch(sess, url):
    """Прямой запрос первым, дальше Jina, дальше прокси. По одной попытке.
    Возвращаем markdown: HTML переводим через fetcher.to_md."""
    try:
        r = sess.get(url, headers=HDRS, timeout=25)
        if r.status_code == 200 and good(r.text):
            return to_md(r.text, url)
        print(f"  прямой {r.status_code}, {len(r.text)} знаков")
    except Exception as e:
        print(f"  прямой сбой {type(e).__name__}")

    try:
        h = dict(HDRS)
        h["x-engine"] = "browser"
        r = sess.get("https://r.jina.ai/" + url, headers=h, timeout=40)
        if r.status_code == 200 and good(r.text):
            print("  jina")
            return r.text
        print(f"  jina {r.status_code}, {len(r.text)} знаков")
    except Exception as e:
        print(f"  jina сбой {type(e).__name__}")

    for p in ("https://api.allorigins.win/raw", "https://api.codetabs.com/v1/proxy"):
        try:
            r = sess.get(p, params={"url": url}, headers=HDRS, timeout=40)
            if r.status_code == 200 and good(r.text):
                print("  прокси")
                return to_md(r.text, url)
            print(f"  прокси {r.status_code}")
        except Exception as e:
            print(f"  прокси сбой {type(e).__name__}")

    print("  чтение не удалось:", url)
    return ""

# ---------- разбор ----------

def get_items(sess, deadline):
    """Ссылки разделов, пока идут новые и пока на странице есть свежее."""
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
                if date and date in ok_dates:
                    fresh += 1
                if iid not in found:
                    found[iid] = {"path": path, "date": date}
                    n += 1
                elif date and not found[iid]["date"]:
                    found[iid]["date"] = date
            per_cat += n
            print(f"[{cat}] стр.{page}: новых {n}, свежих {fresh}")
            if n == 0 or per_cat >= PER_CAT:
                break
            if fresh == 0 and page > 1:
                break                  # дальше только старое
            time.sleep(1)
    return found

def item_data(sess, path):
    md = fetch(sess, BASE + path)
    if not md:
        return None

    cut = md.find("Похожие")
    if cut > 0:
        md = md[:cut]

    title = ""
    m = re.search(r"^#{1,3}\s+(.+)$", md, re.M)
    if m:
        title = scrub(m.group(1).strip())

    price = ""
    m = re.search(r"([\d][\d ,\.]*)\s*(֏|\$|€)", md)
    if m:
        num = " ".join(re.sub(r"[.,]00$", "", m.group(1).strip()).split())
        if re.search(r"[1-9]", num):
            price = num + " " + m.group(2)
    if not price and "Договорная" in md:
        price = "Договорная"

    desc = ""
    m = re.search(r"Описание\s*\n+(.+?)\n+\d{2}\.\d{2}\.\d{4}", md, re.S)
    if m:
        desc = scrub(" ".join(m.group(1).split()))

    place = ""
    m = re.search(r"Расположение\s*\n+(.+)", md)
    if m:
        place = " ".join(m.group(1).split())[:60]

    photos = []
    for m in PHOTO_RE.finditer(md):
        u = m.group(0).split("]")[0].split(")")[0]
        if u not in photos:
            photos.append(u)

    print(f"  {path}: фото {len(photos)}, заголовок «{title}», цена «{price}», "
          f"описание {len(desc)} знаков")
    return {"title": title, "price": price, "desc": desc,
            "place": place, "photos": photos[:10], "url": BASE + path}

# ---------- текст ----------

def headline(sess, d):
    src = scrub((d["title"] or "").strip())
    t = scrub(translate(sess, src).strip())
    if is_translit_hy(src) or is_translit_hy(t):
        return ""
    # инфинитив вроде «Одеваться» заголовком не ставим
    if t and not re.search(r"(?:ться|тись)$", t.lower()) and (has_cyr(t) or script(t) == "lat"):
        return " ".join(t.split())[:90]
    alt = scrub(translate(sess, d["desc"][:300]).strip())
    if has_cyr(alt) and len(alt.split()) >= 3:
        return " ".join(re.split(r"[.!?]\s", alt)[0].strip().split())[:90]
    if t and (has_cyr(t) or script(t) == "lat"):
        return " ".join(t.split())[:90]
    return " ".join((t or alt).split())[:90]

def make_caption(sess, d):
    head = headline(sess, d)
    if not head:
        return ""
    lines = [head]
    if d["price"]:
        lines.append(d["price"])
    if d["place"]:
        lines.append(d["place"])
    if d["desc"]:
        lines.append(scrub(translate(sess, d["desc"][:700])))
    lines = [" ".join(scrub(x).split()) for x in lines if x]
    return "\n\n".join(lines)[:1024]

# ---------- Telegram ----------

def send(sess, d, caption):
    markup = {"inline_keyboard": [[{"text": "Связаться", "url": d["url"]}]]}
    photos = d["photos"]

    if len(photos) >= 2:
        media = [{"type": "photo", "media": photos[0], "caption": caption}]
        for p in photos[1:10]:
            media.append({"type": "photo", "media": p})
        r = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendMediaGroup",
                      data={"chat_id": CHAT_ID,
                            "media": json.dumps(media, ensure_ascii=False)},
                      timeout=60)
        print("telegram album:", r.status_code, r.text[:200])

        if not r.ok:
            payload = {"chat_id": CHAT_ID, "photo": photos[0],
                       "caption": caption, "reply_markup": json.dumps(markup)}
            r1 = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                           data=payload, timeout=40)
            print("telegram fallback:", r1.status_code, r1.text[:200])
            return bool(r1.ok)

        mid = None
        try:
            res = r.json().get("result") or []
            if res:
                mid = res[0]["message_id"]
        except Exception:
            mid = None

        payload = {"chat_id": CHAT_ID, "text": "Связаться с продавцом",
                   "reply_markup": json.dumps(markup)}
        if mid:
            payload["reply_parameters"] = json.dumps({"message_id": mid})
        r2 = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                       data=payload, timeout=40)
        print("telegram кнопка:", r2.status_code, r2.text[:200])
        return bool(r.ok and r2.ok)

    payload = {"chat_id": CHAT_ID, "reply_markup": json.dumps(markup)}
    if photos:
        payload["photo"] = photos[0]
        payload["caption"] = caption
        method =…
