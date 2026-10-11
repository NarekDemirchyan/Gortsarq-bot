# -*- coding: utf-8 -*-
"""
ivi.am -> Telegram.
Публикует объявления, обновлённые продавцом за последние 24 часа.
Каждое объявление выходит один раз: id попадает в state-ivi.json -> "published".
Из разделов исключены: услуги, работа, бизнес, прочие объявления,
аренда недвижимости, автосервис, стройка/ремонт.
"""
import os
import re
import json
import time
import html as htmllib
from datetime import datetime, timedelta, timezone

import requests

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

STATE       = "state-ivi.json"
INTERVAL    = 10 * 60    # пауза между публикациями, сек
PUBLISH_MAX = 1          # сколько публиковать за один запуск
QUEUE_MAX   = 300        # предел очереди
FRESH_H     = 24         # сколько часов объявление считается свежим после обновления
PER_CAT     = 60         # сколько карточек смотреть в разделе
SAMPLE      = 3          # сколько карточек печатать в лог для проверки
TEST        = False      # True -> ничего не отправлять, только показать разбор в логе

BASE = "https://ivi.am"
TZ   = timezone(timedelta(hours=4))          # время сайта (Ереван)

SECTIONS = {
    "elektronika":         "Электроника",
    "dom-i-sad":           "Дом и сад",
    "transport":           "Транспорт",
    "moda-i-stil":         "Мода и стиль",
    "detskiy-mir":         "Детский мир",
    "zhivotnye":           "Животные",
    "hobbi-otdyh-i-sport": "Искусство и хобби",
    "ansharj-guyq":        "Недвижимость (только продажа)",
    "lost-found":          "Потеряно / найдено",
}

# подкатегории, которые не берём, даже если они попали в раздел
SKIP_SUB = {
    "ansharj-guyq": ("vardzakalotyon",),       # аренда недвижимости
    "transport":    ("avto-moto-uslugi",),     # автосервис, мойки
    "dom-i-sad":    ("stroitelstvo-remont",),  # стройка и ремонт
}

HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "ru,hy;q=0.9,en;q=0.8",
}

LINK_RE  = re.compile(r"/ru/item/([a-z0-9][a-z0-9\-]*?)-(\d{1,12})\.html", re.I)
PHOTO_RE = re.compile(
    r'(?:src|data-src)="([^"]*?/files/images/items/\d+/[0-9a-z]+\.(?:jpg|jpeg|png|webp))"',
    re.I)
PRICE_RE = re.compile(r"(\d[\d\s]{0,12})\s*֏")
UPD_RE   = re.compile(r"(?:Обновлено|Թարմացվել է)\s*:?\s*([^|]{1,40})")
REL_RE   = re.compile(r"([^|]{0,20}(?:Сегодня|Вчера|այսօր|երեկ)\s+\d{1,2}:\d{2})")
MONTHS   = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "мая": 5, "июн": 6,
            "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}

# ---------- сеть ----------

def fetch(sess, url):
    """Прямой запрос, при отказе — открытый прокси. Сайт защиты не имеет."""
    for attempt in range(2):
        try:
            r = sess.get(url, headers=HDRS, timeout=40)
            if r.status_code == 200 and len(r.text) > 1500:
                return r.text
            print(f"  прямой {r.status_code}, {len(r.text)} знаков")
        except Exception as e:
            print(f"  ошибка {type(e).__name__}: {e}")
        time.sleep(2)
    try:
        r = sess.get("https://api.allorigins.win/raw",
                     params={"url": url}, headers=HDRS, timeout=60)
        if r.status_code == 200 and len(r.text) > 1500:
            print("  через прокси")
            return r.text
        print(f"  прокси {r.status_code}, {len(r.text)} знаков")
    except Exception as e:
        print(f"  прокси ошибка: {type(e).__name__}: {e}")
    return ""

def plain(h):
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", h)).split())

# ---------- разбор ----------

def find_price(txt):
    m = PRICE_RE.search(txt or "")
    if not m:
        return ""
    return " ".join(m.group(1).split()) + " ֏"

def parse_updated(s):
    """'Сегодня 19:03' / 'Вчера 22:10' / '10 октября' -> unix-время или None."""
    s = (s or "").strip()
    if not s:
        return None
    now = datetime.now(TZ)
    m = re.search(r"(\d{1,2}):(\d{2})", s)
    hh, mm = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    low = s.lower()
    if "сегодня" in low or "այսօր" in low:
        d = now
    elif "вчера" in low or "երեկ" in low:
        d = now - timedelta(days=1)
    else:
        m2 = re.search(r"(\d{1,2})\s+([А-Яа-яЁё]+)", s)
        if not m2:
            return None
        mon = MONTHS.get(m2.group(2).lower()[:3])
        if not mon:
            return None
        year = now.year
        d = datetime(year, mon, int(m2.group(1)), hh, mm, tzinfo=TZ)
        if d > now + timedelta(days=1):
            d = datetime(year - 1, mon, int(m2.group(1)), hh, mm, tzinfo=TZ)
    return d.replace(hour=hh, minute=mm, second=0, microsecond=0).timestamp()

def is_fresh(ts):
    if not ts:
        return False
    now = time.time()
    return 0 <= now - ts <= FRESH_H * 3600

def card_scan(h, slug, limit):
    """Карточки раздела: id, ссылка, «Обновлено», цена, превью-фото."""
    ms = list(LINK_RE.finditer(h))
    out = {}
    skip = SKIP_SUB.get(slug, ())
    i = 0
    while i < len(ms):
        iid = ms[i].group(2)
        j = i + 1
        while j < len(ms) and ms[j].group(2) == iid:
            j += 1                     # карточка ссылается на объявление дважды
        start = ms[i].start()
        end = ms[j].start() if j < len(ms) else min(len(h), ms[j - 1].end() + 3000)
        win = h[start:end]
        url = BASE + ms[i].group(0)
        i = j
        if iid in out or any(tok in win for tok in skip):
            continue
        txt = plain(win)[:500]
        mu = UPD_RE.search(txt) or REL_RE.search(txt)
        upd = mu.group(1).strip(" |·—") if mu else ""
        ph = PHOTO_RE.search(win)
        out[iid] = {
            "id": iid,
            "url": url,
            "text": txt,
            "updated_txt": upd,
            "ts": parse_updated(upd),
            "price": find_price(txt),
            "photo": ph.group(1) if ph else "",
        }
        if len(out) >= limit:
            break
    return out

def item_data(sess, url):
    h = fetch(sess, url)
    if not h:
        return None
    for tail in ("Похожие", "Նման", "Այլ հայտարարություններ"):
        cut = h.find(tail)
        if cut > 0:
            h = h[:cut]
            break

    title = ""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S)
    if m:
        title = plain(m.group(1))
    if not title:
        m = re.search(r'property="og:title"\s+content="([^"]+)"', h, re.I)
        if m:
            title = htmllib.unescape(m.group(1)).strip()

    txt = plain(h)
    price = find_price(txt)

    place = ""
    m = re.search(r"([А-Яа-яЁёA-Za-z][^|<>]{2,40}?)\s*\|\s*\d{2}\.\d{2}\.\d{4}", txt)
    if m:
        place = re.sub(r"\s+", " ", m.group(1)).strip(" ,")

    photos = []
    for u in PHOTO_RE.findall(h):
        if u.startswith("//"):
            u = "https:" + u
        if u not in photos:
            photos.append(u)
    if not photos:
        m = re.search(r'property="og:image"\s+content="([^"]+)"', h, re.I)
        if m:
            photos = [htmllib.unescape(m.group(1))]

    desc = ""
    m = re.search(r'class="[^"]*(?:desc|text|opisanie)[^"]*"[^>]*>(.*?)</div>',
                  h, re.S | re.I)
    if m:
        desc = plain(m.group(1))
    if not desc:
        m = re.search(r'property="og:description"\s+content="([^"]+)"', h, re.I)
        if m:
            desc = htmllib.unescape(m.group(1)).strip()
    if title and desc.startswith(title):
        desc = desc[len(title):].strip(" -—|")

    print(f"  {url.rsplit('-', 1)[-1]}: фото {len(photos)}, цена «{price}», место «{place}»")
    return {"title": title, "price": price, "place": place,
            "desc": desc, "photos": photos[:10], "url": url}

# ---------- текст ----------

TR_CACHE = {}

def translate(sess, text):
    text = (text or "").strip()
    if not text or re.search(r"[А-Яа-яЁё]", text):
        return text
    if text in TR_CACHE:
        return TR_CACHE[text]
    try:
        r = sess.get("https://translate.googleapis.com/translate_a/single",
                     params={"client": "gtx", "sl": "auto", "tl": "ru",
                             "dt": "t", "q": text[:2000]}, timeout=30)
        res = "".join(p[0] for p in r.json()[0] if p and p[0]).strip()
        if res:
            TR_CACHE[text] = res
            return res
    except Exception as e:
        print("  перевод недоступен:", type(e).__name__)
    return text

def has_cyr(s):
    return bool(re.search(r"[А-Яа-яЁё]", s or ""))

def headline(sess, d):
    t = translate(sess, d["title"]).strip()
    if has_cyr(t):
        return t
    alt = translate(sess, d["desc"][:300]).strip()
    if has_cyr(alt) and len(alt.split()) >= 3:
        return re.split(r"[.!?]\s", alt)[0].strip()[:90]
    return t[:90]

def caption(sess, d):
    head = headline(sess, d)
    if not head:
        return ""
    lines = [head]
    if d["price"]:
        lines.append(d["price"])
    if d["place"]:
        lines.append(d["place"])
    if d["desc"]:
        lines.append(translate(sess, d["desc"][:700]))
    return "\n\n".join(x for x in lines if x)[:1024]

# ---------- Telegram ----------

def send(sess, d, cap):
    markup = {"inline_keyboard": [[{"text": "Связаться", "url": d["url"]}]]}
    photos = d["photos"]

    if len(photos) >= 2:
        media = [{"type": "photo", "media": photos[0], "caption": cap}]
        for p in photos[1:10]:
            media.append({"type": "photo", "media": p})
        r = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendMediaGroup",
                      data={"chat_id": CHAT_ID,
                            "media": json.dumps(media, ensure_ascii=False)},
                      timeout=60)
        print("telegram album:", r.status_code, r.text[:200])
        if not r.ok:
            r1 = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                           data={"chat_id": CHAT_ID, "photo": photos[0],
                                 "caption": cap,
                                 "reply_markup": json.dumps(markup)}, timeout=40)
            print("telegram fallback:", r1.status_code, r1.text[:200])
            return r1.ok
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
        return r.ok

    payload = {"chat_id": CHAT_ID, "reply_markup": json.dumps(markup)}
    if photos:
        payload["photo"] = photos[0]
        payload["caption"] = cap
        method = "sendPhoto"
    else:
        payload["text"] = cap
        method = "sendMessage"
    r = sess.post(f"https://api.telegram.org/bot{TOKEN}/{method}",
                  data=payload, timeout=40)
    print("telegram:", r.status_code, r.text[:300])
    return r.ok

# ---------- состояние ----------

def load_st():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception as e:
            print("state-ivi.json не читается:", e)
    return {}

def main():
    if not TOKEN or not CHAT_ID:
        print("нет TELEGRAM_TOKEN / TELEGRAM_CHAT_ID")
        return

    st = load_st()
    st.setdefault("queue", [])       # [[id, url, ts], ...]
    st.setdefault("published", {})   # id -> ts
    st.setdefault("failed", {})
    st.setdefault("last_publish", 0)

    sess = requests.Session()
    now = time.time()

    # 1. все объявления, обновлённые за последние 24 часа
    for slug, name in SECTIONS.items():
        h = fetch(sess, f"{BASE}/ru/search/{slug}/")
        cards = card_scan(h, slug, PER_CAT)
        fresh = [c for c in cards.values() if is_fresh(c["ts"])]
        print(f"[{slug}] {name}: карточек {len(cards)}, свежих за {FRESH_H}ч {len(fresh)}")
        for c in list(cards.values())[:SAMPLE]:
            print(f"    {c['id']}: «{c['text'][:140]}»")
        for c in fresh:
            if c["id"] in st["published"]:
                continue
            if any(q[0] == c["id"] for q in st["queue"]):
                continue
            st["queue"].append([c["id"], c["url"], int(c["ts"])])
        time.sleep(1)

    # 2. старое выкидываем, хвост очереди ограничиваем
    far = time.time() - FRESH_H * 3600
    st["queue"] = [q for q in st["queue"] if q[2] >= far]
    while len(st["queue"]) > QUEUE_MAX:
        st["queue"].pop(0)
    print("в очереди:", len(st["queue"]))

    # 3. публикация
    done = 0
    while st["queue"] and done < PUBLISH_MAX:
        if not TEST and now - st["last_publish"] < INTERVAL:
            print("ждём интервал между публикациями")
            break
        iid, url, ts = st["queue"].pop(0)
        if iid in st["published"]:
            continue
        d = None
        try:
            d = item_data(sess, url)
        except Exception as e:
            print("ошибка разбора:", e)
        cap = caption(sess, d) if d else ""
        if TEST:
            print("---", url)
            print(cap or "нечего публиковать")
            done += 1
            continue
        ok = bool(cap) and bool(d["photos"]) and send(sess, d, cap)
        if ok:
            st["published"][iid] = int(now)
            st["last_publish"] = now
            done += 1
            print("опубликовано:", iid)
        else:
            st["failed"][iid] = st["failed"].get(iid, 0) + 1
            if st["failed"][iid] < 2:
                st["queue"].append([iid, url, ts])
                print("  вернём в конец очереди:", iid)
            else:
                print("  брошен:", iid)

    if len(st["published"]) > 50000:
        keep = list(st["published"])[-20000:]
        st["published"] = {k: st["published"][k] for k in keep}
    if len(st["failed"]) > 5000:
        st["failed"] = {}

    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    print("готово, публикаций за запуск:", done)

main()
