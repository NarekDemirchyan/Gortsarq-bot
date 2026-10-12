# -*- coding: utf-8 -*-
"""
list.am -> Telegram («Барахолка Երևան»).

Один запуск = один пост. Ритм задаёт run.py (слоты по 15 минут), поэтому
INTERVAL здесь нулевой: ждать внутри запуска нечего.

Заголовок и описание переводим на русский (цепочка переводчиков в fetcher.py).
Не вышло — публикуем оригинал. Армянское слово латиницей («vacharum em heraxos»)
не публикуем: объявление пропускаем, берём следующее.
Фото — до 10 (больше Telegram не даёт), без фото объявление пропускаем.
Свежим считаем объявление, обновлённое за последние 24 часа, даже если
выставлено оно давно (в карточке «Сегодня 15:35», «11.10.2026», «12 октября»).
id каждого поста уходит в общую тетрадь published.json.

Поля карточки читаются из одного окна одной карточки — соседние не подмешиваются.
Галерею объявления берём браузером (playwright в workflow), при неудаче — jina.
"""
import os
import re
import json
import time
import html as htmllib
from datetime import datetime, timedelta, timezone

import requests

from fetcher import jina, translate, script, is_translit_hy, led_has, led_add

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

SITE        = "list"
STATE       = "state.json"
INTERVAL    = 0           # ритм задаёт расписание (run.py)
PUBLISH_MAX = 1           # публикаций за один запуск
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

ITEM_RE   = re.compile(r"/ru/item/(\d+)")
PHOTO_RE  = re.compile(r"(?:https?:)?//img\.list\.am/[a-z]+/\d+/\d+\.(?:webp|jpg|jpeg|png)", re.I)
PRICE_RE  = re.compile(r"\d[\d\s,]{2,}\s*֏|\$\s?\d[\d\s,]{1,}(?:\.\d{1,2})?|\d[\d\s,]{2,}\s*₽")
DATE_RE   = re.compile(r"(Сегодня|Вчера)\s*,?\s*(\d{1,2}):(\d{2})")
FULLDATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})")
MONTHDATE_RE = re.compile(r"(\d{1,2})\s+([А-Яа-яЁё]{3,})")
ALT_MD    = re.compile(r"!\[([^\]\n]{1,160})\]\((?:https?:)?//img\.list\.am/", re.I)
ALT_HTML  = re.compile(r"alt=\"([^\"]{1,160})\"", re.I)
H1_HTML   = re.compile(r"<h1[^>]*>(.{2,200}?)</h1>", re.S | re.I)
TITLE_TAG = re.compile(r"<title[^>]*>(.{2,200}?)</title>", re.S | re.I)
BANNER    = re.compile(r"!\[[^\]]*\]\((?:https?:)?//upa\.list\.am/[^)]*\)", re.I)
JUNK_TXT  = re.compile(r"list\.am|https?://|www\.|@|\+\d{2}\s?\d", re.I)

MONTHS = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "мая": 5, "июн": 6,
          "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}

BLOCKED = ("just a moment", "attention required", "enable javascript",
           "checking your browser", "cloudflare", "performing security verification")

DESC_END = (r"(?:Номер объявления|Пожаловаться|Отзыв|Написать|Позвонить|Похожие|"
            r"Комментарии|Информация о (?:товаре|продавце)|Download App|На главную|"
            r"Report|Смотреть все)")

def is_blocked(text):
    t = (text or "").strip()
    return len(t) < 800 or any(m in t.lower() for m in BLOCKED)

def plain(h):
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h or "", flags=re.S | re.I)
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", h)).split())

def clean_title(s):
    s = htmllib.unescape(s or "")
    s = re.sub(r"^\s*Image\s*\d+\s*[:.\-–—)]*\s*", "", s, flags=re.I)
    s = re.sub(r"\s*[-|–—]?\s*(www\.)?list\.am.*$", "", s, flags=re.I)
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

# ---------- браузер: только он видит галерею ----------

_pw = None
_browser = None

def browser_html(url, wait_ms=2500):
    global _pw, _browser
    try:
        if _browser is None:
            from playwright.sync_api import sync_playwright
            _pw = sync_playwright().start()
            _browser = _pw.chromium.launch(args=["--no-sandbox"])
        page = _browser.new_page(user_agent=HDRS["User-Agent"],
                                 locale="ru-RU", viewport={"width": 1366, "height": 900})
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(wait_ms)
            try:
                page.mouse.wheel(0, 1200)      # дотолкать lazy-загрузку
                page.wait_for_timeout(800)
            except Exception:
                pass
            html = page.content()
        finally:
            page.close()
        return html
    except Exception as e:
        print("  браузер не открыл страницу:", type(e).__name__, str(e)[:120])
        return ""

def browser_stop():
    global _pw, _browser
    try:
        if _browser:
            _browser.close()
        if _pw:
            _pw.stop()
    except Exception:
        pass
    _browser = None
    _pw = None

# ---------- разбор карточек раздела ----------

def card_title(win, pm):
    """Заголовок строго этой карточки: подпись её картинки, иначе первая строка."""
    title = ""
    for m in ALT_MD.finditer(win):
        title = clean_title(m.group(1))
    if len(title) >= 3 and not PHOTO_RE.search(title):
        return title

    if pm:
        around = win[max(0, pm.start() - 300):pm.end() + 120]
        for m in ALT_HTML.finditer(around):
            title = clean_title(m.group(1))
        if len(title) >= 3 and not PHOTO_RE.search(title):
            return title

    for raw in re.sub(r"!\[[^\]]*\]\([^)]*\)", "\n", win).splitlines():
        s = " ".join(htmllib.unescape(raw).split()).strip(" []()|*\\-—")
        if len(s) < 3 or s.isdigit() or "img.list.am" in s:
            continue
        if DATE_RE.search(s) or "›" in s:
            continue
        s = clean_title(PRICE_RE.sub(" ", s))       # цена в той же строке — не помеха
        if len(s) >= 3 and not PHOTO_RE.search(s):
            return s
    return ""

def card_place(win, pos, span=200):
    """Место — последний кусок перед датой, после «›» категории."""
    seg = win[max(0, pos - span):pos]
    if "›" in seg:
        seg = seg.rsplit("›", 1)[1]
    seg = re.sub(r"[\[\]()|*]", " ", seg)
    seg = " ".join(seg.split()).strip(" ,·—")
    if re.search(r"[\d֏$]", seg) or JUNK_TXT.search(seg):
        return ""
    return seg[-40:].strip(" ,") if 2 <= len(seg) <= 60 else ""

def card_time(win):
    """Время обновления карточки: unix-время и позиция даты (место ищем рядом)."""
    now = datetime.now(TZ)
    for dm in DATE_RE.finditer(win):
        d = now.replace(hour=int(dm.group(2)), minute=int(dm.group(3)), second=0, microsecond=0)
        if dm.group(1).lower().startswith("вчера"):
            d = d - timedelta(days=1)
        return int(d.timestamp()), dm.start()
    for fm in FULLDATE_RE.finditer(win):
        day, mon, year = int(fm.group(1)), int(fm.group(2)), int(fm.group(3))
        year = year + 2000 if year < 100 else year
        try:
            d = datetime(year, mon, day, 23, 59, tzinfo=TZ)
        except ValueError:
            continue
        return int(d.timestamp()), fm.start()
    for mm in MONTHDATE_RE.finditer(win):
        mon = MONTHS.get(mm.group(2).lower()[:3])
        if not mon:
            continue
        day = int(mm.group(1))
        d = datetime(now.year, mon, day, 23, 59, tzinfo=TZ)
        if d > now + timedelta(days=1):
            d = datetime(now.year - 1, mon, day, 23, 59, tzinfo=TZ)
        return int(d.timestamp()), mm.start()
    return 0, 0

def parse_cards(text):
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
            pass
        photo = ok_photo(pm.group(0)) if pm else ""

        price = ""
        for pr in PRICE_RE.finditer(win):
            price = " ".join(pr.group(0).split())

        ts, dpos = card_time(win)
        place = card_place(win, dpos) if dpos else ""

        out[iid] = {"id": iid, "url": f"{BASE}/ru/item/{iid}",
                    "title": card_title(win, pm), "price": price,
                    "place": place, "photo": photo, "ts": ts}
    return out

def is_fresh(ts):
    if not ts:
        return False
    now = time.time()
    return 0 <= now - ts <= FRESH_H * 3600

# ---------- страница объявления ----------

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
    """До 10 фото галереи, описание и заголовок. Неудача публикации не мешает."""
    url = f"{BASE}/ru/item/{iid}"
    photos, state, counts = {}, {"desc": "", "title": ""}, []

    def take(name, src, cut_similar=False):
        if not src:
            counts.append(f"{name}: –")
            return
        if cut_similar:
            low = src.lower()
            for mark in ("похожие объявления", "похожие", "similar ads"):
                i = low.find(mark)
                if i > 1000:
                    src = src[:i]
                    break
        was = len(photos)
        for m in PHOTO_RE.finditer(src.replace("\\/", "/")):
            u = ok_photo(m.group(0))
            photos[u.rsplit("/", 1)[-1]] = u
        counts.append(f"{name}: {len(photos) - was}")
        if not state["desc"]:
            state["desc"] = extract_desc(plain(src) if "<" in src[:400] else src)
        if not state["title"]:
            m = re.search(r"^#\s+(.+)$", src, re.M) or H1_HTML.search(src) or TITLE_TAG.search(src)
            if m:
                t = clean_title(plain(m.group(1)) if "<" in m.group(1) else m.group(1))
                if len(t) >= 3 and not JUNK_TXT.search(t):
                    state["title"] = t

    take("браузер", browser_html(url), cut_similar=True)
    if len(photos) < 2 or not state["desc"]:
        take("jina", jina(sess, url))
    if len(photos) < 2:
        take("jina-html", jina(sess, url, {"x-respond-with": "html"}))
    if len(photos) < 2:
        take("прямой", direct(sess, url))

    print(f"  {iid}: фото {len(photos)} ({', '.join(counts)}), "
          f"описание {len(state['desc'])} знаков, заголовок {'да' if state['title'] else 'нет'}")
    return {"photos": list(photos.values())[:10], "desc": state["desc"], "title": state["title"]}

# ---------- текст ----------

def make_caption(sess, e, extra):
    head = translate(sess, extra.get("title") or e["title"]).strip()
    if not head or JUNK_TXT.search(head) or is_translit_hy(head):
        head = e["place"].strip()
    if JUNK_TXT.search(head):
        head = ""
    lines = [head] if head else []
    if e["price"]:
        lines.append(e["price"])
    if e["place"] and e["place"] != head:
        lines.append(e["place"])
    if extra.get("desc"):
        lines.append(translate(sess, extra["desc"][:700]))
    return "\n\n".join(x for x in lines if x)[:1024]

# ---------- Telegram ----------

def telegram(sess, method, data=None):
    return sess.post(f"https://api.telegram.org/bot{TOKEN}/{method}",
                     data=data, timeout=60)

def try_album(sess, photos, caption):
    media = [{"type": "photo", "media": photos[0], "caption": caption}]
    for p in photos[1:10]:
        media.append({"type": "photo", "media": p})
    r = telegram(sess, "sendMediaGroup",
                 {"chat_id": CHAT_ID, "media": json.dumps(media, ensure_ascii=False)})
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
        return 0

    st = load()
    st.setdefault("seen", [])
    st.setdefault("published", {})
    st.setdefault("failed", {})
    st.setdefault("last_publish", 0)
    norm_queue(st)

    sess = requests.Session()
    now = time.time()

    # 1. карточки разделов
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
    added, latin, translit = 0, 0, 0
    for iid, c in fresh_cards.items():
        if (iid in st["seen"] or iid in st["published"]
                or iid in known or led_has(SITE, iid)):
            continue
        st["seen"].append(iid)
        if is_translit_hy(c["title"]):
            translit += 1
            print(f"    латиницей армянский, пропускаем: {iid} «{c['title'][:60]}»")
            continue
        if script(c["title"]) == "lat":
            latin += 1
            print(f"    латиница, пойдёт как есть: {iid} «{c['title'][:60]}»")
        st["queue"].append(c)
        added += 1
    print(f"новых в очередь: {added}, из них латиницей: {latin}, пропущено транслитом: {translit}")
    st["seen"] = st["seen"][-100000:]
    while len(st["queue"]) > QUEUE_MAX:
        st["queue"].pop(0)
    print("очередь:", len(st["queue"]))

    if TEST:
        for e in st["queue"][:3]:
            print("---", e["url"], "|", make_caption(sess, e, {"desc": ""}))
        browser_stop()
        return 0

    # 3. публикация — ровно один пост за запуск
    sent, tried = 0, 0
    while st["queue"] and sent < PUBLISH_MAX and tried < PUBLISH_MAX * 3:
        if now - st["last_publish"] < INTERVAL:
            break
        tried += 1
        e = st["queue"].pop(0)
        if e["id"] in st["published"] or led_has(SITE, e["id"]):
            continue

        extra = {"photos": [], "desc": "", "title": ""}
        try:
            extra = item_extra(sess, e["id"])
        except Exception as ex:
            print("  страница объявления не далась:", ex)

        head = extra.get("title") or e["title"]
        if is_translit_hy(head):
            print("  латиницей армянский, пропускаем:", e["id"], f"«{head[:50]}»")
            continue

        photos = extra["photos"] or ([e["photo"]] if e["photo"] else [])
        if not photos:
            print("  без фото, пропускаем:", e["id"])
            continue

        cap = make_caption(sess, e, extra)
        ok = False
        try:
            ok = bool(cap) and send(sess, e, photos, cap)
        except Exception as ex:
            print("ошибка отправки:", ex)

        if ok:
            st["last_publish"] = now
            st["published"][e["id"]] = now
            led_add(SITE, e["id"], now)
            sent += 1
            print("опубликовано:", e["id"], f"фото {len(photos)}")
            continue

        st["failed"][e["id"]] = st["failed"].get(e["id"], 0) + 1
        print("  не вышло:", e["id"], "ошибок", st["failed"][e["id"]])
        if st["failed"][e["id"]] < 2:
            st["queue"].append(e)

    if not st["queue"]:
        print("публиковать нечего: очередь пуста")
    print("за запуск опубликовано:", sent)

    if len(st["published"]) > 50000:
        keys = list(st["published"])[-50000:]
        st["published"] = {k: st["published"][k] for k in keys}
    if len(st["failed"]) > 5000:
        st["failed"] = {}

    browser_stop()
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    print("готово, в очереди:", len(st["queue"]))
    return sent

if __name__ == "__main__":
    main()
