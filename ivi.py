# -*- coding: utf-8 -*-
"""
gortsarq.am -> Telegram («Барахолка Երևան»).

Как бот собирает объявление:
  1. Обходит разделы (CATS) и листает страницы раздела, пока идут новые
     карточки; потолок — PER_CAT ссылок на раздел.
  2. Берёт объявления за последнюю неделю по дате на карточке (ДД.ММ.ГГГГ).
     Объявление без даты считает сегодняшним.
  3. Открывает страницу объявления и берёт оттуда фотографии (до 10),
     заголовок, цену, место и описание.
  4. Публикует по одному объявлению за запуск: название, цена, место,
     описание и кнопка «Связаться».

Один id может выйти снова: если продавец обновил объявление позже, чем оно
выходило в группе, оно публикуется заново. У gortsarq на карточке видна
только дата, поэтому сравниваются даты, а не часы.

Закрытые телефоны вида «(0x xxx xxxx)» из текста вырезаем.

Чтение и перевод — из fetcher.py. Заголовок и описание переводим на русский,
не вышло — публикуем оригинал. Армянское слово латиницей («vacharum em
heraxos») не публикуем, берём следующее. Фото — до 10, без фото объявление
пропускаем.

Знаки «<» и «>» в разборе страниц набраны кодом символа (GT): в переписке
такие сочетания иногда съедаются, и регэксп остаётся пустым.
"""
import os
import re
import json
import time

import requests

from fetcher import jina, translate, script, has_cyr, is_translit_hy, led_add

GT = chr(62)

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

SITE       = "gortsarq"
STATE      = "state-gortsarq.json"
INTERVAL   = 0              # ритм задаёт расписание (run.py)
QUEUE_MAX  = 2000           # максимум объявлений в очереди
FRESH_DAYS = 7              # берём объявления за последнюю неделю
PER_CAT    = 2000           # потолок ссылок на раздел
MAX_PAGES  = 5              # сколько страниц раздела листать
RESET_ONCE = False          # однократный сброс очереди (уже сделан)
TEST       = False

BASE = "https://gortsarq.am"

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

def get_items(sess):
    """Ссылки разделов: страница за страницей, пока идут новые."""
    found = {}
    for cat in CATS:
        per_cat = 0
        for page in range(1, MAX_PAGES + 1):
            md = jina(sess, list_url(cat, page))
            if not md:
                break
            n = 0
            for m in ITEM_RE.finditer(md):
                iid = m.group(1)
                path = m.group(0)[len(BASE):].split("]")[0].split(")")[0]
                tail = md[m.end():m.end() + 300]
                d = DATE_RE.search(tail)
                date = d.group(0) if d else ""
                if iid not in found:
                    found[iid] = {"path": path, "date": date}
                    n += 1
                elif date and not found[iid]["date"]:
                    found[iid]["date"] = date
            per_cat += n
            print(f"[{cat}] стр.{page}: новых {n}")
            if n == 0 or per_cat >= PER_CAT:
                break
            time.sleep(3)
    return found

def item_data(sess, path):
    md = jina(sess, BASE + path)
    if not md:
        return None

    html = jina(sess, BASE + path, {"x-respond-with": "html"})
    cut = html.find("Похожие")
    if cut > 0:
        html = html[:cut]

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
    for m in PHOTO_RE.finditer(md + "\n" + html):
        u = m.group(0).split("]")[0].split(")")[0]
        if u not in photos:
            photos.append(u)

    return {"title": title, "price": price, "desc": desc,
            "place": place, "photos": photos[:10], "url": BASE + path}

def headline(sess, d):
    src = scrub((d["title"] or "").strip())
    t = scrub(translate(sess, src).strip())
    if is_translit_hy(src) or is_translit_hy(t):
        return ""
    if t and (has_cyr(t) or script(t) == "lat"):
        return " ".join(t.split())[:90]
    alt = scrub(translate(sess, d["desc"][:300]).strip())
    if has_cyr(alt) and len(alt.split()) >= 3:
        return re.split(r"[.!?]\s", alt)[0].strip()[:90]
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
        return bool(r.ok)

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
    print("telegram:", r.status_code, r.text[:200])
    return bool(r.ok)

def load():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception as e:
            print("state-gortsarq.json не читается:", e)
    return {"queue": [], "last_publish": 0, "published": {}, "failed": {}}

def save(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)

def norm_queue(st):
    """Очередь — словари {id, path, date}; старые строки-пути тоже принимаем."""
    q = []
    for e in st.get("queue", []):
        if isinstance(e, dict) and e.get("path"):
            p = str(e["path"]).split("]")[0].split(")")[0]
            iid = str(e.get("id") or "")
            if not iid:
                m = ITEM_ID_RE.search(p)
                iid = m.group(1) if m else p
            q.append({"id": iid, "path": p, "date": e.get("date", "")})
        elif isinstance(e, str):
            p = e.split("]")[0].split(")")[0]
            m = ITEM_ID_RE.search(p)
            q.append({"id": m.group(1) if m else p, "path": p, "date": ""})
    st["queue"] = q

def norm_published(st):
    """Старые отметки времени превращаем в дату обновления (ДД.ММ.ГГГГ)."""
    pub = st.get("published") or {}
    for k, v in list(pub.items()):
        if isinstance(v, (int, float)):
            pub[k] = time.strftime("%d.%m.%Y", time.gmtime(v + 4 * 3600))
    st["published"] = pub

def main():
    st = load()
    st.setdefault("failed", {})
    st.setdefault("published", {})
    st.setdefault("last_publish", 0)
    norm_queue(st)
    norm_published(st)
    sess = requests.Session()

    if RESET_ONCE and not st.get("reset_done2"):
        st["queue"] = []
        st["failed"] = {}
        st["reset_done2"] = True
        print("очистка: очередь сброшена")

    items = get_items(sess)
    print("всего найдено:", len(items))

    if TEST:
        for iid, it in list(items.items())[:1]:
            d = item_data(sess, it["path"])
            if d:
                print("фото:", len(d["photos"]), "заголовок:", d["title"])
        return 0

    ok_dates = last_days(FRESH_DAYS)
    fresh = []
    for iid, it in items.items():
        if not it["date"]:
            it["date"] = today_str()      # без даты на карточке считаем сегодняшним
        if it["date"] not in ok_dates:
            continue                      # старше недели — не берём вовсе
        if st["published"].get(it["path"]) == it["date"]:
            continue                      # выходило с этой же датой обновления
        fresh.append({"id": iid, "path": it["path"], "date": it["date"]})

    if fresh:
        st["queue"].extend(fresh)
        print("новых в очередь:", len(fresh))
        while len(st["queue"]) > QUEUE_MAX:
            st["queue"].pop(0)

    now = time.time()
    print("свежих за неделю:", len(fresh), "| очередь:", len(st["queue"]))

    done = 0
    if st["queue"] and now - st["last_publish"] >= INTERVAL:
        for _ in range(3):
            if not st["queue"] or done >= 1:
                break
            e = st["queue"].pop(0)
            iid, path = e["id"], e["path"]
            if st["published"].get(path) == e.get("date"):
                continue                  # уже выходило с этой же датой
            ok = False
            d = None
            try:
                d = item_data(sess, path)
                if d and not d["photos"]:
                    print("  без фото, пропускаем:", iid)
                    continue
                cap = make_caption(sess, d) if d else ""
                if d and not cap:
                    print("  без заголовка (латиницей армянский или пусто), пропускаем:", iid)
                    continue
                ok = bool(cap) and bool(d["photos"]) and send(sess, d, cap)
            except Exception as ex:
                print("ошибка публикации:", ex)
                ok = False

            if ok:
                st["last_publish"] = now
                st["published"][path] = e.get("date") or today_str()
                led_add(SITE, iid, now)
                done += 1
                print(f"опубликовано: {iid} ({path}) фото {len(d['photos'])}")
                break

            st["failed"][path] = st["failed"].get(path, 0) + 1
            if st["failed"][path] < 2:
                st["queue"].append(e)
                print(f"  вернём в конец очереди: {path}")
            else:
                print(f"  брошен {path}: не удалось 2 раза")

    if len(st["published"]) > 50000:
        keys = list(st["published"])[-20000:]
        st["published"] = {k: st["published"][k] for k in keys}

    save(st)
    today = today_str()
    n_today = sum(1 for v in st["published"].values() if str(v) == today)
    print(f"готово, публикаций за запуск: {done} | сегодня всего: {n_today}")
    return done

if __name__ == "__main__":
    main()
