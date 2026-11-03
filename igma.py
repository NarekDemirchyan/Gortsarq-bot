# -*- coding: utf-8 -*-
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

SITE        = "igma"
STATE       = "state-igma.json"
QUEUE_MAX   = 3000
FRESH_DAYS  = 60
PER_CAT     = 2000
MAX_SECONDS = 1500
MAX_PAGES   = 40
MAX_TRIES   = 4
MAX_POSTS   = 1
SEND_TRIES  = 3
MAXP        = 9 * 1024 * 1024

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
)

CANDIDATES = ("cat-200", "cat-400", "cat-500", "cat-600", "cat-700", "cat-800",
              "cat-900", "cat-1000", "cat-2000", "cat-3000", "cat-4000",
              "cat-5000", "cat-6000", "cat-7000", "cat-8000", "cat-9000")

EXCLUDE  = ("անշարժ", "աշխատանք", "ծառայութ", "տրանսպորտ", "транспорт", "բոլոր")
CARD_RE  = re.compile(r"\[!\[[^\]]*\]\(([^)]+)\)\]\((https://igma\.am/(?:[a-z]{2}/)?ads/(\d+))\)")
PRICE_RE = re.compile(r"([\d][\d\s ]{0,12})\s*(֏|\$|€)")
IMG_RE   = re.compile(r"!\[[^\]]*\]\((https?://[^)]+)\)")
PHONE_RE = re.compile(r"(?:\+374|0)\d{2}[\s\-]?\d{2}[\s\-]?\d{2}[\s\-]?\d{2}")

BAD = ("http", "www.", "igma.am", "размещено", "обновлено", "информация",
       "image", "аватар", "ավատար", "logo", "title", "url", "статус",
       "категория", "տեղադրված", "թարմացվել", "igmo", "— igma", "- igma")

def scrub(text):
    t = " ".join((text or "").split())
    t = re.sub(r"\s*([,.;:!?])\s*", r"\1 ", t)
    t = re.sub(r"^[\s,.;:!?\-]+", "", t)
    return t.strip(" ,.;:!?-\"")

def clean(text):
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
    t = " ".join(w for w in t.split() if not w.lower().startswith(("http", "www.")))
    return scrub(t)

def junk(text):
    low = (text or "").lower()
    return any(b in low for b in BAD)

def money(price):
    m = re.match(r"([\d\s.,]+)\s*(֏|\$|€)?", scrub(price))
    if not m:
        return ""
    digits = "".join(ch for ch in m.group(1) if ch.isdigit())
    if not digits:
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
    bare = url.split("://", 1)[-1]
    for p in ("https://images.weserv.nl/?url=" + bare,
              "https://wsrv.nl/?url=" + bare):
        try:
            r = sess.get(p, headers=h, timeout=25)
            if img_ok(r) and len(r.content) <= MAXP:
                return r.content
        except Exception:
            pass
    _FAILS[0] += 1
    print("  նկարը չներբեռնվեց:", url)
    return None

def cats(sess, st):
    if st.get("cats"):
        return st["cats"]
    found = []
    for cat in CANDIDATES:
        md = fetch(sess, "%s/listings?category=%s" % (BASE, cat))
        if not md:
            continue
        m = re.search(r"^#\s*Igma\.am\s*[—–-]\s*(.+)$", md, re.M)
        name = scrub(m.group(1)) if m else ""
        if not name or any(x in name.lower() for x in EXCLUDE):
            print("  %s՝ «%s» — բաց թողնված" % (cat, name))
            continue
        found.append(cat)
        print("  %s՝ «%s»" % (cat, name))
        time.sleep(1)
    if not found:
        found = ["cat-400", "cat-500", "cat-600"]
    st["cats"] = found
    save(st)
    return found

def get_items(sess, st, deadline):
    found = {}
    for cat in cats(sess, st):
        per_cat = 0
        for page in range(1, MAX_PAGES + 1):
            if time.time() > deadline:
                return found
            url = "%s/listings?category=%s" % (BASE, cat)
            if page > 1:
                url += "&page=%d" % page
            md = fetch(sess, url)
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
                found[path] = {"id": m.group(3), "path": path, "date": "",
                               "price": price, "photo": m.group(1), "cat": cat}
                n += 1
            per_cat += n
            print("[%s] стр.%d: новых %d" % (cat, page, n))
            if n == 0 or per_cat >= PER_CAT:
                break
            time.sleep(1)
    return found

def find_phone(md):
    for line in md.split("\n"):
        if "եռախոս" in line or "телефон" in line.lower():
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
    m = re.search(r"Title\W{0,3}\s*(.+)", md)
    if m:
        title = clean(m.group(1).split("—")[0].split("- Igma")[0])
    if not title:
        m = re.search(r"^#+\s*(.+)$", md, re.M)
        if m:
            title = clean(m.group(1))
    if junk(title):
        title = ""

    price = ""
    mp = PRICE_RE.search(md)
    if mp:
        num = " ".join(mp.group(1).split())
        if re.search(r"[1-9]", num):
            price = num + " " + mp.group(2)

    place = ""
    mpl = re.search(r"Տեղ\W{0,3}\s*(.+)", md)
    if mpl:
        parts = [p.strip() for p in re.split(r"[—–]", mpl.group(1)) if p.strip()]
        if parts:
            place = clean(" — ".join(parts[:2]))[:60]
    if junk(place) or "2026" in place:
        place = ""

    desc = ""
    m = re.search(r"(?:Նկարագրություն|Описание)\W*\n+(.+?)(?:\n\s*\n|\Z)", md, re.S)
    if m:
        desc = clean(" ".join(m.group(1).split()))
    if len(desc) < 20:
        best = ""
        for line in md.split("\n"):
            s = clean(line)
            if len(s) > len(best) and not junk(s) and s != title:
                best = s
        desc = best if len(best) > 20 else desc

    updated = ""
    mu = re.search(r"(?:Թարմացվել է|Обновлено)\D{0,3}(\d{2}\.\d{2}\.\d{4})", md)
    if mu:
        updated = mu.group(1)
    posted = ""
    mpo = re.search(r"(?:Տեղադրված է|Размещено)\D{0,3}(\d{2}\.\d{2}\.\d{4})", md)
    if mpo:
        posted = mpo.group(1)

    photos = []
    for mi in IMG_RE.finditer(md):
        u = mi.group(1)
        if "logo" in u or "promo" in u:
            continue
        if ("r2.dev" in u or "/storage/" in u) and u not in photos:
            photos.append(u)

    print("  %s: фото %d, «%s», «%s», опис %d, обн «%s»"
          % (path, len(photos), title[:40], price, len(desc), updated))

    return {"title": title, "price": price, "place": place, "desc": desc,
            "phone": find_phone(md), "photos": photos[:10],
            "date": updated or posted, "url": BASE + path}

def make_caption(sess, d):
    head = clean(translate(sess, d.get("title") or ""))
    if not head or len(head) < 6:
        head = clean(translate(sess, (d.get("desc") or "")[:140]))
    head = head[:90]

    lines = []
    if head:
        lines.append(head)
    if d.get("price"):
        p = money(d["price"])
        if p:
            lines.append("Գին՝ " + p)
    if d.get("place"):
        pl = clean(translate(sess, d["place"]))
        if pl:
            lines.append("Տեղ՝ " + pl[:60])
    if d.get("phone"):
        lines.append("Հեռախոս՝ " + d["phone"])

    body = "\n".join(l for l in lines if l and not junk(l))

    desc = clean(translate(sess, (d.get("desc") or "")[:400]))
    if desc and desc != head and len(body) + len(desc) < 950:
        body = (body + "\n\n" + desc).strip()

    out = [l for l in body.split("\n") if l.strip() and not junk(l)]
    return "\n".join(out)[:1024]

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
            print("state-igma.json չի կարդացվում:", e)
    return {"queue": [], "published": {}, "seen": {}}

def save(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)

def norm_queue(st):
    q = []
    for e in st.get("queue", []):
        p = e.get("path") if isinstance(e, dict) else e
        p = str(p).split("]")[0].split(")")[0]
        mm = re.search(r"/ads/(\d+)", p)
        q.append({"id": mm.group(1) if mm else p, "path": p,
                  "price": e.get("price", "") if isinstance(e, dict) else "",
                  "photo": e.get("photo", "") if isinstance(e, dict) else "",
                  "cat": e.get("cat", "") if isinstance(e, dict) else ""})
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

def publish_one(sess, st, e, now):
    path = e["path"]
    iid = e.get("id") or ""

    if st["published"].get(path):
        return "skip"

    try:
        d = item_data(sess, path)
    except Exception as ex:
        print("ошибка разбора:", ex)
        d = None

    if not d:
        if not site_up(sess):
            return "hold"
        st["seen"][path] = True
        return "dropcard"

    if not d.get("price") and e.get("price"):
        d["price"] = e["price"]
    if not d.get("photos") and e.get("photo"):
        d["photos"] = [e["photo"]]

    date = d.get("date") or ""
    if date and date not in last_days(FRESH_DAYS):
        st["seen"][path] = True
        return "drop"

    cap = make_caption(sess, d)
    if not cap:
        st["seen"][path] = True
        return "drop"

    if send(sess, d, cap):
        st["published"][path] = date or today_str()
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
    norm_queue(st)

    sess = requests.Session()
    now = time.time()
    deadline = now + MAX_SECONDS

    done = publish_phase(sess, st, now, MAX_POSTS)
    save(st)

    items = get_items(sess, st, deadline)
    print("всего найдено:", len(items))

    in_queue = set(e["path"] for e in st["queue"])
    by_cat = {}
    for path, it in items.items():
        if path in st["seen"] or path in st["published"] or path in in_queue:
            continue
        by_cat.setdefault(it["cat"], []).append(it)

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

    if done < MAX_POSTS and st["queue"]:
        done += publish_phase(sess, st, time.time(), MAX_POSTS - done)

    trim(st)
    save(st)
    print("готово, публикаций за запуск: %d | очередь: %d" % (done, len(st["queue"])))
    return done

if __name__ == "__main__":
    main()
