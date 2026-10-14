# -*- coding: utf-8 -*-
"""
gortsarq.am -> Telegram («Բարախոլկա Երևան»).

Պորդանոկ աշխատանքի մեկ գործարկման:
  1. Հրապարակում ենք պատրաստի հերթից — մեկ գրառում, արագ։
  2. Հավաքում ենք վերջին շաբաթվա հայտարարությունները, բայց ոչ ավելի քան MAX_SECONDS։
  3. Վիճակը պահվում է ամեն աստիճանից հետո։

Բաժինը թերթվում է, քանի դեռ էջում կա FRESH_DAYS օրվա թարմ հայտարարություն։

Հայտարարության էջը կարդացվում է մեկ անգամ՝ markdown-ը բավական է և՛ վերնագրի, և՛ գնի, և՛ նկարների համար։
Նկարները բոտն ինքն է իջեցնում ու ուղարկում ֆայլով. ուղիղ հղումով Telegram-ը դրանք չի քաշում։
Նկար չեղավ կամ չիջավ՝ հայտարարությունը գնում է տեքստով, ոչ թե դեն է նետվում։
Ալբոմով գրառմանը կոճակը կցվում է առանձին հաղորդագրությամբ (ալբոմը reply_markup չի ընդունում)։
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
MAX_TRIES   = 15             # сколько объявлений пробуем за один запуск
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
    """Прямой запрос первым, дальше Jina, дальше прокси. По одной попытке."""
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

# ---------- նկարներ ----------

def thumb_urls(u):
    """Մեկ նկարի տարբերակները՝ սկզբում մանրը, վերջում՝ ամբողջականը։"""
    out = []
    if "/images/detailed/" in u:
        tail = u.split("/images/detailed/", 1)[1]
        for size in ("480/480", "800/800", "1024/1024"):
            out.append(f"{BASE}/images/thumbnails/{size}/detailed/{tail}")
    out.append(u)
    return out

def relay_urls(u):
    """Շրջանցիկ հասցե. նկարը վերցնում է ուրիշ սերվեր ու տալիս մեզ։"""
    bare = u.split("://", 1)[-1]
    return [f"https://images.weserv.nl/?url={bare}"]

def img_ok(r):
    """Նման է նկարի՝ ոչ սխալ, ոչ դատարկ ու ոչ HTML։"""
    b = r.content or b""
    if r.status_code != 200 or len(b) < 1200:
        return False
    return not b.lstrip()[:1] == b"<"

def bytes_from(sess, url, ref):
    """Նկարի բայթերը՝ ուղիղ Referer-ով, հետո proxy-ներով, հետո փոխանցիչով։"""
    h = dict(HDRS)
    h["Referer"] = ref or BASE + "/"
    h["Accept"] = "image/avif,image/webp,image/jpeg,image/*,*/*;q=0.8"

    try:
        r = sess.get(url, headers=h, timeout=30)
        if img_ok(r):
            return r.content
        print(f"  նկար ուղիղ {r.status_code}, {len(r.content or b'')} բայթ")
    except Exception as e:
        print(f"  նկար ուղիղ սխալ {type(e).__name__}")

    for p in ("https://api.allorigins.win/raw", "https://api.codetabs.com/v1/proxy"):
        try:
            r = sess.get(p, params={"url": url}, headers=h, timeout=60)
            if img_ok(r):
                print("  նկար proxy")
                return r.content
            print(f"  նկար proxy {r.status_code}")
        except Exception as e:
            print(f"  նկար proxy սխալ {type(e).__name__}")

    for p in relay_urls(url):
        try:
            r = sess.get(p, headers=h, timeout=60)
            if img_ok(r):
                print("  նկար փոխանցիչ")
                return r.content
            print(f"  նկար փոխանցիչ {r.status_code}")
        except Exception as e:
            print(f"  նկար փոխանցիչ սխալ {type(e).__name__}")
    return None

def photo_bytes(sess, url, ref):
    """Նկարը ներբեռնում ենք ինքներս։ Ուղիղ հղումով Telegram-ը դա չի անում։"""
    for u in thumb_urls(url):
        b = bytes_from(sess, u, ref)
        if b:
            return b
    print("  նկարը չներբեռնվեց:", url)
    return None

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
    """Նկարները ներբեռնում ենք ինքներս ու ուղարկում ֆայլով. ուղիղ հղումով Telegram-ը
    դրանք չի քաշում։ Նկար չեղավ կամ չիջավ՝ ուղարկում ենք տեքստով, որ խումբը չքնի։
    Ալբոմից հետո կոճակը գնում է առանձին հաղորդագրությամբ։"""
    markup = {"inline_keyboard": [[{"text": "Связаться", "url": d["url"]}]]}

    blobs = []
    for u in d["photos"][:10]:
        b = photo_bytes(sess, u, d["url"])
        if b:
            blobs.append(b)

    if not blobs:
        r = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": caption,
                            "reply_markup": json.dumps(markup)},
                      timeout=40)
        print("telegram текст (без фото):", r.status_code, r.text[:200])
        return bool(r.ok)

    if len(blobs) >= 2:
        files = {f"p{i}": (f"p{i}.jpg", b) for i, b in enumerate(blobs)}
        media = [{"type": "photo", "media": "attach://p0", "caption": caption}]
        for i in range(1, len(blobs)):
            media.append({"type": "photo", "media": f"attach://p{i}"})
        r = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendMediaGroup",
                      data={"chat_id": CHAT_ID,
                            "media": json.dumps(media, ensure_ascii=False)},
                      files=files, timeout=120)
        print("telegram album:", r.status_code, r.text[:200])
        if r.ok:
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
            return bool(r2.ok)
        print("  ալբոմը չանցավ, փորձում ենք մեկ նկարով")

    r = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                  data={"chat_id": CHAT_ID, "caption": caption,
                        "reply_markup": json.dumps(markup)},
                  files={"photo": ("p.jpg", blobs[0])}, timeout=90)
    print("telegram:", r.status_code, r.text[:200])
    return bool(r.ok)

# ---------- состояние ----------

def load():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception as e:
            print("state-gortsarq.json не читается:", e)
    return {"queue": [], "last_publish": 0, "published": {}, "failed": {}, "skipped": {}}

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

# ---------- публикация ----------

def publish_one(sess, st, e, now):
    """Одна попытка публикации. 'ok' | 'skip' | 'drop' | 'fail'."""
    path = e["path"]
    iid = e.get("id") or ""
    if st["published"].get(path) == e.get("date"):
        return "skip"                  # выходило с этой же датой обновления
    d = None
    try:
        d = item_data(sess, path)
    except Exception as ex:
        print("ошибка разбора:", ex)
    if not d:
        print("  страница не открылась, пропускаем:", iid)
        return "drop"
    cap = make_caption(sess, d)
    if not cap:
        print("  без заголовка (латиницей армянский или пусто), пропускаем:", iid)
        st["skipped"][path] = e.get("date") or today_str()
        return "drop"
    if send(sess, d, cap):
        st["last_publish"] = now
        st["published"][path] = e.get("date") or today_str()
        led_add(SITE, iid, now)
        print(f"опубликовано: {iid} ({path}) фото {len(d['photos'])}")
        return "ok"
    return "fail"

def publish_phase(sess, st, now, limit=1):
    """Публикуем из очереди: не больше limit постов, не больше MAX_TRIES попыток."""
    done, tried = 0, 0
    while st["queue"] and done < limit and tried < MAX_TRIES:
        if now - st["last_publish"] < INTERVAL:
            print("ждём интервал между публикациями")
            break
        tried += 1
        e = st["queue"].pop(0)
        res = publish_one(sess, st, e, now)
        if res == "ok":
            done += 1
        elif res == "fail":
            path = e["path"]
            st["failed"][path] = st["failed"].get(path, 0) + 1
            if st["failed"][path] < 2:
                st["queue"].append(e)
                print(f"  вернём в конец очереди: {path}")
            else:
                print(f"  брошен {path}: не удалось 2 раза")
    return done

def main():
    st = load()
    st.setdefault("failed", {})
    st.setdefault("published", {})
    st.setdefault("skipped", {})
    st.setdefault("last_publish", 0)
    norm_queue(st)
    norm_published(st)
    sess = requests.Session()
    now = time.time()
    deadline = now + MAX_SECONDS

    if RESET_ONCE and not st.get("reset_done2"):
        st["queue"] = []
        st["failed"] = {}
        st["reset_done2"] = True
        print("очистка: очередь сброшена")

    # 1. публикация из уже собранной очереди — до всякого сбора
    done = publish_phase(sess, st, now, 1)
    save(st)

    if TEST:
        for e in st["queue"][:1]:
            d = item_data(sess, e["path"])
            if d:
                print("фото:", len(d["photos"]), "заголовок:", d["title"])
        return done

    # 2. сбор объявлений за неделю, пока хватает времени
    items = get_items(sess, deadline)
    print("всего найдено:", len(items))

    ok_dates = last_days(FRESH_DAYS)
    fresh = []
    for iid, it in items.items():
        if not it["date"]:
            it["date"] = today_str()      # без даты на карточке считаем сегодняшним
        if it["date"] not in ok_dates:
            continue                      # старше недели — не берём вовсе
        if st["published"].get(it["path"]) == it["date"]:
            continue                      # выходило с этой же датой обновления
        if st["skipped"].get(it["path"]) == it["date"]:
            continue                      # уже отброшено: без заголовка
        fresh.append({"id": iid, "path": it["path"], "date": it["date"]})

    if fresh:
        st["queue"].extend(fresh)
        print("новых в очередь:", len(fresh))
    while len(st["queue"]) > QUEUE_MAX:
        st["queue"].pop(0)
    print("свежих за неделю:", len(fresh), "| очередь:", len(st["queue"]))
    save(st)

    # 3. перемешиваем, чтобы разделы шли вперемешку
    random.shuffle(st["queue"])

    # 4. если очередь была пуста и только что наполнилась — публикуем
    if done == 0 and st["queue"]:
        done += publish_phase(sess, st, time.time(), 1)

    if len(st["published"]) > 50000:
        keys = list(st["published"])[-20000:]
        st["published"] = {k: st["published"][k] for k in keys}

    if len(st["skipped"]) > 20000:
        keys = list(st["skipped"])[-8000:]
        st["skipped"] = {k: st["skipped"][k] for k in keys}

    save(st)
    today = today_str()
    n_today = sum(1 for v in st["published"].values() if str(v) == today)
    print(f"готово, публикаций за запуск: {done} | сегодня всего: {n_today}")
    return done

if __name__ == "__main__":
    main()
