# -*- coding: utf-8 -*-
"""
ivi.am -> Telegram («Барахолка Երևան»).

Порядок работы одного запуска:
  1. Публикуем из уже собранной очереди — один пост, быстро.
     Пост выходит, даже если сбор потом не успеет.
  2. Добираем новые объявления, но не дольше MAX_SECONDS.
  3. Состояние сохраняется после каждой ступени.

Настройки сбора подобраны под 25-минутный потолок GitHub Actions: разделам
хватает MAX_PAGES страниц, потому что обновлённое за неделю всегда лежит на
первых двух.

Чтение идёт прямым запросом первым (list.am отвечает ему быстро), а Jina и
открытые прокси остаются запасными: по одной попытке, без повторов.

Фотографии берём только из папки самого объявления (/files/images/items/):
картинки меню, баннеров и рекламных плашек не публикуем. Крупный размер
предпочитаем мелкому: из пары 38576v....jpg и 38576s....jpg остаётся первый.
Закрытые телефоны вида «(0x xxx xxxx)» вырезаем из текста. Подписи кнопок
сайта заголовком не становятся. Заголовок и описание переводим на русский,
не вышло — публикуем оригинал. Без фото объявление пропускаем.

Хвост страницы отрезаем до «похожих объявлений»: иначе в описание попадают
чужие заголовки и цены, а в альбом — чужие картинки. Сравнение идёт без
учёта регистра, потому что сайт пишет «похожие» со строчной буквы.

Знаки «<» и «>» в разборе страниц набраны кодом символа: в переписке такие
сочетания иногда съедаются, и регэксп остаётся пустым.
"""
import os
import re
import json
import time
import html as htmllib
from datetime import datetime, timedelta, timezone

import requests

from fetcher import translate, script, has_cyr, is_translit_hy, led_add

LT = chr(60)
GT = chr(62)

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

SITE        = "ivi"
STATE       = "state-ivi.json"
INTERVAL    = 0          # ритм задаёт расписание (run.py)
PUBLISH_MAX = 1          # публикаций за один запуск
QUEUE_MAX   = 2000       # предел очереди
FRESH_H     = 168        # сколько часов объявление считается свежим (7 суток)
PER_CAT     = 2000       # потолок карточек на раздел
MAX_PAGES   = 2          # сколько страниц раздела листать
MAX_SECONDS = 15 * 60    # сколько секунд отдаём сбору, остальное — запас под потолок
SAMPLE      = 3          # сколько карточек печатать в лог для проверки
TEST        = False      # True -> ничего не отправлять, только показать разбор в логе

BASE = "https://ivi.am"
TZ   = timezone(timedelta(hours=4))          # время сайта (Ереван)

ITEM_IMGS = "/files/images/items/"           # только настоящие фото объявлений

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

# страница защиты вместо данных
BLOCKED = ("just a moment", "performing security verification", "checking your browser",
           "enable javascript", "captcha", "are you a human")

LINK_RE  = re.compile(r"/ru/item/([a-z0-9][a-z0-9\-]*?)-(\d{1,12})\.html", re.I)
IMG_RE   = re.compile(r'(?:src|data-src|data-original|data-lazy)="([^"]+\.(?:jpg|jpeg|png|webp))"', re.I)
PRICE_RE = re.compile(r"(\d[\d\s]{0,12})\s*(֏|\$|€|₽)")
UPD_RE   = re.compile(r"(?:Обновлено|Թարմացվել է)\s*:?\s*([^|]{1,40})")
REL_RE   = re.compile(r"([^|]{0,20}(?:Сегодня|Вчера|այսօր|երեկ)\s+\d{1,2}:\d{2})")
TITLE_RE = re.compile(r'title="([^"]{2,120})"')
MONTHS   = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "мая": 5, "июн": 6,
            "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}

# закрытый телефон: (0x xxx xxxx) и любые похожие наборы «x»
MASK_RE  = re.compile(r"\(?\s*0?\s*x{2,}[\s\-x]*x{2,}\s*\)?", re.I)

# хвост страницы: сравниваем без учёта регистра, сайт пишет «похожие» строчными
CUT_AT = ("похожие", "другие объявления", "последние обявления",
          "նման", "այլ հայտարար")

# служебные фразы сайта, которые не должны попадать в описание
JUNK = ("вход", "добавить объявлен", "мой профиль", "մուտք", "ավելացնել հայտարար",
        "премиум", "premium", "vip", "избранное", "все объявлен", "показать телефон",
        "написать продавцу", "написать автору", "позвонить", "звонить", "жалоб",
        "наверх", "facebook", "հավանել", "тэг", "регион", "категор", "today",
        "հայտարարություն", "состояние", "վիճակ", "просмотр", "դիտում",
        "комментар", "մեկնաբան", "поделиться", "կիսվել")

# подписи кнопок сайта: заголовком объявления быть не должны
BAD_TITLE = ("добавить в избранное", "избранное", "написать продавцу", "написать автору",
             "позвонить", "сравнить", "հավանել", "ավելացնել էջանիշներում")

# картинки, которые к объявлению не относятся
BAD_IMG = ("logo", "avatar", "icon", "sprite", "social", "flag", "banner",
           "brand", "noimage", "no-image", "placeholder", "loading")

# крупный размер фото: 38576v....jpg против 38576s....jpg
BIG_RE = re.compile(r"/\d+v[0-9a-f]{4,}\.(?:jpg|jpeg|png|webp)$", re.I)

# ---------- сеть ----------

def good(text):
    """Страница пригодна: не защита и не пустышка."""
    if not text or len(text) < 1500:
        return False
    low = text.lower()
    return not any(b in low for b in BLOCKED)

def fetch(sess, url):
    """Прямой запрос первым, дальше Jina, дальше открытые прокси. По одной попытке."""
    try:
        r = sess.get(url, headers=HDRS, timeout=25)
        if r.status_code == 200 and good(r.text):
            return r.text
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
                return r.text
            print(f"  прокси {r.status_code}")
        except Exception as e:
            print(f"  прокси сбой {type(e).__name__}")

    print("  чтение не удалось:", url)
    return ""

def plain(h):
    return " ".join(htmllib.unescape(re.sub(LT + "[^" + GT + "]+" + GT, " ", h)).split())

def scrub(text):
    """Убираем закрытые телефоны и лишние пробелы."""
    return " ".join(MASK_RE.sub(" ", text or "").split())

# ---------- разбор ----------

def find_price(txt):
    m = PRICE_RE.search(txt or "")
    if not m:
        return ""
    return " ".join(m.group(1).split()) + " " + m.group(2)

def gallery(h, only_items=False):
    """Фото объявления по порядку. only_items — только папка самого объявления."""
    out = []
    for m in IMG_RE.finditer(h or ""):
        u = htmllib.unescape(m.group(1))
        if u.startswith("//"):
            u = "https:" + u
        elif u.startswith("/"):
            u = BASE + u
        low = u.lower()
        if any(bad in low for bad in BAD_IMG):
            continue
        if only_items and ITEM_IMGS not in low:
            continue
        if u not in out:
            out.append(u)
    return out

def prefer_big(urls):
    """Из пары «крупный / мелкий» оставляем крупный."""
    if not urls:
        return urls
    big = [u for u in urls if BIG_RE.search(u.split("?")[0])]
    return big or urls

def clean_desc(text, limit=700):
    parts = [p.strip(" |·—") for p in re.split(r"\s*\|\s*|" + LT + r"br\s*/?" + GT, text or "")]
    keep = []
    for p in parts:
        if len(p) < 5:
            continue
        low = p.lower()
        if any(j in low for j in JUNK):
            continue
        p = scrub(p)
        if len(p) < 5 or MASK_RE.search(p):
            continue
        keep.append(p)
    return " ".join(keep)[:limit]

def desc_reserve(txt, limit=700):
    """Запасной путь: служебные фразы вырезаем по одной, а не целым куском."""
    tail = txt or ""
    for key in ("Состояние", "Վիճակ", "Просмотр", "Դիտում"):
        k = tail.find(key)
        if k > 0:
            tail = tail[k:]
            break
    low = tail.lower()
    for j in JUNK:
        i = low.find(j)
        while i >= 0:
            tail = tail[:i] + " " + tail[i + len(j):]
            low = tail.lower()
            i = low.find(j)
    return scrub(" ".join(tail.split()))[:limit]

def parse_updated(s):
    """'Сегодня 19:03' / 'Вчера 22:10' / '11 октября' / '11.10.2026' -> unix-время."""
    s = (s or "").strip()
    if not s:
        return None
    now = datetime.now(TZ)
    mt = re.search(r"(\d{1,2}):(\d{2})", s)
    hh, mm = (int(mt.group(1)), int(mt.group(2))) if mt else (23, 59)
    low = s.lower()
    if "сегодня" in low or "այսօր" in low:
        d = now
    elif "вчера" in low or "երեկ" in low:
        d = now - timedelta(days=1)
    else:
        md = re.search(r"(\d{1,2})[.\s]+(\d{1,2})[.\s]+(\d{2,4})", s)
        if md:
            day, mon, year = int(md.group(1)), int(md.group(2)), int(md.group(3))
            year = year + 2000 if year < 100 else year
            try:
                d = datetime(year, mon, day, hh, mm, tzinfo=TZ)
            except ValueError:
                return None
        else:
            mn = re.search(r"(\d{1,2})\s+([А-Яа-яЁё]+)", s)
            if not mn:
                return None
            mon = MONTHS.get(mn.group(2).lower()[:3])
            if not mon:
                return None
            year = now.year
            d = datetime(year, mon, int(mn.group(1)), hh, mm, tzinfo=TZ)
            if d > now + timedelta(days=1):
                d = datetime(year - 1, mon, int(mn.group(1)), hh, mm, tzinfo=TZ)
    return d.replace(hour=hh, minute=mm, second=59, microsecond=0).timestamp()

def is_fresh(ts):
    if not ts:
        return False
    now = time.time()
    return 0 <= now - ts <= FRESH_H * 3600

def card_scan(h, slug, limit):
    """Карточки раздела: id, ссылка, название, цена, «Обновлено», фото."""
    ms = list(LINK_RE.finditer(h))
    out = {}
    skip = SKIP_SUB.get(slug, ())
    i = 0
    while i < len(ms):
        iid = ms[i].group(2)
        j = i + 1
        while j < len(ms) and ms[j].group(2) == iid:
            j += 1                     # карточка ссылается на объявление дважды
        start = ms[i - 1].end() if i > 0 else 0       # включаем открытые теги карточки
        end = ms[j].start() if j < len(ms) else min(len(h), ms[j - 1].end() + 3000)
        win = h[start:end]
        url = BASE + ms[i].group(0)
        i = j
        if iid in out or any(tok in win for tok in skip):
            continue
        txt = plain(win)[:500]
        mu = UPD_RE.search(txt) or REL_RE.search(txt)
        upd = mu.group(1).strip(" |·—") if mu else ""
        mt = TITLE_RE.search(win)
        title = htmllib.unescape(mt.group(1)).strip() if mt else ""
        if not title or any(b in title.lower() for b in BAD_TITLE):
            title = ""                 # кнопки сайта не становятся заголовком
        ph = prefer_big(gallery(win, only_items=True))
        out[iid] = {
            "id": iid,
            "url": url,
            "title": title,
            "price": find_price(txt),
            "updated_txt": upd,
            "ts": parse_updated(upd),
            "photo": ph[0] if ph else "",
        }
        if len(out) >= limit:
            break
    return out

def item_data(sess, url, iid):
    """Фото и описание объявления. Название, цену и место даёт карточка раздела."""
    h = fetch(sess, url)
    if not h:
        return None
    low = h.lower()
    for tail in CUT_AT:
        cut = low.find(tail)
        if cut > 0:
            h = h[:cut]
            break

    photos = prefer_big(gallery(h, only_items=True))
    if not photos:
        m = re.search(r'property="og:image"\s+content="([^"]+)"', h, re.I)
        if m:
            u = htmllib.unescape(m.group(1))
            if ITEM_IMGS in u.lower():
                photos = [u]

    txt = plain(h)

    title = ""
    m = re.search(LT + r"h1[^" + GT + r"]*" + GT + r"(.*?)" + LT + r"/h1" + GT, h, re.S)
    if m:
        title = scrub(plain(m.group(1)))

    # номер объявления ищем тремя способами: «| N <id>», «N <id>», «N: <id>»
    mi = None
    for pat in (r"\|\s*N\s*" + str(iid), r"\bN\s*" + str(iid), r"N\s*[:№]?\s*" + str(iid)):
        mi = re.search(pat, txt)
        if mi:
            break
    head_zone = txt[:mi.start()] if mi else txt[:1500]

    price = find_price(head_zone)

    place = ""
    m = re.search(r"([А-Яа-яЁёA-Za-z][^|" + LT + GT + r"]{2,40}?)\s*\|\s*\d{2}\.\d{2}\.\d{4}", txt)
    if m:
        place = re.sub(r"\s+", " ", m.group(1)).strip(" ,")

    desc = clean_desc(txt[mi.end():]) if mi else ""
    if len(desc) < 20:
        desc = desc_reserve(txt)

    print(f"  {iid}: фото {len(photos)}, цена «{price}», место «{place}», "
          f"описание {len(desc)} знаков, блок {'найден' if mi else 'не найден'}")
    return {"title": title, "price": price, "place": place,
            "desc": desc, "photos": photos[:10], "url": url}

# ---------- текст ----------

def headline(sess, e, d):
    src = scrub((e.get("title") or (d["title"] if d else "")).strip())
    t = scrub(translate(sess, src).strip())
    if is_translit_hy(src) or is_translit_hy(t):
        return ""
    if t and (has_cyr(t) or script(t) == "lat"):
        return " ".join(t.split())[:90]
    alt = scrub(translate(sess, d["desc"][:300]).strip()) if d else ""
    if has_cyr(alt) and len(alt.split()) >= 3:
        return re.split(r"[.!?]\s", alt)[0].strip()[:90]
    return " ".join((t or alt).split())[:90]

def caption(sess, e, d):
    head = headline(sess, e, d)
    if not head:
        return ""
    lines = [head]
    price = e.get("price") or (d["price"] if d else "")
    place = e.get("place") or (d["place"] if d else "")
    desc = scrub(d["desc"]) if d else ""
    if price:
        lines.append(price)
    if place:
        lines.append(place)
    if desc:
        lines.append(scrub(translate(sess, desc)))
    lines = [" ".join(scrub(x).split()) for x in lines if x]
    return "\n\n".join(lines)[:1024]

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
        return bool(r.ok)

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
    return bool(r.ok)

# ---------- состояние ----------

def load_st():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception as e:
            print("state-ivi.json не читается:", e)
    return {}

def save_st(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)

def norm_queue(st):
    """Очередь хранится словарями; старый формат [id, url, ts] тоже принимаем."""
    q = []
    for e in st.get("queue", []):
        if isinstance(e, dict) and e.get("id"):
            q.append(e)
        elif isinstance(e, (list, tuple)) and len(e) >= 3:
            q.append({"id": e[0], "url": e[1], "ts": int(e[2]),
                      "title": "", "price": ""})
    st["queue"] = q

def collect(sess, slug, st, deadline):
    """Карточки раздела, страница за страницей, пока идут новые и есть время."""
    seen = {}
    for page in range(1, MAX_PAGES + 1):
        if time.time() > deadline:
            print("  сбор: время вышло")
            break
        url = f"{BASE}/ru/search/{slug}/" if page == 1 else f"{BASE}/ru/search/{slug}/?page={page}"
        h = fetch(sess, url)
        if not h:
            break
        cards = card_scan(h, slug, PER_CAT)
        new = 0
        for iid, c in cards.items():
            if iid not in seen:
                seen[iid] = c
                new += 1
        print(f"  стр.{page}: карточек {len(cards)}, новых {new}")
        if new == 0 or len(seen) >= PER_CAT:
            break
        time.sleep(1)
    return seen

def publish_one(sess, st, e, now):
    """Одна попытка публикации. 'ok' | 'skip' | 'drop' | 'fail'."""
    iid, url = e["id"], e["url"]
    prev = st["published"].get(iid)
    if prev and prev >= int(e.get("ts") or 0):
        return "skip"                  # выходило и с тех пор не обновлялось
    d = None
    try:
        d = item_data(sess, url, iid)
    except Exception as ex:
        print("ошибка разбора:", ex)
    if not d:
        print("  страница не открылась, пропускаем:", iid)
        return "drop"
    if not d["photos"]:
        print("  без фото, пропускаем:", iid)
        return "drop"
    cap = caption(sess, e, d)
    if not cap:
        print("  без заголовка (латиницей армянский или пусто), пропускаем:", iid)
        return "drop"
    if send(sess, d, cap):
        st["published"][iid] = int(e.get("ts") or now)
        st["last_publish"] = time.time()
        led_add(SITE, iid, now)
        print("опубликовано:", iid, f"фото {len(d['photos'])}")
        return "ok"
    return "fail"

def publish_phase(sess, st, now, limit=PUBLISH_MAX):
    """Публикуем из очереди: не больше limit постов, не больше трёх попыток."""
    done, tried = 0, 0
    while st["queue"] and done < limit and tried < 3:
        if not TEST and now - st["last_publish"] < INTERVAL:
            print("ждём интервал между публикациями")
            break
        tried += 1
        e = st["queue"].pop(0)
        res = publish_one(sess, st, e, now)
        if res == "ok":
            done += 1
        elif res == "fail":
            iid = e["id"]
            st["failed"][iid] = st["failed"].get(iid, 0) + 1
            if st["failed"][iid] < 2:
                st["queue"].append(e)
                print("  вернём в конец очереди:", iid)
            else:
                print("  брошен:", iid)
    return done

def main():
    if not TOKEN or not CHAT_ID:
        print("нет TELEGRAM_TOKEN / TELEGRAM_CHAT_ID")
        return 0

    st = load_st()
    st.setdefault("published", {})   # id -> время обновления объявления
    st.setdefault("failed", {})
    st.setdefault("last_publish", 0)
    norm_queue(st)

    sess = requests.Session()
    now = time.time()
    deadline = now + MAX_SECONDS

    # 1. публикация из уже собранной очереди — до всякого сбора
    done = publish_phase(sess, st, now, PUBLISH_MAX)
    save_st(st)

    if TEST:
        for e in st["queue"][:3]:
            print("---", e["url"])
        return done

    # 2. сбор новых, пока хватает времени
    for slug, name in SECTIONS.items():
        if time.time() > deadline:
            print("сбор остановлен: время вышло")
            break
        cards = collect(sess, slug, st, deadline)
        fresh = [c for c in cards.values() if is_fresh(c["ts"])]
        print(f"[{slug}] {name}: карточек {len(cards)}, свежих за {FRESH_H}ч {len(fresh)}")
        for c in list(cards.values())[:SAMPLE]:
            print(f"    {c['id']}: «{c['title']}» {c['price']} "
                  f"обновлено «{c['updated_txt']}» свежее={is_fresh(c['ts'])}")
        in_queue = {e["id"]: e for e in st["queue"]}
        for c in fresh:
            ts = int(c["ts"])
            prev = st["published"].get(c["id"])
            if prev and prev >= ts:
                continue                  # выходило и с тех пор не обновлялось
            if c["id"] in in_queue:
                if in_queue[c["id"]].get("ts", 0) >= ts:
                    continue              # уже стоит в очереди с тем же обновлением
                in_queue[c["id"]].update(ts=ts, title=c["title"], price=c["price"])
                continue                  # объявление обновили — очередь возьмёт его снова
            st["queue"].append({"id": c["id"], "url": c["url"], "ts": ts,
                                "title": c["title"], "price": c["price"]})
        time.sleep(1)
    save_st(st)

    # 3. старое выкидываем, хвост очереди ограничиваем
    far = time.time() - FRESH_H * 3600
    st["queue"] = [e for e in st["queue"] if e.get("ts", 0) >= far]
    while len(st["queue"]) > QUEUE_MAX:
        st["queue"].pop(0)

    # 4. если очередь была пуста и только что наполнилась — публикуем
    if done == 0 and st["queue"]:
        done += publish_phase(sess, st, time.time(), 1)

    if len(st["published"]) > 50000:
        keep = list(st["published"])[-20000:]
        st["published"] = {k: st["published"][k] for k in keep}
    if len(st["failed"]) > 5000:
        st["failed"] = {}

    save_st(st)
    today = time.strftime("%d.%m.%Y", time.gmtime(time.time() + 4 * 3600))
    n_today = sum(1 for v in st["published"].values()
                  if time.strftime("%d.%m.%Y", time.gmtime(int(v) + 4 * 3600)) == today)
    print(f"готово, публикаций за запуск: {done} | сегодня всего: {n_today}")
    return done

if __name__ == "__main__":
    main()
