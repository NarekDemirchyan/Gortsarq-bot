# -*- coding: utf-8 -*-
"""
igma.am -> Telegram («Բարախոլկա Երևան»).

 1. Հրապարակում հերթի գլխից՝ FIFO։
 2. Հավաքում վերջին 60 օրվա հայտարարությունները, դնում պոչ։
 3. Մեկ անգամ հրապարակվածը չի կրկնվում։
 4. Տեքստում հղում, աստղանիշ կամ backtick չի մնում՝ միայն կոճակը։
 5. Հերթի պոչը լցվում է բաժինների միջև հերթով։
"""
import os
import re
import json
import time
import urllib.parse

import requests

from fetcher import translate, script, has_cyr, is_translit_hy, led_add, led_has

try:
    from fetcher import to_md
except ImportError:
    def to_md(html, url=""):
        return html

GT = chr(62)

TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
JINA_KEY = os.environ.get("JINA_KEY", "")

SITE        = "igma"
STATE       = "state-igma.json"
LED         = "published.json"
QUEUE_MAX   = 3000
FRESH_DAYS  = 60
PER_CAT     = 2000
MAX_SECONDS = 1500
MAX_PAGES   = 40
MAX_TRIES   = 4
MAX_POSTS   = 1
SEND_TRIES  = 3
WATCH_K     = 30
MAXP        = 9 * 1024 * 1024

EXCLUDE_V   = 3

BASE        = "https://igma.am"
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
    "https://api.cors.lol/?url=",
)

CANDIDATES = ("cat-200", "cat-400", "cat-500", "cat-600", "cat-700", "cat-800",
              "cat-900", "cat-1000", "cat-2000", "cat-3000", "cat-4000",
              "cat-5000", "cat-6000", "cat-7000", "cat-8000", "cat-9000",
              "cat-10000")

EXCLUDE = ("անշարժ", "աշխատանք", "ծառայութ", "տրանսպորտ", "транспорт", "բոլոր")

CARD_RE  = re.compile(r"\[!\[[^\]]*\]\(([^)]+)\)\]\((https://igma\.am/(?:[a-z]{2}/)?ads/(\d+))\)")
PRICE_RE = re.compile(r"([\d][\d\s ]{0,12})\s*(֏|\$|€)")
IMG_RE   = re.compile(r"!\[[^\]]*\]\((https?://[^)]+)\)")
ITEM_ID_RE = re.compile(r"/ads/(\d+)")
SPEC_RE  = re.compile(r"^([^:]{2,30}):\s*(.+)$")

HEAD_FALLBACK = "Գովազդ"

JUNK_KEYS = ("информация", "размещено", "обновлено", "категория", "статус")

def scrub(text):
    t = " ".join((text or "").split())
    t = re.sub(r"\s*([,.;:!?])\s*", r"\1 ", t)
    t = re.sub(r"^[\s,.;:!?\-]+", "", t)
    return t.strip(" ,.;:!?-\"")

def clean(text):
    """Մաքրում է հղումները, աստղանիշները, backtick-ը և ավելորդ նշանները։"""
    t = text or ""
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
    t = t.replace("[", " ").replace("]", " ")
    t = t.replace("**", " ").replace("`", " ").replace("_", " ")
    words = [w for w in t.split() if not w.lower().startswith(("http", "www."))]
    t = " ".join(words)
    t = t.replace("igma.am", " ")
    return scrub(t)

def money(price):
    t = scrub(price or "")
    m = re.match(r"([\d\s.,]+)\s*(֏|\$|€)?", t)
    if not m:
        return ""
    digits = "".join(ch for ch in m.group(1) if ch.isdigit())
    if not digits:
        return ""
    num = "{:,}".format(int(digits)).replace(",", " ")
    cur = m.group(2) or ""
    return (num + " " + cur).strip()

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

# ---------- ցանց ----------

_FAILS = [0]

def fetch(sess, url):
    if _FAILS[0] >= 8:
        return ""
    try:
        r = sess.get(url, headers=HDRS, timeout=15)
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
        r = sess.get("https://r.jina.ai/" + url, headers=h, timeout=25)
        if r.status_code == 200 and good(r.text):
            _FAILS[0] = 0
            return r.text
    except Exception:
        pass
    for p in PROXIES[:2]:
        try:
            r = via_proxy(sess, p, url, HDRS, 20)
            if r.status_code == 200 and good(r.text):
                _FAILS[0] = 0
                return to_md(r.text, url)
        except Exception:
            pass
    _FAILS[0] += 1
    print("  չկարդացվեց:", url)
    return ""

def site_up(sess):
    return bool(fetch(sess, BASE + "/listings"))

# ---------- նկարներ ----------

def relay_urls(u):
    bare = u.split("://", 1)[-1]
    return [f"https://images.weserv.nl/?url={bare}",
            f"https://wsrv.nl/?url={bare}"]

def img_ok(r):
    b = r.content or b""
    if r.status_code != 200 or len(b) < 800:
        return False
    return not b.lstrip()[:1] == b"<"

def photo_bytes(sess, url, ref):
    if _FAILS[0] >= 12:
        return None
    h = dict(HDRS)
    h["Referer"] = ref or BASE + "/"
    h["Accept"] = "image/avif,image/webp,image/jpeg,image/*,*/*;q=0.8"
    try:
        r = sess.get(url, headers=h, timeout=25)
        if img_ok(r) and len(r.content) <= MAXP:
            _FAILS[0] = 0
            return r.content
    except Exception:
        pass
    for p in PROXIES[:2]:
        try:
            r = via_proxy(sess, p, url, h, 25)
            if img_ok(r) and len(r.content) <= MAXP:
                return r.content
        except Exception:
            pass
    for p in relay_urls(url)[:1]:
        try:
            r = sess.get(p, headers=h, timeout=25)
            if img_ok(r) and len(r.content) <= MAXP:
                return r.content
        except Exception:
            pass
    _FAILS[0] += 1
    print("  նկարը չներբեռնվեց:", url)
    return None

# ---------- բաժիններ ----------

def cats(sess, st):
    if st.get("cats") and st.get("exclude_v") == EXCLUDE_V:
        return st["cats"]
    found = []
    for cat in CANDIDATES:
        md = fetch(sess, "%s/listings?category=%s" % (BASE, cat))
        if not md:
            continue
        m = re.search(r"^#\s*Igma\.am\s*[—–-]\s*(.+)$", md, re.M)
        name = scrub(m.group(1)) if m else ""
        low = name.lower()
        if not name or any(x in low for x in EXCLUDE):
            print(f"  {cat}՝ «{name}» — բաց թողնված")
            continue
        found.append(cat)
        print(f"  {cat}՝ «{name}»")
        time.sleep(1)
    if not found:
        print("  բաժինները չճանաչվեցին, վերցնում ենք հիմնականները")
        found = ["cat-400", "cat-500", "cat-600"]
    st["cats"] = found
    st["exclude_v"] = EXCLUDE_V
    save(st)
    return found

# ---------- հավաքում ----------

def list_url(cat, page):
    u = "%s/listings?category=%s" % (BASE, cat)
    return u if page == 1 else u + "&page=%d" % page

def get_items(sess, st, deadline):
    found = {}
    for cat in cats(sess, st):
        per_cat = 0
        for page in range(1, MAX_PAGES + 1):
            if time.time() > deadline:
                print("  հավաքում. ժամանակը վերջացավ")
                return found
            md = fetch(sess, list_url(cat, page))
            if not md:
                break
            n = 0
            for m in CARD_RE.finditer(md):
                path = "/ads/" + m.group(3)
                if path in found:
                    continue
                tail = md[m.end():m.end() + 400]
                price = ""
                pm = PRICE_RE.search(tail)
                if pm:
                    price = " ".join(pm.group(1).split()) + " " + pm.group(2)
                found[path] = {"id": m.group(3), "path": path,
                               "date": "", "price": price, "place": "",
                               "photo": m.group(1), "cat": cat}
                n += 1
            per_cat += n
            print(f"[{cat}] стр.{page}: новых {n}")
            if n == 0 or per_cat >= PER_CAT:
                break
            time.sleep(1)
    return found

# ---------- քարտ ----------

def find_phone(md):
    """Հեռախոսը վերցնում ենք միայն այն տողից, որտեղ բառ կա՝ հեռախոս/телефон։"""
    for line in md.split("\n"):
        low = line.lower()
        if "եռախոս" in line or "телефон" in low:
            digits = "".join(ch for ch in line if ch.isdigit() or ch == "+")
            if len(digits) >= 8:
                return digits
    return ""

def item_data(sess, path):
    md = fetch(sess, BASE + path)
    if not md:
        return None

    cut = md.find("Igma.am app")
    if cut > 0:
        md = md[:cut]

    title = ""
    mt = re.search(r"^#\s+(.+)$", md, re.M)
    if mt:
        title = clean(mt.group(1))
    if not title:
        mt = re.search(r"^#{1,3}\s+(.+)$", md, re.M)
        if mt:
            title = clean(mt.group(1))

    price = ""
    mp = PRICE_RE.search(md[:1200])
    if mp:
        num = " ".join(mp.group(1).split())
        if re.search(r"[1-9]", num):
            price = num + " " + mp.group(2)

    tail = md[mp.end():mp.end() + 500] if mp else md[:1000]
    parts = []
    for line in tail.split("\n"):
        low = clean(line)
        if not low or low.startswith("#"):
            continue
        if low.lower().startswith(JUNK_KEYS):
            continue
        if "եռախոս" in low or "телефон" in low.lower():
            continue
        parts.append(low)
        if len(parts) >= 2:
            break
    place = clean(" ".join(parts)).replace("|", "·")[:80]
    if "2026" in place or "20" + "2" in place[:3]:
        place = ""
    if "елефон" in place.lower() or "եռախոս" in place:
        place = ""

    desc = ""
    mdsc = re.search(r"##\s*Նկարագրություն\s*\n+(.+?)(?:\n+\s*Տեղադրված է|\Z)", md, re.S)
    if mdsc:
        desc = clean(" ".join(mdsc.group(1).split()))

    posted = ""
    mpo = re.search(r"Տեղադրված է\s*(\d{2}\.\d{2}\.\d{4})", md)
    if mpo:
        posted = mpo.group(1)

    updated = ""
    mup = re.search(r"Թարմացվել է\s*(\d{2}\.\d{2}\.\d{4})", md)
    if mup:
        updated = mup.group(1)

    specs = []
    for line in md.split("\n"):
        s = clean(line)
        if not s or len(s) > 90 or s.startswith("#"):
            continue
        msm = SPEC_RE.match(s)
        if not msm:
            continue
        k = clean(msm.group(1))
        v = clean(msm.group(2))
        if not v or len(v) > 60:
            continue
        if k.lower().startswith(JUNK_KEYS):
            continue
        if "елефон" in k.lower() or "եռախոս" in k:
            continue
        if v.lower().startswith(("http", "www.")):
            continue
        specs.append((k, v))
        if len(specs) >= 5:
            break

    phone = find_phone(md)

    photos = []
    for mi in IMG_RE.finditer(md):
        u = mi.group(1)
        if "logo" in u or "promo" in u:
            continue
        if "r2.dev" in u or "/storage/ads/" in u:
            if u not in photos:
                photos.append(u)

    print(f"  {path}: նկար {len(photos)}, «{title[:40]}», «{price}», "
          f"նկարագրություն {len(desc)}, թարմացված «{updated}», բնութագիր {len(specs)}")

    return {"title": title, "price": price, "desc": desc, "place": place,
            "photos": photos[:10], "posted": posted, "updated": updated,
            "specs": specs, "phone": phone,
            "date": updated or posted, "url": BASE + path}

# ---------- տեքստ ----------

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
    parts = [clean(headline(sess, d)) or HEAD_FALLBACK]

    if d.get("price"):
        p = money(d["price"])
        if p:
            parts.append("Գին՝ " + p)

    if d.get("place"):
        place = clean(translate(sess, d["place"]))
        if place and len(place) > 2:
            parts.append("Տեղ՝ " + place[:80])

    for k, v in (d.get("specs") or [])[:5]:
        kk = clean(translate(sess, k))
        vv = clean(translate(sess, v))
        if kk and vv:
            parts.append((kk + "՝ " + vv)[:80])

    if d.get("phone"):
        parts.append("Հեռախոս՝ " + d["phone"])

    body = "\n".join(parts)

    if d.get("desc"):
        desc = clean(translate(sess, d["desc"][:500]))
        if desc and len(body) + len(desc) + 2 <= 1000:
            body = body + "\n\n" + desc

    out = []
    for line in body.split("\n"):
        line = clean(line)
        if line:
            out.append(line)
    return "\n".join(out)[:1024]

def fp_key(d, date):
    t = " ".join([d.get("title") or "", d.get("price") or "", date or ""]).lower()
    t = " ".join(t.split())
    h = 0
    for ch in t:
        h = (h * 131 + ord(ch)) % 1000000007
    return "fp%d" % h

# ---------- Telegram ----------

def tg_post(sess, method, data, files=None, timeout=90):
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
                    files=files, timeout=180)
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
                files={"photo": ("p.jpg", blobs[0])}, timeout=150)
    return r is not None

# ---------- վիճակ ----------

def load():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception as e:
            print("state-igma.json չի կարդացվում:", e)
    return {"queue": [], "published": {}, "seen": {}, "fps": {},
            "watch": [], "watch_pos": 0}

def save(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)

def norm_queue(st):
    q = []
    for e in st.get("queue", []):
        if isinstance(e, dict) and e.get("path"):
            p = str(e["path"]).split("]")[0].split(")")[0]
            iid = str(e.get("id") or "")
            if not iid:
                mm = ITEM_ID_RE.search(p)
                iid = mm.group(1) if mm else p
            q.append({"id": iid, "path": p, "date": e.get("date", ""),
                      "price": e.get("price", ""), "place": e.get("place", ""),
                      "photo": e.get("photo", ""), "cat": e.get("cat", "")})
        elif isinstance(e, str):
            p = e.split("]")[0].split(")")[0]
            mm = ITEM_ID_RE.search(p)
            q.append({"id": mm.group(1) if mm else p, "path": p, "date": "",
                      "price": "", "place": "", "photo": "", "cat": ""})
    st["queue"] = q

def trim(st):
    while len(st["queue"]) > QUEUE_MAX:
        gone = st["queue"].pop(0)
        st["seen"][gone["path"]] = True
        print("առաստաղ. գլխից դուրս եկավ:", gone["path"])
    if len(st["published"]) > 50000:
        keys = list(st["published"])[-20000:]
        st["published"] = {k: st["published"][k] for k in keys}
    if len(st["seen"]) > 50000:
        for k in list(st["seen"])[:20000]:
            st["seen"].pop(k, None)
    if len(st["fps"]) > 20000:
        for k in list(st["fps"])[:12000]:
            st["fps"].pop(k, None)
    if len(st["watch"]) > 6000:
        st["watch"] = st["watch"][-4000:]

# ---------- հրապարակում ----------

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
            print("  կայքը չի բացվում. հայտարարությունը մնում է հերթի գլխում:", iid)
            return "hold"
        print("  քարտը չի ընթերցվում, դուրս ենք գցում ընդմիշտ:", iid)
        st["seen"][path] = True
        return "dropcard"

    if not d.get("price") and e.get("price"):
        d["price"] = e["price"]
    if not d.get("photos") and e.get("photo"):
        d["photos"] = [e["photo"]]

    date = d.get("date") or e.get("date") or ""
    if date and date not in last_days(FRESH_DAYS):
        print("  հին հայտարարություն, դուրս ենք գցում:", iid, date)
        st["seen"][path] = True
        return "drop"

    cap = make_caption(sess, d)
    if not cap:
        print("  դատարկ հայտարարություն, դուրս ենք գցում հերթից:", iid)
        st["seen"][path] = True
        return "drop"

    k = fp_key(d, date)
    if st["fps"].get(k):
        print("  նույնը արդեն եղել է՝", k)
        st["seen"][path] = True
        return "skip"

    if send(sess, d, cap):
        st["published"][path] = date or today_str()
        st["fps"][k] = date or today_str()
        if path not in st["watch"]:
            st["watch"].append(path)
        led_add(SITE, iid, now)
        print(f"опубликовано: {iid} ({path}) фото {len(d.get('photos') or [])}")
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
        elif res == "hold":
            st["queue"].insert(0, e)
            break
        elif res == "fail":
            st["queue"].insert(0, e)
            break
    return done

# ---------- թարմացումներ ----------

def recheck(sess, st, deadline):
    watch = st.get("watch") or []
    if not watch:
        return 0
    pos = int(st.get("watch_pos", 0)) % len(watch)
    picked = [watch[(pos + k) % len(watch)] for k in range(min(WATCH_K, len(watch)))]
    st["watch_pos"] = (pos + len(picked)) % len(watch)

    queued = {e["path"] for e in st["queue"]}
    added = 0
    for path in picked:
        if time.time() > deadline:
            break
        d = item_data(sess, path)
        if not d or not d.get("date"):
            continue
        prev = st["published"].get(path) or ""
        if dkey(d["date"]) > dkey(prev) and path not in queued:
            st["queue"].append({"id": path.rsplit("/", 1)[-1], "path": path,
                                "date": d["date"], "price": d.get("price", ""),
                                "place": d.get("place", ""),
                                "photo": (d.get("photos") or [""])[0],
                                "cat": ""})
            queued.add(path)
            added += 1
            print("  թարմացվածը հերթում է:", path, d["date"])
    trim(st)
    print("թարմացումների ստուգում. նորերը՝", added, "| հերթ:", len(st["queue"]))
    save(st)
    return added

# ---------- հիմնական ----------

def main():
    st = load()
    st.setdefault("published", {})
    st.setdefault("seen", {})
    st.setdefault("fps", {})
    st.setdefault("watch", [])
    st.setdefault("watch_pos", 0)
    norm_queue(st)

    sess = requests.Session()
    now = time.time()
    deadline = now + MAX_SECONDS

    done = publish_phase(sess, st, now, MAX_POSTS)
    save(st)

    items = get_items(sess, st, deadline)
    print("всего найдено:", len(items))

    in_queue = {e["path"] for e in st["queue"]}
    by_cat = {}
    for path, it in items.items():
        if path in st["seen"] or path in st["published"] or path in in_queue:
            continue
        by_cat.setdefault(it.get("cat", ""), []).append({
            "id": it["id"], "path": path, "date": it.get("date", ""),
            "price": it.get("price", ""), "place": "",
            "photo": it.get("photo", ""), "cat": it.get("cat", "")})

    fresh = []
    order = list(by_cat)
    i = 0
    while order and any(by_cat[c] for c in order):
        c = order[i % len(order)]
        if by_cat[c]:
            fresh.append(by_cat[c].pop(0))
        i += 1

    if fresh:
        st["queue"].extend(fresh)
        print("новых в очередь:", len(fresh))
    trim(st)
    save(st)

    if time.time() < deadline:
        recheck(sess, st, deadline)

    if done < MAX_POSTS and st["queue"]:
        done += publish_phase(sess, st, time.time(), MAX_POSTS - done)

    trim(st)
    save(st)
    print(f"готово, публикаций за запуск: {done} | очередь: {len(st['queue'])}")
    return done

if __name__ == "__main__":
    main()
