# -*- coding: utf-8 -*-
"""
list.am -> Telegram («Барахолка Երևան»).

Новые объявления берутся из карточек разделов: заголовок, цена, место, фото, время.
Публикация не зависит от страницы объявления: если она не читается (403 / защита),
пост всё равно уходит с данными и фото из карточки. Страница объявления —
дополнение: все фото галереи и описание.
"""
import os
import re
import json
import time
import html as htmllib
from datetime import datetime, timedelta, timezone

import requests

TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")

STATE       = "state.json"
INTERVAL = 10 * 60
PUBLISH_MAX = 3           # сколько публикаций за один запуск (1 = как было)
QUEUE_MAX   = 250         # максимум объявлений в очереди
FRESH_H     = 24          # сколько часов объявление считается свежим
TEST        = False       # True -> ничего не отправлять, только лог

BASE = "https://www.list.am"
TZ   = timezone(timedelta(hours=4))          # время сайта (Ереван)

HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "ru,hy;q=0.9,en;q=0.8",
}

SECTIONS = {
    "4":   "Электроника",
    "133": "Дом и сад",
    "27":  "Детский мир",
    "393": "Красота и здоровье",
    "39":  "Хобби и спорт",
    "17":  "Мода и стиль",
}

ITEM_RE  = re.compile(r"/ru/item/(\d+)")
PHOTO_RE = re.compile(r"(?:https?:)?//img\.list\.am/[a-z]+/\d+/\d+\.(?:webp|jpg|jpeg|png)", re.I)
PRICE_RE = re.compile(r"\d[\d\s,]{2,}\s*֏|\$\s?\d[\d\s,]{1,}(?:\.\d{1,2})?|\d[\d\s,]{2,}\s*₽")
DATE_RE  = re.compile(r"(Сегодня|Вчера)\s*,?\s*(\d{1,2}):(\d{2})")
TITLE_RE = re.compile(r"!\[([^\]\n]{2,140})\]\(|alt=\"([^\"]{2,140})\"")
BLOCKED  = ("just a moment", "attention required", "enable javascript",
            "checking your browser", "cloudflare", "performing security verification")

DESC_END = (r"(?:Номер объявления|Пожаловаться|Отзыв|Написать|Позвонить|Похожие|"
            r"Комментарии|Информация о (?:товаре|продавце)|Download App|На главную|"
            r"Report|Смотреть все)")

def is_blocked(text):
    t = (text or "").strip()
    return len(t) < 800 or any(m in t.lower() for m in BLOCKED)

def plain(h):
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", h)).split())

# ---------- разбор карточек раздела ----------

def nearest(pat, text, pos, span=600):
    """Ближайшее совпадение к ссылке — работает и в html, и в markdown."""
    best, bestd = None, 10 ** 9
    for m in pat.finditer(text, max(0, pos - span), min(len(text), pos + span)):
        d = abs(m.start() - pos)
        if d < bestd:
            best, bestd = m, d
    return best

def clean_title(s):
    s = htmllib.unescape(s or "")
    s = re.sub(r"^\s*Image\s*\d+\s*[:.\-]\s*", "", s, flags=re.I)   # метка Jina
    s = re.sub(r"[\\]{1,2}", "", s)
    s = " ".join(s.split())
    return s[:140]

def card_place(text, pos, span=220):
    """Место — последний кусок перед датой, после «›» категории."""
    seg = text[max(0, pos - span):pos]
    if "›" in seg:
        seg = seg.rsplit("›", 1)[1]
    seg = re.sub(r"[\[\]()|*]", " ", seg)
    seg = " ".join(seg.split()).strip(" ,·—")
    return seg[-40:].strip(" ,") if 2 <= len(seg) <= 60 else ""

def full_photo(u):
    """img.list.am/r/692/96450692.webp -> img.list.am/f/692/96450692.webp"""
    u = htmllib.unescape(u or "")
    if u.startswith("//"):
        u = "https:" + u
    return re.sub(r"(img\.list\.am)/[a-z]+/", r"\1/f/", u, count=1)

def parse_cards(text):
    """{id: {id, url, title, price, place, photo, ts}} по карточкам раздела."""
    out = {}
    text = text or ""
    for m in ITEM_RE.finditer(text):
        iid = m.group(1)
        if iid in out:
            continue
        pos = m.start()
        pm = nearest(PHOTO_RE, text, pos)
        pr = nearest(PRICE_RE, text, pos)
        tm = nearest(TITLE_RE, text, pos)
        dm = nearest(DATE_RE, text, pos)

        photo = full_photo(pm.group(0)) if pm else ""
        title = clean_title((tm.group(1) or tm.group(2)) if tm else "")
        price = " ".join(pr.group(0).split()) if pr else ""

        ts, place = 0, ""
        if dm:
            d = datetime.now(TZ).replace(hour=int(dm.group(2)), minute=int(dm.group(3)),
                                         second=0, microsecond=0)
            if dm.group(1).lower().startswith("вчера"):
                d = d - timedelta(days=1)
            ts = int(d.timestamp())
            place = card_place(text, dm.start())

        out[iid] = {"id": iid, "url": f"{BASE}/ru/item/{iid}", "title": title,
                    "price": price, "place": place, "photo": photo, "ts": ts}
    return out

def is_fresh(ts):
    if not ts:
        return False
    now = time.time()
    return 0 <= now - ts <= FRESH_H * 3600

# ---------- страница объявления (дополнение, не обязательно) ----------

def direct(sess, url):
    try:
        r = sess.get(url, headers=HDRS, timeout=40)
        if r.status_code == 200 and len(r.text) > 2000 and not is_blocked(r.text):
            return r.text
        print(f"  прямой вход {r.status_code}, {len(r.text)} знаков")
    except Exception as e:
        print("  прямой вход ошибка:", type(e).__name__)
    return ""

def extract_desc(text):
    if not text:
        return ""
    m = re.search(r"Описание\s*[:：]?\s*(.{30,4000}?)\s*(?:" + DESC_END + r"|\Z)", text, re.S)
    if not m:
        return ""
    s = " ".join(m.group(1).split())
    for junk in ("Пожаловаться", "Написать", "Позвонить", "Download App",
                 "На главную", "Смотреть все"):
        i = s.find(junk)
        if i > 20:
            s = s[:i]
    s = s.strip(" -–—")
    if s.lower() in ("нет", "нет описания") or len(s) < 15:
        return ""
    return s[:700]

def item_extra(sess, iid):
    """Все фото галереи и описание. Неудача публикации не мешает."""
    url = f"{BASE}/ru/item/{iid}"

    html = direct(sess, url)                 # html -> вся галерея
    md = jina(sess, url)                     # markdown -> описание

    photos, desc = {}, ""
    for src in ((html.replace("\\/", "/") if html else ""), md or ""):
        for m in PHOTO_RE.finditer(src):
            u = full_photo(m.group(0))
            photos[u.rsplit("/", 1)[-1]] = u
    if html:
        desc = extract_desc(plain(html))
    if not desc:
        cut = (md or "").find("Похожие")
        desc = extract_desc(md[:cut] if cut > 0 else md)

    print(f"  {iid}: фото {len(photos)}, описание {len(desc)} знаков")
    return {"photos": list(photos.values())[:10], "desc": desc}

# ---------- текст ----------

def translate(sess, text):
    text = (text or "").strip()
    if not text or re.search(r"[А-Яа-яЁё]", text):
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

def make_caption(sess, e, extra):
    head = translate(sess, e["title"]).strip()
    if not head:
        head = translate(sess, e["place"]).strip()
    lines = [head] if head else []
    if e["price"]:
        lines.append(e["price"])
    if e["place"] and e["place"] != head:
        lines.append(e["place"])
    if extra.get("desc"):
        lines.append(translate(sess, extra["desc"][:700]))
    return "\n\n".join(x for x in lines if x)[:1024]

# ---------- Telegram ----------

def send(sess, e, photos, caption):
    markup = {"inline_keyboard": [[{"text": "Связаться", "url": e["url"]}]]}

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
        payload["caption"] = caption
        method = "sendPhoto"
    else:
        payload["text"] = caption
        method = "sendMessage"

    r = sess.post(f"https://api.telegram.org/bot{TOKEN}/{method}",
                  data=payload, timeout=40)
    print("telegram:", r.status_code, r.text[:300])
    return r.ok

# ---------- состояние ----------

def load():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception as e:
            print("state.json не читается:", e)
    return {}

def norm_queue(st):
    """Очередь хранится словарями; старые записи-идентификаторы выбрасываем."""
    q, dropped = [], 0
    for e in st.get("queue", []):
        if isinstance(e, dict) and e.get("id"):
            q.append(e)
        else:
            dropped += 1
    if dropped:
        print(f"очередь: выброшено старых записей {dropped}")
    st["queue"] = q

def main():
    if not TOKEN or not CHAT_ID:
        print("нет TELEGRAM_TOKEN / TELEGRAM_CHAT_ID")
        return

    st = load()
    st.setdefault("seen", [])
    st.setdefault("published", {})
    st.setdefault("failed", {})
    st.setdefault("last_publish", 0)
    norm_queue(st)

    sess = requests.Session()
    now = time.time()

    # 1. карточки разделов: заголовок, цена, место, фото, время
    fresh_cards = {}
    for cat, name in SECTIONS.items():
        md = jina(sess, f"{BASE}/ru/category/{cat}")
        cards = parse_cards(md)
        fresh = {i: c for i, c in cards.items() if is_fresh(c["ts"])}
        print(f"[{cat}] {name}: карточек {len(cards)}, свежих за {FRESH_H}ч {len(fresh)}")
        for c in list(cards.values())[:2]:
            print(f"    {c['id']}: «{c['title']}» {c['price']} {c['place']}")
        fresh_cards.update(fresh)
        time.sleep(3)

    # 2. новое — в очередь
    known = {e["id"] for e in st["queue"]}
    added = 0
    for iid, c in fresh_cards.items():
        if iid in st["seen"] or iid in st["published"] or iid in known:
            continue
        st["seen"].append(iid)
        st["queue"].append(c)
        added += 1
    print(f"новых в очередь: {added}")
    st["seen"] = st["seen"][-100000:]
    while len(st["queue"]) > QUEUE_MAX:
        st["queue"].pop(0)
    print("очередь:", len(st["queue"]))

    if TEST:
        for e in st["queue"][:3]:
            print("---", e["url"], "|", make_caption(sess, e, {"desc": ""}))
        return

    # 3. публикации
    if st["queue"] and now - st["last_publish"] >= INTERVAL:
        sent, tried = 0, 0
        while st["queue"] and sent < PUBLISH_MAX and tried < PUBLISH_MAX * 3:
            tried += 1
            e = st["queue"].pop(0)
            if e["id"] in st["published"]:
                continue

            extra = {"photos": [], "desc": ""}
            try:
                extra = item_extra(sess, e["id"])
            except Exception as ex:
                print("  страница объявления не далась:", ex)

            photos = extra["photos"] or ([e["photo"]] if e["photo"] else [])
            cap = make_caption(sess, e, extra)

            ok = False
            try:
                ok = bool(cap) and bool(photos) and send(sess, e, photos, cap)
            except Exception as ex:
                print("ошибка отправки:", ex)

            if ok:
                st["last_publish"] = now
                st["published"][e["id"]] = now
                sent += 1
                print("опубликовано:", e["id"])
                continue

            st["failed"][e["id"]] = st["failed"].get(e["id"], 0) + 1
            print("  не вышло:", e["id"], "ошибок", st["failed"][e["id"]])
            if st["failed"][e["id"]] < 2:
                st["queue"].append(e)          # вернём в конец очереди
        print("за запуск опубликовано:", sent)
    else:
        print("публиковать нечего или ещё не прошёл интервал")

    if len(st["published"]) > 50000:
        keys = list(st["published"])[-20000:]
        st["published"] = {k: st["published"][k] for k in keys}
    if len(st["failed"]) > 5000:
        st["failed"] = {}

    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    print("готово, в очереди:", len(st["queue"]))

from fetcher import jina

main()
