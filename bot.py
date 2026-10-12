# -*- coding: utf-8 -*-
"""
list.am -> Telegram («Барахолка Երևան»).

Все поля карточки (заголовок, цена, место, фото, время) берутся из одного окна
одной карточки — соседние объявления не подмешиваются. Публикация не зависит от
страницы объявления: если она не читается (403 / защита), пост всё равно уходит
с данными и фото из карточки. Страница объявления — дополнение: заголовок,
галерея (до 10 фото) и описание.
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
ALT_MD   = re.compile(r"!\[([^\]\n]{1,160})\]\((?:https?:)?//img\.list\.am/", re.I)
ALT_HTML = re.compile(r"alt=\"([^\"]{1,160})\"", re.I)
BANNER   = re.compile(r"!\[[^\]]*\]\((?:https?:)?//upa\.list\.am/[^)]*\)", re.I)

# армянские буквы / кириллица / латиница
HY_RE  = re.compile(r"[\u0530-\u058F]")
CYR_RE = re.compile(r"[А-Яа-яЁё]")
LAT_RE = re.compile(r"[A-Za-z]")

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

def script(s):
    """hy — армянский, ru — кириллица, lat — латиница, none — ни одного."""
    s = s or ""
    if HY_RE.search(s):
        return "hy"
    if CYR_RE.search(s):
        return "ru"
    if LAT_RE.search(s):
        return "lat"
    return "none"

def clean_title(s):
    s = htmllib.unescape(s or "")
    s = re.sub(r"^\s*Image\s*\d+\s*[:.\-–—)]*\s*", "", s, flags=re.I)   # метка Jina
    s = re.sub(r"[\\]{1,2}", "", s)
    s = " ".join(s.split())
    return s[:140]

def ok_photo(u):
    u = htmllib.unescape(u or "")
    if u.startswith("//"):
        u = "https:" + u
    return u

def alt_photos(photos):
    """Тот же набор в полном размере — вторая попытка, если альбом не ушёл."""
    return [re.sub(r"(img\.list\.am)/[a-z]+/", r"\1/f/", p, count=1) for p in photos]

# ---------- разбор карточек раздела ----------

def card_title(win, pm):
    """Заголовок строго этой карточки: подпись её картинки, иначе первая строка."""
    title = ""
    for m in ALT_MD.finditer(win):          # подпись привязана к img.list.am
        title = clean_title(m.group(1))
    if len(title) >= 3 and not PHOTO_RE.search(title):
        return title

    if pm:                                  # html-вариант: alt рядом с фото
        around = win[max(0, pm.start() - 300):pm.end() + 120]
        for m in ALT_HTML.finditer(around):
            title = clean_title(m.group(1))
        if len(title) >= 3 and not PHOTO_RE.search(title):
            return title

    for raw in re.sub(r"!\[[^\]]*\]\([^)]*\)", "\n", win).splitlines():
        s = " ".join(htmllib.unescape(raw).split()).strip(" []()|*\\-—")
        if len(s) < 3 or "›" in s or "·" in s or s.isdigit():
            continue
        if PRICE_RE.search(s) or DATE_RE.search(s) or "img.list.am" in s:
            continue
        return clean_title(s)
    return ""

def card_place(win, pos, span=200):
    """Место — последний кусок перед датой, после «›» категории."""
    seg = win[max(0, pos - span):pos]
    if "›" in seg:
        seg = seg.rsplit("›", 1)[1]
    seg = re.sub(r"[\[\]()|*]", " ", seg)
    seg = " ".join(seg.split()).strip(" ,·—")
    if re.search(r"[\d֏$]", seg):           # цена/цифры — не место
        return ""
    return seg[-40:].strip(" ,") if 2 <= len(seg) <= 60 else ""

def parse_cards(text):
    """Каждая карточка разбирается в своём окне — поля не смешиваются."""
    out = {}
    text = BANNER.sub(" ", text or "")
    hits = [(m.group(1), m) for m in ITEM_RE.finditer(text)]
    prev_end = 0
    for iid, m in hits:
        win = text[prev_end:m.start()]
        prev_end = m.end()
        if iid in out:
            continue

        pm = None
        for pm in PHOTO_RE.finditer(win):
            pass                            # последнее фото карточки
        photo = ok_photo(pm.group(0)) if pm else ""

        price = ""
        for pr in PRICE_RE.finditer(win):
            price = " ".join(pr.group(0).split())

        ts, place = 0, ""
        for dm in DATE_RE.finditer(win):
            d = datetime.now(TZ).replace(hour=int(dm.group(2)), minute=int(dm.group(3)),
                                         second=0, microsecond=0)
            if dm.group(1).lower().startswith("вчера"):
                d = d - timedelta(days=1)
            ts = int(d.timestamp())
            place = card_place(win, dm.start())

        out[iid] = {"id": iid, "url": f"{BASE}/ru/item/{iid}",
                    "title": card_title(win, pm), "price": price,
                    "place": place, "photo": photo, "ts": ts}
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
    """Заголовок, все фото галереи (до 10) и описание. Неудача публикации не мешает."""
    url = f"{BASE}/ru/item/{iid}"
    sources = [("прямой", direct(sess, url)),
               ("jina", jina(sess, url)),
               ("jina-html", jina(sess, url, {"x-respond-with": "html"}))]

    photos, desc, title = {}, "", ""
    counts = []
    for name, src in sources:
        if not src:
            counts.append(f"{name}: 0")
            continue
        was = len(photos)
        for m in PHOTO_RE.finditer(src.replace("\\/", "/")):
            u = ok_photo(m.group(0))
            photos[u.rsplit("/", 1)[-1]] = u
        counts.append(f"{name}: {len(photos) - was}")
        if not desc:
            desc = extract_desc(plain(src) if name == "прямой" else src)
        if not title:
            m = re.search(r"^#\s+(.+)$", src, re.M)
            if m:
                title = clean_title(m.group(1))

    print(f"  {iid}: фото {len(photos)} ({', '.join(counts)}), "
          f"описание {len(desc)} знаков, заголовок {'да' if title else 'нет'}")
    return {"photos": list(photos.values())[:10], "desc": desc, "title": title}

# ---------- текст ----------

def translate(sess, text):
    """Перевод не обязателен: не вышло — публикуем как есть."""
    text = (text or "").strip()
    if not text or script(text) != "hy":
        return text
    try:
        r = sess.get("https://translate.googleapis.com/translate_a/single",
                     params={"client": "gtx", "sl": "hy", "tl": "ru",
                             "dt": "t", "q": text[:2000]}, timeout=30)
        res = "".join(p[0] for p in r.json()[0] if p and p[0]).strip()
        if res and script(res) != "lat":
            return res
    except Exception as e:
        print("  перевод недоступен, публикуем оригинал:", type(e).__name__)
    return text

def make_caption(sess, e, extra):
    head = translate(sess, extra.get("title") or e["title"]).strip()
    if not head:
        head = e["place"].strip()
    lines = [head] if head else []
    if e["price"]:
        lines.append(e["price"])
    if e["place"] and e["place"] != head:
        lines.append(e["place"])
    if extra.get("desc"):
        lines.append(translate(sess, extra["desc"][:700]))
    return "\n\n".join(x for x in lines if x)[:1024]

# ---------- Telegram ----------

def telegram(sess, method, data=None, params=None):
    return sess.post(f"https://api.telegram.org/bot{TOKEN}/{method}",
                     data=data, params=params, timeout=60)

def try_album(sess, photos, caption):
    media = [{"type": "photo", "media": photos[0], "caption": caption}]
    for p in photos[1:10]:
        media.append({"type": "photo", "media": p})
    r = telegram(sess, "sendMediaGroup",
                 {"chat_id": CHAT_ID,
                  "media": json.dumps(media, ensure_ascii=False)})
    print("telegram album:", r.status_code, r.text[:200])
    return r

def send(sess, e, photos, caption):
    markup = {"inline_keyboard": [[{"text": "Связаться", "url": e["url"]}]]}

    if len(photos) >= 2:
        r = try_album(sess, photos, caption)
        if not r.ok:
            alt = alt_photos(photos)
            if alt != photos:
                r = try_album(sess, alt, caption)
        if not r.ok:
            r1 = telegram(sess, "sendPhoto",
                          {"chat_id": CHAT_ID, "photo": photos[0],
                           "caption": caption, "reply_markup": json.dumps(markup)})
            print("telegram fallback:", r1.status_code, r1.text[:200])
            return r1.ok

        mid = None
        try:
            res = r.json().get("result") or []
            if res:
                mid = res[0]["message_id"]
        except Exception:
            mid = None

        data = {"chat_id": CHAT_ID, "text": "Связаться с продавцом",
                "reply_markup": json.dumps(markup)}
        if mid:
            data["reply_parameters"] = json.dumps({"message_id": mid})
        r2 = telegram(sess, "sendMessage", data)
        print("telegram кнопка:", r2.status_code, r2.text[:200])
        return r.ok

    data = {"chat_id": CHAT_ID, "reply_markup": json.dumps(markup)}
    if photos:
        data["photo"] = photos[0]
        data["caption"] = caption
        method = "sendPhoto"
    else:
        data["text"] = caption
        method = "sendMessage"

    r = telegram(sess, method, data)
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

    # 2. новое — в очередь (латиница не берётся)
    known = {e["id"] for e in st["queue"]}
    added, latin = 0, 0
    for iid, c in fresh_cards.items():
        if iid in st["seen"] or iid in st["published"] or iid in known:
            continue
        st["seen"].append(iid)
        if script(c["title"]) == "lat":
            latin += 1
            print(f"    латиница, в очередь не идёт: {iid} «{c['title'][:60]}»")
            continue
        st["queue"].append(c)
        added += 1
    print(f"новых в очередь: {added}, отброшено латиницы: {latin}")
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
            if script(e["title"]) == "lat":
                print("  латиница, из очереди убрано:", e["id"])
                continue

            extra = {"photos": [], "desc": "", "title": ""}
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
                print("опубликовано:", e["id"], f"фото {len(photos)}")
                continue

            st["failed"][e["id"]] = st["failed"].get(e["id"], 0) + 1
            print("  не вышло:", e["id"], "ошибок", st["failed"][e["id"]])
            if st["failed"][e["id"]] < 2:
                st["queue"].append(e)          # вернём в конец очереди
        print("за запуск опубликовано:", sent)
    else:
        print("публиковать нечего или ещё не прошёл интервал")

    if len(st["published"]) > 50000:
        keys = list(st["published"])[-50000:]
        st["published"] = {k: st["published"][k] for k in keys}
    if len(st["failed"]) > 5000:
        st["failed"] = {}

    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    print("готово, в очереди:", len(st["queue"]))

from fetcher import jina

main()
