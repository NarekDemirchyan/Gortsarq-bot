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
INTERVAL    = 0              # ռիթմը տալիս է գրաֆիկը (run.py)
QUEUE_MAX   = 2000           # առավելագույնը հայտարարություն հերթում
FRESH_DAYS  = 7              # վերցնում ենք վերջին շաբաթվա հայտարարությունները
PER_CAT     = 2000           # բաժնի հղումների առաստաղ
MAX_PAGES   = 20             # պաշտպանիչ՝ ըստ բաժնի էջերի
MAX_SECONDS = 15 * 60        # քանի վայրկյան տալիս ենք հավաքմանը
MAX_TRIES   = 25             # քանի հայտարարություն փորձում ենք մեկ գործարկումով
MAX_POSTS   = 1              # սովորական ռիթմ՝ մեկ հրապարակում մեկ գործարկումով
CATCHUP_AFTER = 90 * 60      # եթե այսքան վայրկյան խումբը լուռ է եղել՝ հասնում ենք
CATCHUP_POSTS = 3            # քանի գրառում թույլ ենք տալիս հասնող գործարկումով
SEND_TRIES  = 3              # քանի անգամ փորձում ենք ուղարկել Telegram
RESET_ONCE  = False          # հերթի միանվագ մաքրում (արդեն արված է)
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
    "https://api.allorigins.win/raw",     # url-ը փոխանցում ենք պարամետրով
    "https://api.codetabs.com/v1/proxy",  # url-ը փոխանցում ենք պարամետրով
    "https://corsproxy.io/?url=",         # url-ը կպցնում ենք
    "https://api.cors.lol/?url=",         # url-ը կպցնում ենք
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

# փակ հեռախոս՝ (0x xxx xxxx) և նմանատիպ «x» շարքեր
MASK_RE    = re.compile(r"\(?\s*0?\s*x{2,}[\s\-x]*x{2,}\s*\)?", re.I)

HEAD_FALLBACK = "Новое объявление"

def today_str():
    return time.strftime("%d.%m.%Y", time.gmtime(time.time() + 4 * 3600))

def scrub(text):
    """Մաքրում ենք փակ հեռախոսները և ավելորդ բացատները։"""
    return " ".join(MASK_RE.sub(" ", text or "").split())

def last_days(n):
    out = set()
    base = time.time() + 4 * 3600
    for k in range(n + 1):
        out.add(time.strftime("%d.%m.%Y", time.gmtime(base - k * 24 * 3600)))
    return out

def list_url(cat, page):
    return f"{BASE}/ru/{cat}/" if page == 1 else f"{BASE}/ru/{cat}/?page={page}"

# ---------- ցանց ----------

def good(text):
    """Էջը պիտանի է՝ ոչ պաշտպանություն, ոչ դատարկ։"""
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
            print(f"  ուղիղ {r.status_code}, {len(r.text)} նշան")
        except Exception as e:
            print(f"  ուղիղ սխալ {type(e).__name__}")

        try:
            h = dict(HDRS)
            h["x-engine"] = "browser"
            if JINA_KEY:
                h["Authorization"] = "Bearer " + JINA_KEY
            r = sess.get("https://r.jina.ai/" + url, headers=h, timeout=45)
            if r.status_code == 200 and good(r.text):
                print("  jina")
                return r.text
            print(f"  jina {r.status_code}, {len(r.text)} նշան")
        except Exception as e:
            print(f"  jina սխալ {type(e).__name__}")

        for p in PROXIES:
            try:
                r = via_proxy(sess, p, url, HDRS, 45)
                if r.status_code == 200 and good(r.text):
                    print("  պրոքսի")
                    return to_md(r.text, url)
            except Exception as e:
                print(f"  պրոքսի սխալ {type(e).__name__}")

        if attempt == 0:
            time.sleep(2)

    print("  ընթերցումը չհաջողվեց:", url)
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
                print("  նկար պրոքսի")
                return r.content
        except Exception as e:
            print(f"  նկար պրոքսի սխալ {type(e).__name__}")

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

# ---------- վերլուծություն ----------

def get_items(sess, deadline):
    """Հղումներն ու քարտի տվյալները՝ ըստ բաժինների։ Պահում ենք գինն ու նկարը որպես պահուստ։"""
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
            print(f"[{cat}] էջ {page}: նոր {n}, թարմ {fresh}")
            if n == 0 or per_cat >= PER_CAT:
                break
            if fresh == 0 and page > 1:
                break                  # դրանից հետո միայն հինն է
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

    place =…
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
        txt = union_read(sess, url)
        if not txt:
            continue
        for m in UNION_NICK_RE.finditer(txt):
            n = m.group(1)
            if n.lower() not in NICK_STOP:
                return n
    return ""

def union_photo(sess, u):
    """Նկարը։ Առաջին հերթին փոխանցիչով (այնտեղից գալիս է jpg), հետո ինքը կայքից։"""
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
    parts.append("https://unionapp.net/en/ad/" + str(e.get("id")))
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
    print("հրապարակված union: %s @%s" % (e.get("id"), e.get("nick") or "-"))
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
