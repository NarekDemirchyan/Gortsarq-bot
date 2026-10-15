# -*- coding: utf-8 -*-
"""
gortsarq.am -> Telegram («Բարախոլկա Երևան»). Աղբյուրը՝ միայն գործարք կայքը։

Մեկ գործարկում.
 1. Հրապարակում ենք հերթից. նկարով, իսկ նկար չլինելու դեպքում՝ տեքստով։
 2. Հավաքում ենք վերջին 60 օրվա հայտարարությունները ու դնում հերթ։
 3. Հերթից ոչինչ դուրս չի գցվում մինչև հրապարակվելը։
"""
import os
import re
import json
import time
import random
import urllib.parse

import requests

from fetcher import translate, script, has_cyr, is_translit_hy, led_add

try:
    from fetcher import to_md
except ImportError:
    def to_md(html, url=""):
        return html

GT = chr(62)

TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
JINA_KEY = os.environ.get("JINA_KEY", "")

SITE        = "gortsarq"
STATE       = "state-gortsarq.json"
LED         = "published.json"
INTERVAL    = 0
QUEUE_MAX   = 8000
FRESH_DAYS  = 60
PER_CAT     = 2000
MAX_SECONDS = 900
MAX_PAGES   = 40
MAX_TRIES   = 4
MAX_POSTS   = 1
CATCHUP_AFTER = 90 * 60
CATCHUP_POSTS = 3
SEND_TRIES  = 3
KEEP_DAYS   = 3650
QUEUE_KEEP  = 8000
RESET_ONCE  = False
TEST        = False

BASE = "https://gortsarq.am"

HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "ru,hy;q=0.9,en;q=0.8",
}

BLOCKED = ("just a moment", "performing security verification", "checking your browser",
           "enable javascript", "captcha", "are you a human", "cloudflare",
           "attention required", "access denied", "ddos-guard")

PROXIES = (
    "https://api.allorigins.win/raw",
    "https://api.codetabs.com/v1/proxy",
    "https://corsproxy.io/?url=",
    "https://api.cors.lol/?url=",
)

CATS = [
    "265c-2-ansharj-guyq",
    "1796c-pahestamaser",
    "1875c-kendaniner",
    "1904c-elektronika-hy",
    "2063c-tun-ev-aygi",
    "2116c-noradzevutyun-ev-och",
    "2295c-mankakan-ashxarh",
    "2328c-hobbi-hangist-ev-sport",
]

ITEM_RE    = re.compile(r"""https://gortsarq\.am/ru/(\d+)p-[^)\]\s"'""" + GT + r"""]+""")
ITEM_ID_RE = re.compile(r"/(\d+)p-")
PHOTO_RE   = re.compile(r"""https://gortsarq\.am/images/detailed/\d+/[^)\]\s"'""" + GT + r"""]+""")
DATE_RE    = re.compile(r"\d{2}\.\d{2}\.\d{4}")
PRICE_RE   = re.compile(r"([\d][\d ,\.]*)\s*(֏|\$|€)")
MASK_RE    = re.compile(r"\(?\s*0?\s*x{2,}[\s\-x]*x{2,}\s*\)?", re.I)

HEAD_FALLBACK = "Новое объявление"

def today_str():
    return time.strftime("%d.%m.%Y", time.gmtime(time.time() + 4 * 3600))

def scrub(text):
    return " ".join(MASK_RE.sub(" ", text or "").split())

def last_days(n):
    out = set()
    base = time.time() + 4 * 3600
    for k in range(n + 1):
        out.add(time.strftime("%d.%m.%Y", time.gmtime(base - k * 24 * 3600)))
    return out

def list_url(cat, page):
    return f"{BASE}/ru/{cat}/" if page == 1 else f"{BASE}/ru/{cat}/?page={page}"

def good(text):
    if not text or len(text) < 1500:
        return False
    low = text.lower()
    return not any(b in low for b in BLOCKED)

def via_proxy(sess, proxy, url, headers, timeout):
    if proxy.endswith("="):
        return sess.get(proxy + urllib.parse.quote(url, safe=""),
                        headers=headers, timeout=timeout)
    return sess.get(proxy, params={"url": url}, headers=headers, timeout=timeout)
# ---------- сеть ----------

_FAILS = [0]

def fetch(sess, url):
    """Ընթերցում՝ կարճ ու սահմանափակ. կրկնությունների երկար շղթա չկա։"""
    if _FAILS[0] >= 8:
        return ""
    try:
        r = sess.get(url, headers=HDRS, timeout=12)
        if r.status_code == 200 and good(r.text):
            _FAILS[0] = 0
            return to_md(r.text, url)
    except Exception:
        pass
    h = dict(HDRS)
    h["x-engine"] = "browser"
    if JINA_KEY:
        h["Authorization"] = "Bearer " + JINA_KEY
    try:
        r = sess.get("https://r.jina.ai/" + url, headers=h, timeout=20)
        if r.status_code == 200 and good(r.text):
            _FAILS[0] = 0
            return r.text
    except Exception:
        pass
    for p in PROXIES[:2]:
        try:
            r = via_proxy(sess, p, url, HDRS, 15)
            if r.status_code == 200 and good(r.text):
                _FAILS[0] = 0
                return to_md(r.text, url)
        except Exception:
            pass
    _FAILS[0] += 1
    print("  չկարդացվեց:", url)
    return ""

# ---------- նկարներ ----------

_THUMBS_DESC = ("1024/1024", "800/800", "480/480", "320/320", "240/240")

def thumb_urls(u):
    """Նկարի բոլոր չափերը՝ ամբողջից մինչև մանրը։"""
    out = []
    if "/images/detailed/" in u:
        tail = u.split("/images/detailed/", 1)[1]
        for size in _THUMBS_DESC:
            out.append(BASE + "/images/thumbnails/" + size + "/detailed/" + tail)
    out.insert(0, u)
    return out

def relay_urls(u):
    """Շրջանցիկ հասցեներ. նկարը վերցնում է ուրիշ սերվեր ու տալիս մեզ։"""
    bare = u.split("://", 1)[-1]
    return [f"https://images.weserv.nl/?url={bare}",
            f"https://images.weserv.nl/?url={bare}&w=800",
            f"https://wsrv.nl/?url={bare}"]

def img_ok(r):
    """Նման է նկարի՝ ոչ սխալ, ոչ դատարկ ու ոչ HTML։"""
    b = r.content or b""
    if r.status_code != 200 or len(b) < 800:
        return False
    return not b.lstrip()[:1] == b"<"

def bytes_from(sess, url, ref):
    """Նկարի բայթերը՝ ուղիղ, 2 պրոքսի, 1 հայելի։ Ամեն ինչ կարճ։"""
    if _FAILS[0] >= 12:
        return None
    h = dict(HDRS)
    h["Referer"] = ref or BASE + "/"
    h["Accept"] = "image/avif,image/webp,image/jpeg,image/*,*/*;q=0.8"
    try:
        r = sess.get(url, headers=h, timeout=20)
        if img_ok(r):
            _FAILS[0] = 0
            return r.content
    except Exception:
        pass
    for p in PROXIES[:2]:
        try:
            r = via_proxy(sess, p, url, h, 25)
            if img_ok(r):
                return r.content
        except Exception:
            pass
    for p in relay_urls(url)[:1]:
        try:
            r = sess.get(p, headers=h, timeout=25)
            if img_ok(r):
                return r.content
        except Exception:
            pass
    _FAILS[0] += 1
    return None

def photo_bytes(sess, url, ref):
    """Նկարը ներբեռնում ենք ինքներս՝ բոլոր չափերով ու ուղիներով։"""
    for u in thumb_urls(url):
        b = bytes_from(sess, u, ref)
        if b:
            return b
    print("  նկարը չներբեռնվեց:", url)
    return None
# ---------- разбор ----------

def get_items(sess, deadline):
    """Հղումներն ու քարտի տվյալները՝ ըստ բաժինների։ Պահում ենք գինն ու նկարը որպես պահուստ։"""
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
                price = ""
                pm = PRICE_RE.search(tail)
                if pm:
                    price = " ".join(pm.group(1).split()) + " " + pm.group(2)
                photo = ""
                ph = PHOTO_RE.search(tail)
                if ph:
                    photo = ph.group(0).split("]")[0].split(")")[0]
                if iid not in found:
                    found[iid] = {"path": path, "date": date,
                                  "price": price, "photo": photo}
                    n += 1
                else:
                    if date and not found[iid]["date"]:
                        found[iid]["date"] = date
                    if price and not found[iid]["price"]:
                        found[iid]["price"] = price
                    if photo and not found[iid]["photo"]:
                        found[iid]["photo"] = photo
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
    m = PRICE_RE.search(md)
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
    src = scrub((d.get("title") or "").strip())
    t = scrub(translate(sess, src).strip()) if src else ""
    if src and (is_translit_hy(src) or is_translit_hy(t)):
        return ""
    # инфинитив вроде «Одеваться» заголовком не ставим
    if t and not re.search(r"(?:ться|тись)$", t.lower()) and (has_cyr(t) or script(t) == "lat"):
        return " ".join(t.split())[:90]
    alt = scrub(translate(sess, (d.get("desc") or "")[:300]).strip())
    if has_cyr(alt) and len(alt.split()) >= 3:
        return " ".join(re.split(r"[.!?]\s", alt)[0].strip().split())[:90]
    if t and (has_cyr(t) or script(t) == "lat"):
        return " ".join(t.split())[:90]
    return " ".join((t or alt).split())[:90]

def make_caption(sess, d):
    head = headline(sess, d)
    if not head:
        head = HEAD_FALLBACK
    lines = [head]
    if d.get("price"):
        lines.append(d["price"])
    if d.get("place"):
        lines.append(d["place"])
    if d.get("desc"):
        lines.append(scrub(translate(sess, d["desc"][:700])))
    lines = [" ".join(scrub(x).split()) for x in lines if x]
    return "\n\n".join(lines)[:1024]
# ---------- Telegram ----------

def tg_post(sess, method, data, files=None, timeout=90):
    """Ուղարկում՝ կրկնումներով։ Ցանցի կամ Telegram-ի դադարը գրառումը չի կանգնեցնում։"""
    for attempt in range(SEND_TRIES):
        try:
            r = sess.post(f"https://api.telegram.org/bot{TOKEN}/{method}",
                          data=data, files=files, timeout=timeout)
            print(f"telegram {method}:", r.status_code, r.text[:160])
            if r.ok:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(5 * (attempt + 1))
                continue
            return None
        except Exception as e:
            print(f"telegram {method} սխալ {type(e).__name__}")
            time.sleep(5 * (attempt + 1))
    return None

def send(sess, d, caption):
    """Ալբոմ -> մեկ նկար -> տեքստ։ Առանց նկարի էլ հրապարակվում է։"""
    blobs = []
    for u in (d.get("photos") or [])[:10]:
        b = photo_bytes(sess, u, d["url"])
        if b:
            blobs.append(b)

    markup = {"inline_keyboard": [[{"text": "Связаться", "url": d["url"]}]]}

    if not blobs:
        print("  նկար չկա, ուղարկում ենք տեքստով")
        r = tg_post(sess, "sendMessage",
                    {"chat_id": CHAT_ID, "text": caption,
                     "reply_markup": json.dumps(markup)}, timeout=60)
        return r is not None

    if len(blobs) >= 2:
        files = {f"p{i}": (f"p{i}.jpg", b) for i, b in enumerate(blobs)}
        media = [{"type": "photo", "media": "attach://p0", "caption": caption}]
        for i in range(1, len(blobs)):
            media.append({"type": "photo", "media": f"attach://p{i}"})
        r = tg_post(sess, "sendMediaGroup",
                    {"chat_id": CHAT_ID, "media": json.dumps(media, ensure_ascii=False)},
                    files=files, timeout=150)
        if r is not None:
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
            tg_post(sess, "sendMessage", payload, timeout=40)
            return True
        print("  ալբոմը չանցավ, փորձում ենք մեկ նկարով")

    r = tg_post(sess, "sendPhoto",
                {"chat_id": CHAT_ID, "caption": caption,
                 "reply_markup": json.dumps(markup)},
                files={"photo": ("p.jpg", blobs[0])}, timeout=120)
    return r is not None
# ---------- состояние ----------

def load():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception as e:
            print("state-gortsarq.json не читается:", e)
    return {"queue": [], "last_publish": 0, "published": {}, "failed": {},
            "failed_date": {}, "skipped": {}}

def save(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)

def led_today():
    """Քանի գրառում է դուրս եկել այսօր՝ ըստ հրապարակման ժամանակի։"""
    try:
        led = json.load(open(LED, encoding="utf-8"))
    except Exception:
        return 0
    shift = 4 * 3600
    today = int((time.time() + shift) // 86400)
    n = 0
    for k, v in led.items():
        if not str(k).startswith("gortsarq-"):
            continue
        try:
            if int((float(v) + shift) // 86400) == today:
                n += 1
        except Exception:
            continue
    return n

def norm_queue(st):
    """Очередь — словари {id, path, date, price, place, photo}; старые строки тоже принимаем."""
    q = []
    for e in st.get("queue", []):
        if isinstance(e, dict) and e.get("path"):
            p = str(e["path"]).split("]")[0].split(")")[0]
            iid = str(e.get("id") or "")
            if not iid:
                m = ITEM_ID_RE.search(p)
                iid = m.group(1) if m else p
            q.append({"id": iid, "path": p, "date": e.get("date", ""),
                      "price": e.get("price", ""), "place": e.get("place", ""),
                      "photo": e.get("photo", "")})
        elif isinstance(e, str):
            p = e.split("]")[0].split(")")[0]
            m = ITEM_ID_RE.search(p)
            q.append({"id": m.group(1) if m else p, "path": p, "date": "",
                      "price": "", "place": "", "photo": ""})
    st["queue"] = q

def norm_published(st):
    """Старые отметки времени превращаем в дату обновления (ДД.ММ.ГГГГ)."""
    pub = st.get("published") or {}
    for k, v in list(pub.items()):
        if isinstance(v, (int, float)):
            pub[k] = time.strftime("%d.%m.%Y", time.gmtime(v + 4 * 3600))
    st["published"] = pub

def mark_failed(st, path, date):
    """Считаем неудачу и запоминаем, на какой версии объявления она случилась."""
    st["failed"][path] = st["failed"].get(path, 0) + 1
    st.setdefault("failed_date", {})[path] = date or ""
    return st["failed"][path]

def is_stuck(st, path, date):
    """Дважды не ушло на этой же версии — больше не пробуем."""
    if st["failed"].get(path, 0) < 2:
        return False
    return st.get("failed_date", {}).get(path, "") == (date or "")

def drop_stuck_queue(st):
    """Убираем из очереди всё, что уже дважды не ушло: иначе оно съедает слот."""
    st.setdefault("failed_date", {})
    keep, dropped = [], 0
    for e in st["queue"]:
        if st["failed"].get(e["path"], 0) >= 2:
            st["failed_date"].setdefault(e["path"], e.get("date") or "")
            dropped += 1
            continue
        keep.append(e)
    if dropped:
        print("очередь: убрано застрявших", dropped)
    st["queue"] = keep
# ---------- публикация ----------

def publish_one(sess, st, e, now):
    """Одна попытка публикации. 'ok' | 'skip' | 'drop' | 'fail'."""
    path = e["path"]
    iid = e.get("id") or ""
    if st["published"].get(path) == e.get("date"):
        return "skip"

    d = None
    try:
        d = item_data(sess, path)
    except Exception as ex:
        print("ошибка разбора:", ex)

    if not d:
        print("  էջը չի բացվում — փորձում ենք հերթում պահված տվյալներով:", iid)
        photos = [e["photo"]] if e.get("photo") else []
        d = {"title": "", "price": e.get("price", ""), "desc": "",
             "place": e.get("place", ""), "photos": photos, "url": BASE + path}
    else:
        if not d.get("price") and e.get("price"):
            d["price"] = e["price"]
        if not d.get("photos") and e.get("photo"):
            d["photos"] = [e["photo"]]

    cap = make_caption(sess, d)
    if not cap:
        print("  դատարկ հայտարարություն, դուրս ենք գցում հերթից:", iid)
        st["skipped"][path] = e.get("date") or today_str()
        return "drop"

    if send(sess, d, cap):
        st["last_publish"] = now
        st["published"][path] = e.get("date") or today_str()
        st["failed"].pop(path, None)
        st.setdefault("failed_date", {}).pop(path, None)
        led_add(SITE, iid, now)
        print(f"опубликовано: {iid} ({path}) фото {len(d.get('photos') or [])}")
        return "ok"
    return "fail"

def _date_key(d):
    try:
        dd, mm, yy = str(d or "").split(".")
        return (int(yy), int(mm), int(dd))
    except Exception:
        return (0, 0, 0)

def publish_phase(sess, st, now, limit=1):
    """Հրապարակում ենք ամենանորը սկզբում։ Չորսից ավելի չենք փորձում։"""
    ok_dates = last_days(KEEP_DAYS)
    keep = [e for e in st["queue"] if e.get("date") in ok_dates]
    gone = len(st["queue"]) - len(keep)
    if gone:
        print("հերթից դուրս եկան հինները:", gone)
    keep.sort(key=lambda e: _date_key(e.get("date")), reverse=True)
    st["queue"] = keep
    done, tried = 0, 0
    while st["queue"] and done < limit and tried < MAX_TRIES:
        e = st["queue"].pop(0)
        tried += 1
        path = e["path"]
        if st["published"].get(path) == e.get("date"):
            continue
        res = publish_one(sess, st, e, now)
        if res == "ok":
            done += 1
            save(st)
        elif res == "fail":
            n = mark_failed(st, path, e.get("date"))
            if n < 2:
                st["queue"].append(e)
    while len(st["queue"]) > QUEUE_KEEP:
        st["queue"].pop()
    return done

def main():
    st = load()
    st.setdefault("failed", {})
    st.setdefault("published", {})
    st.setdefault("skipped", {})
    st.setdefault("last_publish", 0)
    st.setdefault("failed_date", {})
    norm_queue(st)
    norm_published(st)
    drop_stuck_queue(st)
    sess = requests.Session()
    now = time.time()
    deadline = now + MAX_SECONDS

    # խումբը երկար լուռ է եղել՝ այս գործարկումը դուրս է բերում մի քանի գրառում
    gap = now - st["last_publish"]
    limit = CATCHUP_POSTS if gap >= CATCHUP_AFTER else MAX_POSTS
    if limit != MAX_POSTS:
        print(f"догоняем: группа молчит {int(gap / 60)} мин, до {limit} постов")

    # 1. Հրապարակում ենք հավաքված հերթից
    done = publish_phase(sess, st, now, limit)
    save(st)

    if TEST:
        for e in st["queue"][:1]:
            d = item_data(sess, e["path"])
            if d:
                print("фото:", len(d["photos"]), "заголовок:", d["title"])
        return done

    # 2. Հավաքում ենք վերջին 60 օրվա հայտարարությունները
    items = get_items(sess, deadline)
    print("всего найдено:", len(items))

    ok_dates = last_days(FRESH_DAYS)
    in_queue = {e["path"] for e in st["queue"]}
    fresh = []
    for iid, it in items.items():
        if not it["date"]:
            it["date"] = today_str()
        if it["date"] not in ok_dates:
            continue
        if is_stuck(st, it["path"], it["date"]):
            continue
        if st["failed"].get(it["path"]) and \
                st.get("failed_date", {}).get(it["path"]) != it["date"]:
            st["failed"].pop(it["path"], None)
        if st["published"].get(it["path"]) == it["date"]:
            continue
        if st["skipped"].get(it["path"]) == it["date"]:
            continue
        if it["path"] in in_queue:
            continue
        fresh.append({"id": iid, "path": it["path"], "date": it["date"],
                      "price": it.get("price", ""), "place": "",
                      "photo": it.get("photo", "")})

    if fresh:
        st["queue"].extend(fresh)
        print("новых в очередь:", len(fresh))
    while len(st["queue"]) > QUEUE_MAX:
        st["queue"].pop(0)
    print("свежих за 60 дней:", len(fresh), "| очередь:", len(st["queue"]))
    save(st)

    # 3. Խառնում ենք, որ բաժինները հերթով գնան
    random.shuffle(st["queue"])

    # 4. Եթե այս գործարկումում դեռ ոչինչ դուրս չի եկել՝ փորձում ենք նորից
    if done < limit and st["queue"]:
        done += publish_phase(sess, st, time.time(), limit - done)

    if len(st["published"]) > 50000:
        keys = list(st["published"])[-20000:]
        st["published"] = {k: st["published"][k] for k in keys}

    if len(st["skipped"]) > 20000:
        keys = list(st["skipped"])[-8000:]
        st["skipped"] = {k: st["skipped"][k] for k in keys}

    save(st)
    n_today = led_today()
    print(f"готово, публикаций за запуск: {done} | сегодня всего: {n_today}")
    return done
# ---- ուղղում. նույն հայտարարությունը մեկ օրում երկու անգամ չի հրապարակվում ----
_orig_publish_one = publish_one

def publish_one(sess, st, e, now):
    path = e["path"]
    day = st.setdefault("pub_day", {})
    if day.get(path) == today_str():
        return "skip"
    if st.get("published", {}).get(path) and e.get("date") != today_str():
        return "skip"
    res = _orig_publish_one(sess, st, e, now)
    if res == "ok":
        day[path] = today_str()
        if len(day) > 20000:
            for k in list(day)[:12000]:
                day.pop(k, None)
    return res

# ---- մեկանգամյա մաքրում. հերթը՝ զրոյից, վերջին 60 օրը ----
try:
    _st = load()
    if not _st.get("reset60"):
        _st.update({"queue": [], "published": {}, "skipped": {}, "failed": {},
                    "failed_date": {}, "pub_day": {}, "nicks": {}, "seen": [],
                    "reset60": True})
        save(_st)
        print("очистка: очередь и отметки сброшены, собираем 60 дней заново")
except Exception:
    pass

if __name__ == "__main__":
    main()
