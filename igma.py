# -*- coding: utf-8 -*-
"""
igma.am -> Telegram («Բարախոլկա Երևան»).

 - միայն վաճառքի բաժիններ
 - «նմանատիպ հայտարարություններ» բլոկը կտրվում է
 - բնութագրերը կարդացվում են կայքի աղյուսակից՝ «Վիճակ՝ …» տողերով
 - նոր «Թարմացվել է»-ով հայտարարությունը նորից մտնում է հերթ
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

SITE        = "igma"
STATE       = "state-igma.json"
QUEUE_MAX   = 3000
FRESH_DAYS  = 730
MAX_DAYS    = 400
MAX_SECONDS = 300
MAX_CAND    = 120
SCAN_BUDGET = 60
MAX_TRIES   = 2
MAX_POSTS   = 1
SEND_TRIES  = 2
WATCH_K     = 20
MAXP        = 9 * 1024 * 1024
EXCLUDE_V   = 5
RESET_V     = "reset_v5"

BASE        = "https://igma.am"
GROUP_BOOST = "https://t.me/boost/erevan03_baraxolka"

HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/61.0.3163.100 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,hy;q=0.8,en;q=0.7",
}

BLOCKED = ("just a moment", "performing security verification", "checking your browser",
           "enable javascript", "cf-browser-verification", "attention required",
           "access denied", "ddos-guard", "security check")

CANDIDATES = ("cat-200", "cat-400", "cat-500", "cat-600", "cat-700", "cat-800",
              "cat-900", "cat-1000", "cat-2000", "cat-3000", "cat-4000",
              "cat-5000", "cat-6000", "cat-7000", "cat-8000", "cat-9000")

EXCLUDE = ("անշարժ", "տրանսպորտ", "ավտոմեքենա", "աշխատանք", "ծառայություն",
           "վարձույթ", "недвижим", "транспорт", "автомобил", "работа",
           "услуг", "аренд", "прокат")

SIM = ("Նմանատիպ", "Похожие", "Similar", "Այլ հայտարարություն", "Igma.am app")

SPEC_HEAD = ("Ապրանքի մասին տեղեկատվություն", "Տեղեկատվություն ապրանքի",
             "Տեխնիկական բնութագրեր", "О товаре", "Характеристики")

CARD_RE  = re.compile(r"\[!\[[^\]]*\]\(([^)]+)\)\]\((https://igma\.am/(?:[a-z]{2}/)?ads/(\d+))\)")
PRICE_RE = re.compile(r"([\d][\d\s ]{0,12})\s*(֏|\$|€)")
IMG_RE   = re.compile(r"!\[[^\]]*\]\((https?://[^)]+)\)")

BAD = ("http", "www.", "igma.am", "размещено", "обновлено", "информация",
       "image", "аватар", "ավատար", "logo", "url", "статус", "категория",
       "տեղադրված", "թարմացվ", "цена", "цена`")

def scrub(text):
    t = " ".join((text or "").split())
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
    t = t.replace("igma.am", " ").replace("— Igma", " ").replace("- Igma", " ")
    return scrub(t)

def junk(text):
    low = (text or "").lower()
    return any(b in low for b in BAD)

def cut_similar(md):
    for s in SIM:
        cut = md.find(s)
        if cut > 0:
            md = md[:cut]
    return md

def stamp_of(s):
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})(?:\D{0,3}(\d{2}):(\d{2}))?", s or "")
    if not m:
        return (0, 0, 0, 0, 0)
    return (int(m.group(3)), int(m.group(2)), int(m.group(1)),
            int(m.group(4) or 0), int(m.group(5) or 0))

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

def fetch(sess, url):
    try:
        r = sess.get(url, headers=HDRS, timeout=12)
        if r.status_code == 200:
            t = to_md(r.text, url)
            if t and len(t) > 600:
                return t
    except Exception:
        pass
    print("  չկարդացվեց:", url)
    return ""

def site_up(sess):
    return bool(fetch(sess, BASE + "/"))

def img_ok(r):
    b = r.content or b""
    if r.status_code != 200 or len(b) < 800:
        return False
    return not b.lstrip()[:1] == b"<"

def photo_bytes(sess, url, ref):
    h = dict(HDRS)
    h["Referer"] = ref or BASE + "/"
    h["Accept"] = "image/avif,image/webp,image/jpeg,image/png,*/*;q=0.8"
    try:
        r = sess.get(url, headers=h, timeout=15)
        if img_ok(r) and len(r.content) <= MAXP:
            return r.content
    except Exception:
        pass
    return None

def get_cats(sess, st):
    if st.get("cats"):
        return st["cats"]
    return ["anon-1"]

def listing_url(cat, page):
    u = "%s/listings?category=%s" % (BASE, cat)
    if page > 1:
        u += "&page=%d" % page
    return u

def get_items(sess, st, deadline):
    found = {}
    for cat in get_cats(sess, st):
        per_cat = 0
        for page in range(1, 21):
            if time.time() > deadline:
                return found
            md = fetch(sess, listing_url(cat, page))
            if not md:
                break
            n = 0
            for m in CARD_RE.finditer(md):
                path = "/ads/" + m.group(3)
                if path in found:
                    continue
                tail = md[m.end():m.end() + 300]
                pm = PRICE_RE.search(tail)
                price = (" ".join(pm.group(1).split()) + " " + pm.group(2)) if pm else ""
                found[path] = {"id": m.group(3), "path": path,
                               "price": price, "photo": m.group(1) or "",
                               "cat": cat}
                n += 1
            per_cat += n
            print("[%s] էջ %d: նոր %d" % (cat, page, n))
            if n == 0 or per_cat >= 5000:
                break
            time.sleep(0.5)
    return found

def find_phone(md):
    for line in md.split("\n"):
        if "եռախոս" in line or "телефон" in line.lower():
            digits = "".join(ch for ch in line if ch.isdigit() or ch == "+")
            if 8 <= len(digits) <= 15:
                return digits
    return ""

def get_specs(md):
    specs = []
    for head in SPEC_HEAD:
        m = re.search(re.escape(head) + r"(.+?)(?:\n#{1,3}\s|\nՆկարագրություն|\Z)",
                      md, re.S)
        if not m:
            continue
        for line in m.group(1).split("\n"):
            s = clean(line)
            if not s or len(s) > 120:
                continue
            k = v = ""
            if "|" in s:
                cells = [c for c in (clean(x) for x in s.split("|")) if c]
                if len(cells) >= 2:
                    k, v = cells[0], cells[1]
            elif ":" in s or "՝" in s:
                k, v = [clean(x) for x in re.split(r"[:՝]", s, 1)]
            if not k or not v or len(k) > 30 or junk(v):
                continue
            if re.fullmatch(r"[-—\s]+", k):
                continue
            if (k, v) not in specs:
                specs.append((k, v))
            if len(specs) >= 5:
                return specs
    return specs

def item_data(sess, path):
    md = fetch(sess, BASE + path)
    if not md:
        return None

    md = cut_similar(md)

    title = ""
    m = re.search(r"Title\W{0,3}\s*(.+)", md)
    if m:
        title = clean(m.group(1).split("—")[0])[:90]
    if not title or junk(title) or title.lower().startswith("информация"):
        title = ""
        m = re.search(r"^#\s+(.+)", md, re.M)
        if m:
            t = clean(m.group(1))[:90]
            if t and not junk(t) and not t.lower().startswith("информация"):
                title = t

    price = ""
    mp = PRICE_RE.search(md)
    if mp:
        num = " ".join(mp.group(1).split())
        if re.search(r"[1-9]", num):
            price = num + " " + mp.group(2)

    place = ""
    m = re.search(r"Տեղ\s*[:՝]\s*(.+)", md)
    if m:
        parts = [p.strip() for p in re.split(r"[—–]", clean(m.group(1))) if p.strip()]
        if parts:
            place = " — ".join(parts[:2])[:60]
    if not place or len(place) < 3 or re.fullmatch(r"[\W\d_]+", place):
        place = ""
    if re.search(r"[A-Za-z]{2,}\s*\d", place):
        place = ""

    posted = ""
    mpo = re.search(r"Տեղադրվ(?:ած|ել) է\D{0,3}(\d{2}\.\d{2}\.\d{4})(?:\D{0,3}(\d{2}:\d{2}))?", md)
    if mpo:
        posted = mpo.group(1) + (" " + mpo.group(2) if mpo.group(2) else "")
    if not posted:
        mpo = re.search(r"Размещено\D{0,3}(\d{2}\.\d{2}\.\d{4})(?:\D{0,3}(\d{2}:\d{2}))?", md)
        if mpo:
            posted = mpo.group(1) + (" " + mpo.group(2) if mpo.group(2) else "")

    updated = ""
    mu = re.search(r"Թարմացվել է\D{0,3}(\d{2}\.\d{2}\.\d{4})(?:\D{0,3}(\d{2}:\d{2}))?", md)
    if mu:
        updated = mu.group(1) + (" " + mu.group(2) if mu.group(2) else "")
    if not updated:
        mu = re.search(r"Обновлено\D{0,3}(\d{2}\.\d{2}\.\d{4})(?:\D{0,3}(\d{2}:\d{2}))?", md)
        if mu:
            updated = mu.group(1) + (" " + mu.group(2) if mu.group(2) else "")

    stamp = updated or posted

    desc = ""
    m = re.search(r"Նկարագրություն\s*\n+(.+?)(?:\n\s*Տեղադրված է|\n\s*Տեղադրվել է|\n\s*Թարմացվել է|\Z)",
                  md, re.S)
    if m:
        desc = clean(" ".join(m.group(1).split()))
    if len(desc) < 20:
        best = ""
        for line in md.split("\n"):
            s = clean(line)
            if len(s) > len(best) and not junk(s) and s != title:
                best = s
        if len(best) > 20:
            desc = best

    specs = get_specs(md)

    photos = []
    names = set()
    for mi in IMG_RE.finditer(md):
        u = mi.group(1)
        low = u.lower()
        if "logo" in low or "promo" in low or "avatar" in low:
            continue
        name = u.rsplit("/", 1)[-1].split("?")[0]
        if name in names:
            continue
        if "r2.dev" in u or "/storage/" in u:
            names.add(name)
            photos.append(u)

    print("  %s: фото %d, «%s», «%s», բնութ. %d, опис %d, թարմ. «%s»"
          % (path, len(photos), title[:40], price, len(specs), len(desc), updated))

    return {"title": title, "price": price, "desc": desc, "place": place,
            "phone": find_phone(md), "photos": photos[:10], "specs": specs,
            "posted": posted, "updated": updated, "stamp": stamp,
            "date": updated[:10] or posted[:10], "url": BASE + path}

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
    head = clean(headline(sess, d))[:90]

    blocks = []
    if head:
        blocks.append(head)

    meta = []
    if d.get("price"):
        p = money(d["price"])
        if p:
            meta.append("Գին՝ " + p)
    if d.get("place"):
        pl = clean(translate(sess, d["place"]))
        if pl:
            meta.append("Տեղ՝ " + pl[:60])
    if meta:
        blocks.append("\n".join(meta))

    spec_lines = []
    for k, v in (d.get("specs") or [])[:5]:
        kk = clean(translate(sess, k))
        vv = clean(translate(sess, v))
        if kk and vv:
            spec_lines.append((kk + "՝ " + vv)[:80])
    if spec_lines:
        blocks.append("\n".join(spec_lines))

    desc = clean(translate(sess, (d.get("desc") or "")[:300]))
    if head and desc.lower().startswith(head.lower()[:25]):
        desc = desc[len(head):].strip(" .,-")
    if head and head.strip(".") == desc.strip("."):
        desc = ""

    if desc and spec_lines:
        blocks.append(desc.split(". ")[0][:220].strip())
    elif desc:
        lines, cur = [], ""
        for s in re.split(r"(?<=[.!?])\s+", desc):
            if cur and len(cur) + len(s) > 120:
                lines.append(cur)
                cur = s
            else:
                cur = (cur + " " + s).strip()
        if cur:
            lines.append(cur)
        blocks.append("\n".join(lines[:3]))

    if d.get("phone"):
        blocks.append("Հեռախոս՝ " + d["phone"])

    out = []
    for b in blocks:
        b = "\n".join(l for l in b.split("\n") if l.strip() and not junk(l))
        if b.strip():
            out.append(b.strip())

    return "\n\n".join(out)[:1024] or ""

def tg_post(sess, method, data, files=None, timeout=60):
    for attempt in range(SEND_TRIES):
        try:
            r = sess.post("https://api.telegram.org/bot%s/%s" % (TOKEN, method),
                          data=data, files=files, timeout=timeout)
            print("telegram %s: %s %s" % (method, r.status_code, r.text[:120]))
            if r.ok:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(3 * (attempt + 1))
                continue
            return None
        except Exception as e:
            print("telegram %s սխալ %s" % (method, type(e).__name__))
            time.sleep(3 * (attempt + 1))
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
                     "reply_markup": json.dumps(markup)})
        return r is not None

    if len(blobs) >= 2:
        files = {"p%d" % i: ("p%d.jpg" % i, b) for i, b in enumerate(blobs)}
        media = [{"type": "photo", "media": "attach://p0", "caption": caption}]
        for i in range(1, len(blobs)):
            media.append({"type": "photo", "media": "attach://p%d" % i})
        r = tg_post(sess, "sendMediaGroup",
                    {"chat_id": CHAT_ID, "media": json.dumps(media, ensure_ascii=False)},
                    files=files, timeout=120)
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
            tg_post(sess, "sendMessage", payload, timeout=30)
            return True
        print("  ալբոմը չանցավ, մեկ նկարով")

    r = tg_post(sess, "sendPhoto",
                {"chat_id": CHAT_ID, "caption": caption,
                 "reply_markup": json.dumps(markup)},
                files={"photo": ("p.jpg", blobs[0])}, timeout=120)
    if r is None:
        r = tg_post(sess, "sendPhoto", {"chat_id": CHAT_ID, "caption": caption},
                    files={"photo": ("p.jpg", blobs[0])}, timeout=120)
    return r is not None

def load():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception as e:
            print("state-igma.json չի կարդացվում:", e)
    return {"queue": [], "published": {}, "seen": {}, "watch": [], "watch_pos": 0}

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
    if len(st.get("watch") or []) > 5000:
        st["watch"] = st["watch"][-3000:]

def publish_one(sess, st, e, now):
    path = e["path"]
    iid = e.get("id") or ""

    d = None
    try:
        d = item_data(sess, path)
    except Exception as ex:
        print("ошибка разбора:", ex)

    if not d:
        if not site_up(sess):
            return "hold"
        st["seen"][path] = True
        return "dropcard"

    prev = st["published"].get(path)
    stamp = d.get("stamp") or ""
    if prev and stamp_of(stamp) <= stamp_of(prev):
        return "skip"

    if not d.get("price") and e.get("price"):
        d["price"] = e["price"]
    if not d.get("photos") and e.get("photo"):
        d["photos"] = [e["photo"]]

    cap = make_caption(sess, d)
    if not cap.strip():
        st["seen"][path] = True
        return "drop"

    if send(sess, d, cap):
        st["published"][path] = stamp or today_str()
        if path not in st["watch"]:
            st["watch"].append(path)
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

def recheck(sess, st, deadline):
    watch = st.get("watch") or []
    if not watch:
        return 0
    pos = int(st.get("watch_pos", 0)) % len(watch)
    picked = [watch[(pos + k) % len(watch)] for k in range(min(WATCH_K, len(watch)))]
    st["watch_pos"] = (pos + len(picked)) % len(watch)

    queued = set(x["path"] for x in st["queue"])
    added = 0
    for path in picked:
        if time.time() > deadline:
            break
        if path in queued:
            continue
        d = None
        try:
            d = item_data(sess, path)
        except Exception:
            d = None
        if not d:
            continue
        stamp = d.get("stamp") or ""
        prev = st["published"].get(path) or ""
        if stamp_of(stamp) > stamp_of(prev):
            st["queue"].append({"id": path.rsplit("/", 1)[-1], "path": path,
                                "date": (d.get("date") or ""),
                                "price": d.get("price", ""),
                                "photo": (d.get("photos") or [""])[0],
                                "cat": ""})
            queued.add(path)
            added += 1
            print("  թարմացվածը հերթում է:", path, stamp)
        time.sleep(0.3)
    print("թարմացումների ստուգում. նորերը՝ %d | հերթ: %d" % (added, len(st["queue"])))
    save(st)
    return added

def main():
    st = load()
    st.setdefault("published", {})
    st.setdefault("seen", {})
    st.setdefault("watch", [])
    st.setdefault("watch_pos", 0)
    norm_queue(st)

    if not st.get(RESET_V):
        st["queue"] = []
        st[RESET_V] = True
        save(st)
        print("հերթը մաքրվեց")

    sess = requests.Session()
    now = time.time()
    deadline = now + MAX_SECONDS

    done = publish_phase(sess, st, now, MAX_POSTS)
    save(st)

    items = get_items(sess, st, deadline)
    print("всего найдено:", len(items))

    in_queue = set(x["path"] for x in st["queue"])
    by_cat = {}
    for path, it in items.items():
        if path in st["seen"] or path in in_queue:
            continue
        if path in st["published"]:
            continue
        by_cat.setdefault(it.get("cat", ""), []).append(it)

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
    print("готово, публикаций за запуск: %d | очередь: %d" % (done, len(st["queue"])))
    return done

if __name__ == "__main__":
    main()
