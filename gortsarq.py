# -*- coding: utf-8 -*-
"""
gortsarq.am -> Telegram («Բարախոլկա Երևան»).

 - միայն վաճառքի բաժիններ (առանց անշարժի, տրանսպորտի, աշխատանքի,
   ծառայությունների, վարձույթի)
 - մեկ անգամ հրապարակվածը չի կրկնվում
 - տեքստում հղում կամ էջի աղբ չի մնում
"""
import os
import re
import json
import time
import urllib.parse

import requests

from fetcher import translate, script, has_cyr, is_translit_hy, led_add

try:
    from fetcher import to_md
except ImportError:
    def to_md(html, url=""):
        return html

TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
JINA_KEY = os.environ.get("JINA_KEY", "")

SITE        = "gortsarq"
STATE       = "state-gortsarq.json"
QUEUE_MAX   = 8000
FRESH_DAYS  = 60
PER_CAT     = 2000
MAX_SECONDS = 1500
MAX_PAGES   = 40
MAX_TRIES   = 4
MAX_POSTS   = 1
SEND_TRIES  = 3
MAXP        = 9 * 1024 * 1024

BASE        = "https://gortsarq.am"
GROUP_BOOST = "https://t.me/boost/erevan03_baraxolka"

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
)

CATS = [
    "1796c-pahestamaser",
    "1875c-kendaniner",
    "1904c-elektronika-hy",
    "2063c-tun-ev-aygi",
    "2116c-noradzevutyun-ev-och",
    "2295c-mankakan-ashxarh",
    "2328c-hobbi-hangist-ev-sport",
]

ITEM_RE  = re.compile(r"https://gortsarq\.am/ru/(\d+)p-[^)\]\s\"'<]+")
PHOTO_RE = re.compile(r"https://gortsarq\.am/images/detailed/\d+/[^)\]\s\"'<]+")
ID_RE    = re.compile(r"/(\d+)p-")
PRICE_RE = re.compile(r"([\d][\d ,\.]*)\s*(֏|\$|€)")
DATE_RE  = re.compile(r"\d{2}\.\d{2}\.\d{4}")
MASK_RE  = re.compile(r"\(?\s*0?\s*x{2,}[\s\-x]*x{2,}\s*\)?", re.I)

BAD = ("http", "www.", "gortsarq.am", "image", "logo", "аватар", "ավատար",
       "просмотр", "поделиться", "сравнить", "избранное", "войти")

def scrub(text):
    t = MASK_RE.sub(" ", text or "")
    t = " ".join(t.split())
    t = re.sub(r"\s*([,.;:!?])\s*", r"\1 ", t)
    t = re.sub(r"^[\s,.;:!?\-]+", "", t)
    return t.strip(" ,.;:!?-\"")

def clean(text):
    t = (text or "").replace("`", " ").replace("**", " ")
    t = t.replace("[", " ").replace("]", " ")
    for sep in ("](", "] ("):
        guard = 0
        while sep in t and guard < 50:
            guard += 1
            i = t.index(sep)
            j = t.find(")", i)
            if j == -1:
                t = t[:i]
                break
            t = t[:i] + " " + t[j + 1:]
    t = " ".join(w for w in t.split() if not w.lower().startswith(("http", "www.")))
    t = t.replace("gortsarq.am", " ")
    return scrub(t)

def junk(text):
    low = (text or "").lower()
    return any(b in low for b in BAD)

def money(price):
    m = re.match(r"([\d\s.,]+)\s*(֏|\$|€)?", scrub(price))
    if not m:
        return ""
    digits = "".join(ch for ch in m.group(1) if ch.isdigit())
    if not digits or not re.search(r"[1-9]", digits):
        return ""
    return ("{:,}".format(int(digits)).replace(",", " ") + " " + (m.group(2) or "")).strip()

def today_str():
    return time.strftime("%d.%m.%Y", time.gmtime(time.time() + 4 * 3600))

def dkey(date):
    try:
        d, m, y = [int(x) for x in (date or "").split(".")]
        return (y, m, d)
    except Exception:
        return (0, 0, 0)

def last_days(n):
    out = set()
    base = time.time() + 4 * 3600
    for k in range(n + 1):
        out.add(time.strftime("%d.%m.%Y", time.gmtime(base - k * 24 * 3600)))
    return out

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

_FAILS = [0]

def fetch(sess, url):
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

def site_up(sess):
    return bool(fetch(sess, BASE + "/ru/"))

def img_ok(r):
    b = r.content or b""
    if r.status_code != 200 or len(b) < 800:
        return False
    return not b.lstrip()[:1] == b"<"

def thumb_urls(u):
    out = []
    if "/images/detailed/" in u:
        tail = u.split("/images/detailed/", 1)[1]
        for size in ("1024/1024", "800/800", "480/800", "320/320"):
            out.append(BASE + "/images/thumbnails/" + size + "/detailed/" + tail)
    out.append(u)
    return out

def photo_bytes(sess, url, ref):
    if _FAILS[0] >= 12:
        return None
    h = dict(HDRS)
    h["Referer"] = ref or BASE + "/"
    h["Accept"] = "image/avif,image/webp,image/jpeg,image/*,*/*;q=0.8"
    for u in thumb_urls(url):
        try:
            r = sess.get(u, headers=h, timeout=20)
            if img_ok(r) and len(r.content) <= MAXP:
                _FAILS[0] = 0
                return r.content
        except Exception:
            pass
    bare = url.split("://", 1)[-1]
    try:
        r = sess.get("https://images.weserv.nl/?url=" + bare, headers=h, timeout=25)
        if img_ok(r) and len(r.content) <= MAXP:
            return r.content
    except Exception:
        pass
    _FAILS[0] += 1
    print("  նկարը չներբեռնվեց:", url)
    return None

def list_url(cat, page):
    return ("%s/ru/%s/" % (BASE, cat)) if page == 1 else ("%s/ru/%s/?page=%d" % (BASE, cat, page))

def get_items(sess, deadline):
    ok_dates = last_days(FRESH_DAYS)
    found = {}
    for cat in CATS:
        per_cat = 0
        for page in range(1, MAX_PAGES + 1):
            if time.time() > deadline:
                print("  հավաքում. ժամանակը վերջացավ")
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
                dm = DATE_RE.search(tail)
                date = dm.group(0) if dm else ""
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
                if iid in found:
                    if price and not found[iid]["price"]:
                        found[iid]["price"] = price
                    if photo and not found[iid]["photo"]:
                        found[iid]["photo"] = photo
                    continue
                found[iid] = {"id": iid, "path": path, "date": date,
                              "price": price, "photo": photo, "cat": cat}
                n += 1
            per_cat += n
            print("[%s] стр.%d: новых %d, свежих %d" % (cat, page, n, fresh))
            if n == 0 or per_cat >= PER_CAT:
                break
            if fresh == 0 and page > 1:
                break
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
        title = clean(m.group(1))[:90]
    if not title or junk(title):
        title = ""

    price = ""
    mp = PRICE_RE.search(md)
    if mp:
        num = " ".join(re.sub(r"[.,]00$", "", mp.group(1).strip()).split())
        if re.search(r"[1-9]", num):
            price = num + " " + mp.group(2)
    if not price and "Договорная" in md:
        price = "Договорная"

    desc = ""
    m = re.search(r"Описание\s*\n+(.+?)\n+\d{2}\.\d{2}\.\d{4}", md, re.S)
    if m:
        desc = clean(" ".join(m.group(1).split()))

    place = ""
    m = re.search(r"Расположение\s*\n+(.+)", md)
    if m:
        place = clean(" ".join(m.group(1).split()))[:60]
    if junk(place) or "2026" in place:
        place = ""

    date = ""
    dm = DATE_RE.search(md)
    if dm and dm.group(0) in last_days(FRESH_DAYS):
        date = dm.group(0)

    photos = []
    for mi in PHOTO_RE.finditer(md):
        u = mi.group(0).split("]")[0].split(")")[0]
        if u not in photos:
            photos.append(u)

    print("  %s: фото %d, «%s», «%s», опис %d"
          % (path, len(photos), title[:40], price, len(desc)))

    return {"title": title, "price": price, "desc": desc, "place": place,
            "photos": photos[:10], "date": date, "url": BASE + path}

def headline(sess, d):
    src = clean(d.get("title") or "")
    t = clean(translate(sess, src)) if src else ""
    if src and (is_translit_hy(src) or is_translit_hy(t)):
        return ""
    if t and not re.search(r"(?:ться|тись)$", t.lower()) and (has_cyr(t) or script(t) == "lat"):
        return " ".join(t.split())[:90]
    alt = clean(translate(sess, (d.get("desc") or "")[:300]))
    if has_cyr(alt) and len(alt.split()) >= 3:
        return " ".join(re.split(r"[.!?]\s", alt)[0].split())[:90]
    if t and (has_cyr(t) or script(t) == "lat"):
        return " ".join(t.split())[:90]
    return " ".join((t or alt).split())[:90]

def make_caption(sess, d):
    head = clean(headline(sess, d))[:90] or "Նոր հայտարարություն"

    meta = []
    if d.get("price"):
        p = money(d["price"])
        if p:
            meta.append("Գին՝ " + p)
    if d.get("place"):
        pl = clean(translate(sess, d["place"]))
        if pl:
            meta.append("Տեղ՝ " + pl[:60])

    desc = clean(translate(sess, (d.get("desc") or "")[:400]))
    if head and desc.lower().startswith(head.lower()[:25]):
        desc = desc[len(head):].strip(" .,-")
    if head and head.strip(" .") == desc.strip(" ."):
        desc = ""

    blocks = [head]
    if meta:
        blocks.append("\n".join(meta))
    if desc:
        lines, cur = [], ""
        for s in re.split(r"(?<=[.!?])\s+", desc):
            if cur and len(cur) + len(s) > 120:
                lines.append(cur)
                cur = s
            else:
                cur = (cur + " " + s).strip()
        if cur:
            lines.append(cur)
        blocks.append("\n".join(lines))

    out = []
    for b in blocks:
        b = "\n".join(l for l in b.split("\n") if l.strip() and not junk(l))
        if b.strip():
            out.append(b.strip())
    return "\n\n".join(out)[:1024]

def fp_key(d, date):
    t = " ".join([d.get("title") or "", d.get("price") or "", date or ""]).lower()
    h = 0
    for ch in " ".join(t.split()):
        h = (h * 131 + ord(ch)) % 1000000007
    return "fp%d" % h

def tg_post(sess, method, data, files=None, timeout=90):
    for attempt in range(SEND_TRIES):
        try:
            r = sess.post("https://api.telegram.org/bot%s/%s" % (TOKEN, method),
                          data=data, files=files, timeout=timeout)
            print("telegram %s: %s %s" % (method, r.status_code, r.text[:120]))
            if r.ok:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(5 * (attempt + 1))
                continue
            return None
        except Exception as e:
            print("telegram %s սխալ %s" % (method, type(e).__name__))
            time.sleep(5 * (attempt + 1))
    return None

def send(sess, d, caption):
    blobs = []
    for u in (d.get("photos") or [])[:10]:
        b = photo_bytes(sess, u, d["url"])
        if b and len(b) <= MAXP:
            blobs.append(b)

    markup = {"inline_keyboard": [
        [{"text": "Связаться", "url": d["url"]}],
        [{"text": "☺️Поддержать группу", "url": GROUP_BOOST}],
    ]}

    if not blobs:
        r = tg_post(sess, "sendMessage",
                    {"chat_id": CHAT_ID, "text": caption,
                     "reply_markup": json.dumps(markup)}, timeout=60)
        return r is not None

    if len(blobs) >= 2:
        files = {"p%d" % i: ("p%d.jpg" % i, b) for i, b in enumerate(blobs)}
        media = [{"type": "photo", "media": "attach://p0", "caption": caption}]
        for i in range(1, len(blobs)):
            media.append({"type": "photo", "media": "attach://p%d" % i})
        r = tg_post(sess, "sendMediaGroup",
                    {"chat_id": CHAT_ID, "media": json.dumps(media, ensure_ascii=False)},
                    files=files, timeout=180)
        if r is not None:
            mid = None
            try:
                res = r.json().get("result") or []
                if res:
                    mid = res[0]["message_id"]
            except Exception:
                pass
            payload = {"chat_id": CHAT_ID, "text": "Связаться с продавцом",
                       "reply_markup": json.dumps(markup)}
            if mid:
                payload["reply_parameters"] = json.dumps({"message_id": mid})
            tg_post(sess, "sendMessage", payload, timeout=40)
            return True
        print("  ալբոմը չանցավ, մեկ նկարով")

    r = tg_post(sess, "sendPhoto",
                {"chat_id": CHAT_ID, "caption": caption,
                 "reply_markup": json.dumps(markup)},
                files={"photo": ("p.jpg", blobs[0])}, timeout=150)
    return r is not None

def load():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception as e:
            print("state-gortsarq.json չի կարդացվում:", e)
    return {"queue": [], "published": {}, "seen": {}, "fps": {}}

def save(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)

def norm_queue(st):
    q = []
    for e in st.get("queue", []):
        p = e.get("path") if isinstance(e, dict) else e
        p = str(p).split("]")[0].split(")")[0]
        mm = ID_RE.search(p)
        q.append({"id": mm.group(1) if mm else p, "path": p,
                  "price": e.get("price", "") if isinstance(e, dict) else "",
                  "photo": e.get("photo", "") if isinstance(e, dict) else "",
                  "cat": e.get("cat", "") if isinstance(e, dict) else "",
                  "date": e.get("date", "") if isinstance(e, dict) else ""})
    st["queue"] = q

def trim(st):
    while len(st["queue"]) > QUEUE_MAX:
        gone = st["queue"].pop(0)
        st["seen"][gone["path"]] = True
    if len(st["published"]) > 50000:
        st["published"] = {k: st["published"][k] for k in list(st["published"])[-20000:]}
    if len(st["seen"]) > 50000:
        for k in list(st["seen"])[:20000]:
            st["seen"].pop(k, None)
    if len(st["fps"]) > 20000:
        for k in list(st["fps"])[:12000]:
            st["fps"].pop(k, None)

def publish_one(sess, st, e, now):
    path = e["path"]
    iid = e.get("id") or ""

    if st["published"].get(path):
        return "skip"

    d = None
    try:
        d = item_data(sess, path)
    except Exception as ex:
        print("ошибка разбора:", ex)

    if not d:
        if not site_up(sess):
            print("  կայքը չի բացվում, հայտարարությունը մնում է գլխին:", iid)
            return "hold"
        st["seen"][path] = True
        return "dropcard"

    if not d.get("price") and e.get("price"):
        d["price"] = e["price"]
    if not d.get("photos") and e.get("photo"):
        d["photos"] = [e["photo"]]

    if not d.get("date"):
        d["date"] = e.get("date") or today_str()

    cap = make_caption(sess, d)
    if not cap.strip():
        st["seen"][path] = True
        return "drop"

    k = fp_key(d, d.get("date"))
    if st["fps"].get(k):
        st["seen"][path] = True
        return "skip"

    if send(sess, d, cap):
        st["published"][path] = d.get("date") or today_str()
        st["fps"][k] = d.get("date") or today_str()
        led_add(SITE, iid, now)
        print("опубликовано: %s (%s) фото %d" % (iid, path, len(d.get("photos") or [])))
        return "ok"
    return "fail"

def publish_phase(sess, st, now, limit=1):
    done, tried = 0, 0
    while st["queue"] and done < limit and tried < MAX_TRIES:
        e = st["queue"].pop(0)
        tried += 1
        res = publish_one(sess, st, e, now)
        if res == "ok":
            done += 1
            save(st)
        elif res in ("drop", "dropcard", "skip"):
            save(st)
            continue
        else:
            st["queue"].insert(0, e)
            break
    return done

def main():
    st = load()
    st.setdefault("published", {})
    st.setdefault("seen", {})
    st.setdefault("fps", {})
    norm_queue(st)

    sess = requests.Session()
    now = time.time()
    deadline = now + MAX_SECONDS

    done = publish_phase(sess, st, now, MAX_POSTS)
    save(st)

    items = get_items(sess, deadline)
    print("всего найдено:", len(items))

    in_queue = set(e["path"] for e in st["queue"])
    fresh = []
    for iid, it in items.items():
        if it["path"] in st["seen"] or it["path"] in st["published"]:
            continue
        if it["path"] in in_queue:
            continue
        fresh.append(it)
    if fresh:
        st["queue"].extend(fresh)
        print("новых в очередь:", len(fresh))

    trim(st)
    save(st)

    if done < MAX_POSTS and st["queue"]:
        done += publish_phase(sess, st, time.time(), MAX_POSTS - done)

    trim(st)
    save(st)
    print("готово, публикаций за запуск: %d | очередь: %d" % (done, len(st["queue"])))
    return done

if __name__ == "__main__":
    main()
