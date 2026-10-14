# -*- coding: utf-8 -*-
"""
gortsarq.am -> Telegram («Բարախոլկա Երևան»).

Մեկ գործարկում.
 1. Հրապարակում ենք հերթից. միայն նկարով։
 2. Հավաքում ենք վերջին շաբաթվա հայտարարությունները (ոչ ավելի քան MAX_SECONDS),
    հերթում պահում ենք նաև գինը, տեղն ու նկարը՝ պահուստի համար։
 3. Հերթը մաքրվում է խցանվածներից, կրկնօրինակներից ու անհույսներից։

Կանոնները.
 - առանց նկարի գրառում չի դուրս գալիս. նկար չկա՝ հայտարարությունը դուրս է գցվում հերթից,
 - մի քանի նկարից մեկն էլ ներբեռնվեց -> հրապարակում ենք մեկ նկարով,
 - եթե կայքի էջը չի բացվում -> աշխատում ենք հերթում պահված գնով ու նկարով,
 - Telegram-ը չի ընդունում ալբոմը -> ուղարկում ենք մեկ նկարով,
 - վերջին գրառումից CATCHUP_AFTER անց -> գործարկումը դուրս է բերում մինչև CATCHUP_POSTS։
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
LED         = "published.json"   # մատյան՝ հրապարակման ժամանակներով
INTERVAL    = 0              # ритм задаёт расписание (run.py)
QUEUE_MAX   = 2000           # максимум объявлений в очереди
FRESH_DAYS  = 7              # берём объявления за последнюю неделю
PER_CAT     = 2000           # потолок ссылок на раздел
MAX_PAGES   = 20             # предохранитель по страницам раздела
MAX_SECONDS = 15 * 60        # сколько секунд отдаём сбору, остальное — запас
MAX_TRIES   = 25             # сколько объявлений пробуем за один запуск
MAX_POSTS   = 1              # обычный ритм: одна публикация за запуск
CATCHUP_AFTER = 90 * 60      # если столько секунд группа молчала — догоняем
CATCHUP_POSTS = 3            # сколько постов разрешаем в догоняющем запуске
SEND_TRIES  = 3              # сколько раз пробуем отправить в Telegram
RESET_ONCE  = False          # однократный сброс очереди (уже сделан)
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
    "https://api.allorigins.win/raw",     # url передаём параметром
    "https://api.codetabs.com/v1/proxy",  # url передаём параметром
    "https://corsproxy.io/?url=",         # url приклеиваем
    "https://api.cors.lol/?url=",         # url приклеиваем
)

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
PRICE_RE   = re.compile(r"([\d][\d ,\.]*)\s*(֏|\$|€)")

# закрытый телефон: (0x xxx xxxx) и любые похожие наборы «x»
MASK_RE    = re.compile(r"\(?\s*0?\s*x{2,}[\s\-x]*x{2,}\s*\)?", re.I)

HEAD_FALLBACK = "Новое объявление"

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

def via_proxy(sess, proxy, url, headers, timeout):
    if proxy.endswith("="):
        return sess.get(proxy + urllib.parse.quote(url, safe=""),
                        headers=headers, timeout=timeout)
    return sess.get(proxy, params={"url": url}, headers=headers, timeout=timeout)

def fetch(sess, url):
    """Ուղիղ -> Jina -> պրոքսիներ, հետո նույնը կրկին։ Մեկ ուղին չի ստացվել՝ փորձում ենք մյուսը։"""
    for attempt in range(2):
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
            if JINA_KEY:
                h["Authorization"] = "Bearer " + JINA_KEY
            r = sess.get("https://r.jina.ai/" + url, headers=h, timeout=45)
            if r.status_code == 200 and good(r.text):
                print("  jina")
                return r.text
            print(f"  jina {r.status_code}, {len(r.text)} знаков")
        except Exception as e:
            print(f"  jina сбой {type(e).__name__}")

        for p in PROXIES:
            try:
                r = via_proxy(sess, p, url, HDRS, 45)
                if r.status_code == 200 and good(r.text):
                    print("  прокси")
                    return to_md(r.text, url)
            except Exception as e:
                print(f"  прокси сбой {type(e).__name__}")

        if attempt == 0:
            time.sleep(2)

    print("  чтение не удалось:", url)
    return ""

# ---------- նկարներ ----------

THUMBS = ("240/240", "320/320", "480/480", "800/800", "1024/1024")

def thumb_urls(u):
    """Մեկ նկարի բոլոր չափերը՝ մանրից մինչև ամբողջականը։"""
    out = []
    if "/images/detailed/" in u:
        tail = u.split("/images/detailed/", 1)[1]
        for size in THUMBS:
            out.append(f"{BASE}/images/thumbnails/{size}/detailed/{tail}")
    out.append(u)
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
    """Նկարի բայթերը՝ ուղիղ Referer-ով, հետո պրոքսիներով, հետո փոխանցիչով։"""
    h = dict(HDRS)
    h["Referer"] = ref or BASE + "/"
    h["Accept"] = "image/avif,image/webp,image/jpeg,image/*,*/*;q=0.8"

    try:
        r = sess.get(url, headers=h, timeout=25)
        if img_ok(r):
            return r.content
    except Exception as e:
        print(f"  նկար ուղիղ սխալ {type(e).__name__}")

    for p in PROXIES:
        try:
            r = via_proxy(sess, p, url, h, 45)
            if img_ok(r):
                print("  նկար proxy")
                return r.content
        except Exception as e:
            print(f"  նկար proxy սխալ {type(e).__name__}")

    for p in relay_urls(url):
        try:
            r = sess.get(p, headers=h, timeout=45)
            if img_ok(r):
                print("  նկար փոխանցիչ")
                return r.content
        except Exception as e:
            print(f"  նկար փոխանցիչ սխալ {type(e).__name__}")
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
        head = HEAD_FALLBACK           # էջը չի բացվել՝ գրառումը միևնույն է դուրս է գալիս նկարով
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
    """Ալբոմ -> մեկ նկար։ Առանց նկարի գրառում չի ուղարկվում։"""
    blobs = []
    for u in (d.get("photos") or [])[:10]:
        b = photo_bytes(sess, u, d["url"])
        if b:
            blobs.append(b)

    if not blobs:
        print("  նկարները չներբեռնվեցին, հրապարակում չկա")
        return False

    markup = {"inline_keyboard": [[{"text": "Связаться", "url": d["url"]}]]}

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
            return True                # ալբոմը խմբում է՝ գրառումը եղավ
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
    shift = 4 * 3600                       # Երևանի ժամանակ (UTC+4)
    today = int((time.time() + shift) // 86400)
    n = 0
    for k, v in led.items():
        if not str(k).startswith(SITE + "-"):
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
        return "skip"                  # выходило с этой же датой обновления

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

    if not d.get("photos"):
        print("  նկար չկա, դուրս ենք գցում հերթից:", iid)
        st["skipped"][path] = e.get("date") or today_str()
        return "drop"

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

def publish_phase(sess, st, now, limit=1):
    """Հրապարակում ենք հերթից՝ ոչ ավելի քան limit գրառում և MAX_TRIES փորձ։"""
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
            n = mark_failed(st, path, e.get("date"))
            if n < 2:
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
    st.setdefault("failed_date", {})
    norm_queue(st)
    norm_published(st)
    drop_stuck_queue(st)
    sess = requests.Session()
    now = time.time()
    deadline = now + MAX_SECONDS

    if RESET_ONCE and not st.get("reset_done2"):
        st["queue"] = []
        st["failed"] = {}
        st["reset_done2"] = True
        print("очистка: очередь сброшена")

    # խումբը երկար լուռ է եղել՝ այս գործարկումը դուրս է բերում մի քանի գրառում
    gap = now - st["last_publish"]
    limit = CATCHUP_POSTS if gap >= CATCHUP_AFTER else MAX_POSTS
    if limit != MAX_POSTS:
        print(f"догоняем: группа молчит {int(gap / 60)} мин, до {limit} постов")

    # 1. Հրապարակում ենք հավաքված հերթից՝ մինչև որևէ ստուգում
    done = publish_phase(sess, st, now, limit)
    save(st)

    if TEST:
        for e in st["queue"][:1]:
            d = item_data(sess, e["path"])
            if d:
                print("фото:", len(d["photos"]), "заголовок:", d["title"])
        return done

    # 2. Հավաքում ենք վերջին շաբաթվա հայտարարությունները
    items = get_items(sess, deadline)
    print("всего найдено:", len(items))

    ok_dates = last_days(FRESH_DAYS)
    in_queue = {e["path"] for e in st["queue"]}
    fresh = []
    for iid, it in items.items():
        if not it["date"]:
            it["date"] = today_str()      # без даты на карточке считаем сегодняшним
        if it["date"] not in ok_dates:
            continue                      # старше недели — не берём вовсе
        if is_stuck(st, it["path"], it["date"]):
            continue                      # дважды не ушло на этой версии — не пробуем
        if st["failed"].get(it["path"]) and \
                st.get("failed_date", {}).get(it["path"]) != it["date"]:
            st["failed"].pop(it["path"], None)      # объявление обновилось — снова в игру
        if st["published"].get(it["path"]) == it["date"]:
            continue                      # выходило с этой же датой обновления
        if st["skipped"].get(it["path"]) == it["date"]:
            continue                      # уже отброшено
        if it["path"] in in_queue:
            continue                      # уже в очереди — не дублируем
        fresh.append({"id": iid, "path": it["path"], "date": it["date"],
                      "price": it.get("price", ""), "place": "",
                      "photo": it.get("photo", "")})

    if fresh:
        st["queue"].extend(fresh)
        print("новых в очередь:", len(fresh))
    while len(st["queue"]) > QUEUE_MAX:
        st["queue"].pop(0)
    print("свежих за неделю:", len(fresh), "| очередь:", len(st["queue"]))
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

# ---- ուղղում. նույն հայտարարությունը երկու անգամ չի հրապարակվում ----
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
# ===== UNION (ամբողջ Հայաստանը՝ և Երևան, և Գյումրի) =====
# Կայքում առանձին քաղաքի էջ չկա. մեկ լենտա է՝ unionapp.net/en/armenia։
UNION_FEEDS = (("Հայաստան", "https://unionapp.net/en/armenia"),)
UNION_TAKE = 25
UNION_AD_RE = re.compile(r"https://unionapp\.net/(?:en|ru)/ad/([a-z0-9]{6,})")
UNION_IMG_RE = re.compile(r"https://media\.unionapp\.net/ads/[a-z0-9_]+\.webp")
UNION_NICK_RE = re.compile(r"(?:https://t\.me/|t\.me/|@)([A-Za-z0-9_]{4,32})")
UNION_SEC_RE = re.compile(r"(?:^|\n)\s*(?:#{2,4}\s*|<h[1-4][^>]*>)\s*(\d{1,2})\s+([A-Za-zА-Яа-я]+)")
UNION_MON = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
             "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
             "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
             "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
NICK_STOP = ("unionapp_bot", "union", "share", "telegram")

def union_read(sess, url, raw=False):
    """Կայքը աշխատում է ջինայի միջոցով (ուղիղ ու պրոքսիները փակվում են)։"""
    H = dict(HDRS)
    H["Referer"] = "https://unionapp.net/"
    H["x-engine"] = "browser"
    if raw:
        H["x-return-format"] = "html"
    if JINA_KEY:
        H["Authorization"] = "Bearer " + JINA_KEY
    for attempt in (0, 1):
        try:
            r = sess.get("https://r.jina.ai/" + url, headers=H, timeout=60)
            if r.status_code == 200 and len(r.text) > 1000 and "does not exist" not in r.text:
                print("union jina")
                return r.text
            print("union jina", r.status_code, len(r.text))
        except Exception as ex:
            print("union jina սխալ:", ex)
        try:
            r = sess.get(url, headers=HDRS, timeout=25)
            if r.status_code == 200 and len(r.text) > 1500 and "does not exist" not in r.text:
                return r.text
        except Exception as ex:
            print("union ուղիղ սխալ:", ex)
        for p in PROXIES:
            try:
                r = via_proxy(sess, p, url, HDRS, 45)
                if r.status_code == 200 and len(r.text) > 1500 and "does not exist" not in r.text:
                    return r.text
            except Exception:
                pass
        time.sleep(2)
    print("union՝ չկարդացվեց:", url)
    return ""

def union_sections(md):
    out = []
    now = time.gmtime(time.time() + 4 * 3600)
    for m in UNION_SEC_RE.finditer(md):
        try:
            day = int(m.group(1))
        except Exception:
            continue
        mon = UNION_MON.get(m.group(2).lower())
        if not mon or day > 31:
            continue
        year = now.tm_year - (1 if mon > now.tm_mon else 0)
        out.append(("%02d.%02d.%d" % (day, mon, year), m.end()))
    return out

def union_items(md, secs):
    """Լենտայից՝ ամեն հայտարարության ամսաթիվը, համարը, վերնագիրը, գինը, քաղաքն ու նկարները։"""
    out, seen = [], set()
    ms = list(UNION_AD_RE.finditer(md))
    for i, m in enumerate(ms):
        aid = m.group(1)
        if aid in seen:
            continue
        seen.add(aid)
        chunk = md[ms[i - 1].end() if i else 0:m.start()]
        date = ""
        for d, pos in secs:
            if pos <= m.start():
                date = d
            else:
                break
        title = ""
        for tm in re.finditer(r"###\s+(.+)", chunk[-400:]):
            title = scrub(tm.group(1))
        city = ""
        cm = re.search(r"(?:For sale|Wanted|Продажа|Куплю)\s*·\s*([^·\n#]+)", chunk)
        if cm:
            city = " ".join(cm.group(1).split())
        price = ""
        pm = re.search(r"([\d][\d ,\.]*)\s*֏", chunk)
        if pm:
            price = " ".join(pm.group(1).split()) + " ֏"
        desc = ""
        dm = re.search(r"###\s+.+?\n(.+)", chunk, re.S)
        if dm:
            desc = scrub(" ".join(dm.group(1).split()))[:700]
        photos, ps = [], set()
        for im in UNION_IMG_RE.finditer(chunk):
            if im.group(0) not in ps:
                ps.add(im.group(0))
                photos.append(im.group(0))
        out.append((date or today_str(), aid,
                    {"title": title, "desc": desc, "price": price,
                     "city": city, "photos": photos}))
    return out

def union_nick(sess, aid):
    """Վաճառողի նիկը՝ հայտարարության սեփական էջից։ Չգտնվի՝ դատարկ։"""
    for url in ("https://unionapp.net/en/ad/" + aid, "https://unionapp.net/ad/" + aid):
        txt = union_read(sess, url, raw=True)
        if not txt:
            continue
        for m in UNION_NICK_RE.finditer(txt):
            n = m.group(1)
            if n.lower() not in NICK_STOP:
                return n
    return ""

def union_photo(sess, u):
    """Նկարը։ Առաջին հերթին թարգմանիչով (այնտեղից գալիս է jpg), հետո ինքը կայքից։"""
    H = dict(HDRS)
    H["Referer"] = "https://unionapp.net/"
    H["Accept"] = "image/avif,image/webp,image/jpeg,image/*,*/*;q=0.8"
    full = u.replace("_thumbnail", "")
    bare = full.split("://", 1)[-1]
    for cand in ("https://images.weserv.nl/?url=" + bare + "&output=jpg", full, u):
        try:
            r = sess.get(cand, headers=H, timeout=30)
            if img_ok(r):
                return r.content
        except Exception as ex:
            print("union նկար սխալ:", ex)
        for p in PROXIES:
            try:
                r = via_proxy(sess, p, cand, H, 45)
                if img_ok(r):
                    return r.content
            except Exception:
                pass
    print("union՝ նկարը չիջավ:", u)
    return None

def union_send(sess, e, cap):
    """Միայն լուսանկարներ ու տեքստ։ Կոճակ չկա։"""
    blobs = []
    for u in (e.get("photos") or [])[:10]:
        b = union_photo(sess, u)
        if b:
            blobs.append(b)
    if not blobs:
        print("union՝ լուսանկարները չիջան")
        return False
    if len(blobs) >= 2:
        files = {("p%d" % i): ("p%d.jpg" % i, b) for i, b in enumerate(blobs)}
        media = [{"type": "photo", "media": "attach://p0", "caption": cap}]
        for i in range(1, len(blobs)):
            media.append({"type": "photo", "media": "attach://p%d" % i})
        if tg_post(sess, "sendMediaGroup",
                   {"chat_id": CHAT_ID, "media": json.dumps(media, ensure_ascii=False)},
                   files=files, timeout=150) is not None:
            return True
        print("union՝ ալբոմը չանցավ, մեկ նկարով")
    return tg_post(sess, "sendPhoto", {"chat_id": CHAT_ID, "caption": cap},
                   files={"photo": ("p.jpg", blobs[0])}, timeout=120) is not None

def union_caption(e):
    parts = [e.get("title") or HEAD_FALLBACK]
    if e.get("desc") and e.get("desc") != e.get("title"):
        parts.append(e["desc"][:700])
    if e.get("price"):
        parts.append(e["price"])
    if e.get("place"):
        parts.append(e["place"])
    if e.get("nick"):
        parts.append("@" + e["nick"])
    return "\n\n".join(" ".join(p.split()) for p in parts if p)[:1024]

def union_publish_one(sess, st, e, now):
    path = e.get("path") or ("union:" + str(e.get("id")))
    if not union_send(sess, e, union_caption(e)):
        return "fail"
    st["last_publish"] = now
    st["published"][path] = e.get("date") or today_str()
    st["failed"].pop(path, None)
    st.setdefault("failed_date", {}).pop(path, None)
    try:
        led_add("union", str(e.get("id")), now)
    except Exception:
        pass
    print("опубликовано union: %s @%s" % (e.get("id"), e.get("nick") or "-"))
    return "ok"

def union_harvest():
    st = load()
    for k, v in (("queue", []), ("published", {}), ("skipped", {}),
                 ("failed", {}), ("failed_date", {}), ("nicks", {})):
        st.setdefault(k, v)
    sess = requests.Session()
    in_q = set(e.get("path") for e in st["queue"] if isinstance(e, dict))
    ok_dates = last_days(FRESH_DAYS)
    added = 0
    for city, url in UNION_FEEDS:
        md = union_read(sess, url)
        if not md:
            print("[union][%s] լենտան չբացվեց" % city)
            continue
        secs = union_sections(md)
        found = union_items(md, secs)
        print("[union][%s] լենտայում %d, ամսաթիվ %d" % (city, len(found), len(secs)))
        for date, aid, it in found:
            if added >= UNION_TAKE:
                break
            path = "union:" + aid
            if date not in ok_dates or path in in_q:
                continue
            if st["published"].get(path) == date or st["skipped"].get(path) == date:
                continue
            if is_stuck(st, path, date):
                continue
            if not it["photos"]:
                st["skipped"][path] = date
                continue
            nick = st["nicks"].get(aid)
            if nick is None:
                nick = union_nick(sess, aid)
                st["nicks"][aid] = nick
                if len(st["nicks"]) > 3000:
                    for k in list(st["nicks"])[:1000]:
                        st["nicks"].pop(k, None)
            st["queue"].append({"src": "union", "id": aid, "path": path, "date": date,
                                "nick": nick or "", "title": it["title"] or HEAD_FALLBACK,
                                "desc": it["desc"], "price": it["price"],
                                "place": it["city"], "photo": it["photos"][0],
                                "photos": it["photos"][:10]})
            in_q.add(path)
            added += 1
            print("[union] %s @%s | %s" % (aid, nick or "-", it["title"][:60]))
            time.sleep(1)
    # թարմ Union-ի գրառումները դնում ենք հերթի գլուխ, որ չսպասեն gortsarq-ի հին հերթին
    uni = [e for e in st["queue"] if e.get("src") == "union"]
    rest = [e for e in st["queue"] if e.get("src") != "union"]
    random.shuffle(uni)
    st["queue"] = uni[:12] + rest + uni[12:]
    while len(st["queue"]) > QUEUE_MAX:
        st["queue"].pop(0)
    save(st)
    print("union՝ հերթ մտավ %d | հերթում %d" % (added, len(st["queue"])))

OLD_PUBLISH = _orig_publish_one

def _orig_publish_one(sess, st, e, now):
    if isinstance(e, dict) and e.get("src") == "union":
        return union_publish_one(sess, st, e, now)
    return OLD_PUBLISH(sess, st, e, now)

OLD_NORM_QUEUE = norm_queue

def norm_queue(st):
    uni = [e for e in st.get("queue", []) if isinstance(e, dict) and e.get("src") == "union"]
    OLD_NORM_QUEUE(st)
    st["queue"] = list(st.get("queue", [])) + uni

OLD_MAIN = main

def main():
    res = OLD_MAIN()
    try:
        union_harvest()
    except Exception as ex:
        print("union:", ex)
    return res
# ===== UNION ուղղում. նիկը՝ լենտայից, առանց ամեն էջը բացելու =====
MAX_SECONDS = 4 * 60      # հավաքումը՝ 4 րոպե (առաջ 15)
MAX_PAGES   = 5           # բաժնի էջերի առաստաղ (առաջ 20)

_NICK_CACHE = {}

def _union_nick_from_chunk(chunk):
    for m in UNION_NICK_RE.finditer(chunk):
        n = m.group(1)
        if n.lower() not in NICK_STOP:
            return n
    return ""

_OLD_UNION_ITEMS = union_items

def union_items(md, secs):
    items = _OLD_UNION_ITEMS(md, secs)
    hits = [(m.group(1), m.start(), m.end()) for m in UNION_AD_RE.finditer(md)]
    for date, aid, it in items:
        k = None
        for j, h in enumerate(hits):
            if h[0] == aid:
                k = j
                break
        if k is None:
            it["nick"] = ""
            continue
        start = hits[k][1]
        prev_end = hits[k - 1][2] if k else 0
        it["nick"] = _union_nick_from_chunk(md[prev_end:start])
        _NICK_CACHE[aid] = it["nick"]
    return items

def union_nick(sess, aid):
    return _NICK_CACHE.get(aid, "")

_OLD_UNION_CAPTION = union_caption

def union_caption(e):
    cap = _OLD_UNION_CAPTION(e)
    if not e.get("nick") and "unionapp.net/en/ad/" not in cap:
        cap = (cap + "\n\nhttps://unionapp.net/en/ad/" + str(e.get("id")))[:1024]
    return cap
# ===== ԿԱՐԳԱՎՈՐՈՒՄ. արագություն, թարմություն, մեկ կրկնության կանոն =====
MAX_TRIES   = 4            # քանի հայտարարություն ենք փորձում մեկ գրառման համար
KEEP_DAYS   = 3            # որքան օրից հինը դուրս է գցվում
QUEUE_KEEP  = 400          # որքանից ավելի հերթ չենք պահում
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

def bytes_from(sess, url, ref):
    """Նկարը՝ ուղիղ, 2 պրոքսի, 1 հայելի։ Ամեն ինչ կարճ։"""
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

def union_read(sess, url, raw=False):
    """Union-ի լենտան՝ երկու կարճ փորձով։ Չստացվի՝ դատարկ։"""
    if _FAILS[0] >= 8:
        return ""
    H = dict(HDRS)
    H["Referer"] = "https://unionapp.net/"
    H["x-engine"] = "browser"
    if JINA_KEY:
        H["Authorization"] = "Bearer " + JINA_KEY
    try:
        r = sess.get("https://r.jina.ai/" + url, headers=H, timeout=30)
        if r.status_code == 200 and len(r.text) > 1000 and "does not exist" not in r.text:
            _FAILS[0] = 0
            print("union jina")
            return r.text
        print("union jina", r.status_code, len(r.text))
    except Exception as ex:
        print("union jina սխալ:", ex)
    try:
        r = sess.get(url, headers=HDRS, timeout=20)
        if r.status_code == 200 and len(r.text) > 1500 and "does not exist" not in r.text:
            _FAILS[0] = 0
            return r.text
    except Exception:
        pass
    _FAILS[0] += 1
    print("union՝ չկարդացվեց:", url)
    return ""
def _date_key(d):
    try:
        dd, mm, yy = str(d or "").split(".")
        return (int(yy), int(mm), int(dd))
    except Exception:
        return (0, 0, 0)

def publish_phase(sess, st, now, limit=1):
    """Հրապարակում ենք միայն թարմը՝ ամենանորը սկզբում։ Չորսից ավելի չենք փորձում։"""
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
            save(st)                  # անմիջապես՝ որ չեղարկումը մատյանը չկորցնի
        elif res == "fail":
            n = mark_failed(st, path, e.get("date"))
            if n < 2:
                st["queue"].append(e)
    while len(st["queue"]) > QUEUE_KEEP:
        st["queue"].pop()
    return done

def led_today():
    """Այսօրվա գրառումները՝ և՛ Բարախոլկա, և՛ Union։"""
    try:
        led = json.load(open(LED, encoding="utf-8"))
    except Exception:
        return 0
    shift = 4 * 3600
    today = int((time.time() + shift) // 86400)
    n = 0
    for k, v in led.items():
        if not (str(k).startswith("gortsarq-") or str(k).startswith("union-")):
            continue
        try:
            if int((float(v) + shift) // 86400) == today:
                n += 1
        except Exception:
            continue
    return n
# ---- ուղղում. նիկը՝ միայն t.me հղումից, և միայն հրապարակվողի համար ----
NICK_RE2 = re.compile(r"t\.me/([A-Za-z0-9_]{4,32})")

def _union_nick_from_chunk(chunk):
    for m in NICK_RE2.finditer(chunk or ""):
        n = m.group(1)
        if n.lower() not in NICK_STOP:
            return n
    return ""

_OLD_UNION_PUB2 = union_publish_one

def union_publish_one(sess, st, e, now):
    if e.get("src") == "union" and not e.get("nick"):
        try:
            txt = union_read(sess, "https://unionapp.net/en/ad/" + str(e.get("id")), raw=True)
            n = _union_nick_from_chunk(txt)
            if n:
                e["nick"] = n
        except Exception:
            pass
    return _OLD_UNION_PUB2(sess, st, e, now)
# ---- ուղղում. Union-ի գրառումը չի կրկնվում ու չի գնում gortsarq-ի ուղիով ----
_OLD_NORM_QUEUE2 = norm_queue

def norm_queue(st):
    uni = [e for e in st.get("queue", []) if isinstance(e, dict)
           and (e.get("src") == "union" or str(e.get("path") or "").startswith("union:"))]
    st["queue"] = [e for e in st.get("queue", []) if e not in uni]
    _OLD_NORM_QUEUE2(st)
    best = {}
    for e in list(st.get("queue", [])) + uni:
        p = str(e.get("path") or "")
        cur = best.get(p)
        if cur is None or (e.get("src") == "union" and (cur or {}).get("src") != "union"):
            best[p] = e
    st["queue"] = list(best.values())

_OLD_ORIG2 = _orig_publish_one

def _orig_publish_one(sess, st, e, now):
    if isinstance(e, dict) and (e.get("src") == "union"
                                or str(e.get("path") or "").startswith("union:")):
        return union_publish_one(sess, st, e, now)
    return _OLD_ORIG2(sess, st, e, now)
# ---- ուղղում. կեղծ նիկը (@context) չի հայտնվում գրառման մեջ ----
_NICK_JUNK = ("context", "type", "id", "graph", "unionapp_bot", "union", "share", "telegram")
_OLD_UNION_PUB3 = union_publish_one

def union_publish_one(sess, st, e, now):
    if isinstance(e, dict) and (e.get("src") == "union"
                                or str(e.get("path") or "").startswith("union:")):
        n = ""
        try:
            txt = union_read(sess, "https://unionapp.net/en/ad/" + str(e.get("id")), raw=True)
            n = _union_nick_from_chunk(txt)
        except Exception:
            n = ""
        if not n or str(n).lower() in _NICK_JUNK:
            e["nick"] = ""
        else:
            e["nick"] = n
    return _OLD_UNION_PUB3(sess, st, e, now)
if __name__ == "__main__":
    main()
