# -*- coding: utf-8 -*-
"""
ivi.am -> Telegram («Барахолка Երևան»).

Один запуск = один пост (ритм задаёт run.py). Публикуем объявления,
обновлённые продавцом за последнюю неделю (FRESH_H = 168): «Сегодня 19:03»,
«Вчера 22:10», «11.10.2026», «11 октября». Объявление 2023 года,
обновлённое вчера, — свежее.

Один id может выйти снова: если продавец обновил объявление позже, чем оно
выходило в группе, оно публикуется заново. Сравнивается не id, а время
обновления с временем нашей публикации.

Раздел смотрим до PER_CAT карточек — потолок 2000.

Заголовок и описание переводим на русский (цепочка в fetcher.py); не вышло —
публикуем оригинал. Армянское слово латиницей («vacharum em heraxos») не
публикуем, берём следующее. Фото — до 10, без фото объявление пропускаем.
Название, цена и место берутся из карточки раздела, фото — только этого
объявления, описание — из блока после строки «N <id>».

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
PER_CAT     = 2000       # потолок карточек в разделе
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
PRICE_RE = re.compile(r"(\d[\d\s]{0,12})\s*(֏|\$|€|₽)")
UPD_RE   = re.compile(r"(?:Обновлено|Թարմացվել է)\s*:?\s*([^|]{1,40})")
REL_RE   = re.compile(r"([^|]{0,20}(?:Сегодня|Вчера|այսօր|երեկ)\s+\d{1,2}:\d{2})")
TITLE_RE = re.compile(r'title="([^"]{2,120})"')
MONTHS   = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "мая": 5, "июн": 6,
            "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}

# служебные фразы сайта, которые не должны попадать в описание
JUNK = ("вход", "добавить объявлен", "мой профиль", "մուտք", "ավելացնել հայտարար",
        "премиум", "premium", "vip", "избранное", "все объявлен", "показать телефон",
        "написать продавцу", "сообщить", "жалоб", "наверх", "facebook", "հավանել",
        "тэг", "регион", "категор", "today", "հայտարարություն")

def photo_re(iid):
    """Ссылки на фото только этого объявления: имя файла начинается с его id."""
    return re.compile(
        r'(?:src|data-src|data-original|data-lazy)="'
        r'([^"]*?/files/images/items/\d+/' + str(iid) +
        r'[0-9a-z]*\.(?:jpg|jpeg|png|webp))"', re.I)

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
    return " ".join(htmllib.unescape(re.sub(LT + "[^" + GT + "]+" + GT, " ", h)).split())

# ---------- разбор ----------

def find_price(txt):
    m = PRICE_RE.search(txt or "")
    if not m:
        return ""
    return " ".join(m.group(1).split()) + " " + m.group(2)

def clean_desc(text, limit=700):
    parts = [p.strip(" |·—") for p in re.split(r"\s*\|\s*|" + LT + r"br\s*/?" + GT, text or "")]
    keep = []
    for p in parts:
        if len(p) < 5:
            continue
        low = p.lower()
        if any(j in low for j in JUNK):
            continue
        keep.append(p)
    return " ".join(keep)[:limit]

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
        ph = photo_re(iid).search(win)
        out[iid] = {
            "id": iid,
            "url": url,
            "title": htmllib.unescape(mt.group(1)).strip() if mt else "",
            "price": find_price(txt),
            "updated_txt": upd,
            "ts": parse_updated(upd),
            "photo": ph.group(1) if ph else "",
        }
        if len(out) >= limit:
            break
    return out

def item_data(sess, url, iid):
    """Фото и описание объявления. Название и цену даёт карточка раздела."""
    h = fetch(sess, url)
    if not h:
        return None
    for tail in ("Похожие", "Последние Обявления", "Նման", "Այլ հայտարարություններ"):
        cut = h.find(tail)
        if cut > 0:
            h = h[:cut]
            break

    photos = []
    for u in photo_re(iid).findall(h):
        if u.startswith("//"):
            u = "https:" + u
        if u not in photos:
            photos.append(u)
    if not photos:
        m = re.search(r'property="og:image"\s+content="([^"]+)"', h, re.I)
        if m:
            photos = [htmllib.unescape(m.group(1))]

    txt = plain(h)

    title = ""
    m = re.search(LT + r"h1[^" + GT + r"]*" + GT + r"(.*?)" + LT + r"/h1" + GT, h, re.S)
    if m:
        title = plain(m.group(1))

    # цена — только из шапки объявления, до строки «... | N <id>»
    mi = re.search(r"\|\s*N\s*" + str(iid), txt)
    head_zone = txt[:mi.start()] if mi else txt[:1500]
    price = find_price(head_zone)

    place = ""
    m = re.search(r"([А-Яа-яЁёA-Za-z][^|" + LT + GT + r"]{2,40}?)\s*\|\s*\d{2}\.\d{2}\.\d{4}", txt)
    if m:
        place = re.sub(r"\s+", " ", m.group(1)).strip(" ,")

    # описание — от строки «| N <id>» до конца, без служебных фраз
    desc = clean_desc(txt[mi.end():]) if mi else ""

    print(f"  {iid}: фото {len(photos)}, цена «{price}», место «{place}», "
          f"описание {len(desc)} знаков")
    return {"title": title, "price": price, "place": place,
            "desc": desc, "photos": photos[:10], "url": url}

# ---------- текст ----------

def headline(sess, e, d):
    src = (e.get("title") or (d["title"] if d else "")).strip()
    t = translate(sess, src).strip()
    if is_translit_hy(src) or is_translit_hy(t):
        return ""
    if t and (has_cyr(t) or script(t) == "lat"):
        return t[:90]
    alt = translate(sess, d["desc"][:300]).strip() if d else ""
    if has_cyr(alt) and len(alt.split()) >= 3:
        return re.split(r"[.!?]\s", alt)[0].strip()[:90]
    return (t or alt)[:90]

def caption(sess, e, d):
    head = headline(sess, e, d)
    if not head:
        return ""
    lines = [head]
    price = e.get("price") or (d["price"] if d else "")
    place = e.get("place") or (d["place"] if d else "")
    desc = d["desc"] if d else ""
    if price:
        lines.append(price)
    if place:
        lines.append(place)
    if desc:
        lines.append(translate(sess, desc))
    lines = [" ".join(x.split()) for x in lines if x]
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

    # 1. все объявления, обновлённые за последнюю неделю
    for slug, name in SECTIONS.items():
        h = fetch(sess, f"{BASE}/ru/search/{slug}/")
        cards = card_scan(h, slug, PER_CAT)
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

    # 2. старое выкидываем, хвост очереди ограничиваем
    far = time.time() - FRESH_H * 3600
    st["queue"] = [e for e in st["queue"] if e.get("ts", 0) >= far]
    while len(st["queue"]) > QUEUE_MAX:
        st["queue"].pop(0)
    print("в очереди:", len(st["queue"]))

    if TEST:
        for e in st["queue"][:3]:
            print("---", e["url"])
        return 0

    # 3. публикация — ровно один пост за запуск
    done, tried = 0, 0
    while st["queue"] and done < PUBLISH_MAX and tried < PUBLISH_MAX * 3:
        if not TEST and now - st["last_publish"] < INTERVAL:
            print("ждём интервал между публикациями")
            break
        tried += 1
        e = st["queue"].pop(0)
        iid, url = e["id"], e["url"]
        prev = st["published"].get(iid)
        if prev and prev >= int(e.get("ts") or 0):
            continue                      # уже выходило, обновления не было
        d = None
        try:
            d = item_data(sess, url, iid)
        except Exception as ex:
            print("ошибка разбора:", ex)
        if not d:
            print("  страница не открылась, пропускаем:", iid)
            continue
        if not d["photos"]:
            print("  без фото, пропускаем:", iid)
            continue
        cap = caption(sess, e, d)
        if not cap:
            print("  без заголовка (латиницей армянский или пусто), пропускаем:", iid)
            continue
        ok = bool(send(sess, d, cap))
        if ok:
            st["published"][iid] = int(e.get("ts") or now)
            st["last_publish"] = now
            led_add(SITE, iid, now)
            done += 1
            print("опубликовано:", iid, f"фото {len(d['photos'])}")
        else:
            st["failed"][iid] = st["failed"].get(iid, 0) + 1
            if st["failed"][iid] < 2:
                st["queue"].append(e)
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
    return done

if __name__ == "__main__":
    main()
