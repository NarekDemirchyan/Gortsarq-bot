# -*- coding: utf-8 -*-
"""
arenda.py — Անշարժ գույք (gortsarq.am + wse.am → Telegram).
Վարձույթ + Վաճառք նույն խմբում, մեկ հերթով։

Մեկ գործարկում.
 1. Հերթից մեկ գրառում։ Հրապարակում ենք 45 րոպեն մեկ։
 2. Հավաքում՝ gortsarq.am-ի ամբողջ բաժինը (թարմության պատուհանը՝ 3650 օր),
    որ հերթը երբեք դատարկ չմնա։
 3. wse.am-ը որպես երկրորդ աղբյուր. միայն վարձով բնակարանները Երևանում։
    Այս աղբյուրի գրառումները հրապարակվում են մեկ անգամ։
 4. Հայտարարությունը թարմացա՞վ կայքում — քարտի ամսաթիվը փոխվում է,
    ու այն նորից է հերթ մտնում, նորից է հրապարակվում։

Չհրապարակվող հայտարարությունը (առանց նկարի կամ առանց վերնագրի) միանգամից դեն է
նետվում ու հիշվում, որ նորից հերթ չմտնի. բոտը շարունակում է հաջորդով ու չի կանգնում։

Քարտի դաշտերը գալիս են էջի բնութագրերից։ Հեռախոսը գործարքի էջում փակ է,
դրա համար միշտ կցվում է «Связываться» կոճակը։

Նկարները ներբեռնում ենք ինքներս ու ուղարկում ֆայլով. ուղիղ հղումով Telegram-ը
դրանք չի քաշում։ Նկար չստացվեց՝ գրառումը չի հրապարակվում ու դեն է նետվում։
"""
import os
import re
import json
import time
import random

import requests

from fetcher import translate, has_cyr, led_add

try:
    from fetcher import to_md
except ImportError:
    def to_md(html, url=""):
        return html

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID_ARENDA", "")
           or os.environ.get("TELEGRAM_CHAT_ID", ""))

SITE = "arenda"
STATE = "state-arenda.json"

INTERVAL = 45 * 60        # մեկ գրառում 45 րոպեն մեկ
QUEUE_MAX = 2000
FRESH_DAYS = 3650         # ամբողջ բաժինը, որ հերթը դատարկ չմնա
MAX_PAGES = 20
PER_CAT = 2000
MAX_SECONDS = 15 * 60
WSE_SECONDS = 240         # որքան ժամանակ ենք տալիս wse.am-ին
RESET_ONCE = True         # հին (list.am-ի) հերթը մեկ անգամ մաքրվում է
TEST = False

BASE = "https://gortsarq.am"

# ---------- wse.am՝ երկրորդ աղբյուր ----------

WSE_LIST = "https://wse.am/snyat/kvartiru/erevan"
WSE_PAGES = 3             # քանի էջ ենք վերցնում ցանկից
WSE_TAG = "wse"           # այս աղբյուրի գրառումը մեկ անգամ է հրապարակվում
WSE_ITEM_RE = re.compile(r"https://wse\.am/snyat/[^)\]\s\"'<>]+-\d+")
WSE_PHOTO_RE = re.compile(r"https://wse\.am/uploads/[^)\]\s\"'<>]+\.(?:webp|jpe?g|png)", re.I)

# բաժին → տեսակ (rent / sale)
CATS = [("485c-dolgosrochnaya-arenda", "rent"),
        ("487c-prodazha", "sale")]

TYPES = {"490": "Квартира", "501": "Квартира",
         "491": "Дом", "504": "Дом", "492": "Комната",
         "493": "Коммерческое помещение", "502": "Коммерческое помещение",
         "494": "Земельный участок", "505": "Земельный участок",
         "495": "Гараж", "506": "Гараж",
         "496": "Вагон-домик", "503": "Вагон-домик"}

LABELS = {"Количество комнат": "комнат", "Площадь помещения": "площадь",
          "Этаж": "этаж", "Этажей в доме": "этажей",
          "Отделка помещения": "состояние", "Отопление": "отопление",
          "Комфорт": "комфорт", "Бытовая техника": "техника",
          "Можно с питомцами?": "животные",
          "Сдаете семьям с детьми?": "дети",
          "Тип собственности": "кто", "Вариант застройки": "застройка",
          "Площадь участка": "участок",
          "Тип коммерческой недвижимости": "коммерция",
          "Тип земельного участка": "земля"}

HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "ru,hy;q=0.9,en;q=0.8",
}

BLOCKED = ("just a moment", "performing security verification",
           "checking your browser", "enable javascript", "captcha",
           "are you a human")

ITEM_RE = re.compile(r"https://gortsarq\.am/ru/(\d+)p-[^)\]\s\"'>]+")
PHOTO_RE = re.compile(r"https://gortsarq\.am/images/detailed/\d+/[^)\]\s\"'>]+")
DATE_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}")
PRICE_RE = re.compile(r"([\d][\d ,.]*)\s*(֏|\$|€)")
AREA_RE = re.compile(r"(\d{2,4}(?:[.,]\d+)?)\s*"
                     r"(?:քմ|ք\.մ|ք\. մ|кв\.?\s*մ|кв\.?\s*м\.?|м²|m2)", re.I)
FLOOR_RE = re.compile(r"(\d{1,3})\s*/\s*(\d{1,3})\s*(?:հարկ|этаж)", re.I)
ZALOG_RE = re.compile(r"(?:залог|предоплат\w*)[^\n.;]{0,45}", re.I)
KOM_RE = re.compile(r"коммунал\w*[^\n.;]{0,45}", re.I)
MASK_RE = re.compile(r"\(?\s*0?\s*x{2,}[\s\-x]*x{2,}\s*\)?", re.I)

# ---------- ընդհանուր ----------

def today_str():
    return time.strftime("%d.%m.%Y", time.gmtime(time.time() + 4 * 3600))

def last_days(n):
    out = set()
    base = time.time() + 4 * 3600
    for k in range(n + 1):
        out.add(time.strftime("%d.%m.%Y", time.gmtime(base - k * 24 * 3600)))
    return out

def scrub(text):
    return " ".join(MASK_RE.sub(" ", text or "").split())

def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def low1(s):
    s = (s or "").strip()
    return s[:1].lower() + s[1:] if s else s

def list_url(cat, page):
    return f"{BASE}/ru/{cat}/" if page == 1 else f"{BASE}/ru/{cat}/?page={page}"

# ---------- ցանց ----------

def good(text):
    if not text or len(text) < 1500:
        return False
    low = text.lower()
    return not any(b in low for b in BLOCKED)

def fetch(sess, url):
    try:
        r = sess.get(url, headers=HDRS, timeout=25)
        if r.status_code == 200 and good(r.text):
            return to_md(r.text, url)
        print(f" ուղիղ {r.status_code}, {len(r.text)} նշան")
    except Exception as e:
        print(f" ուղիղ սխալ {type(e).__name__}")

    try:
        h = dict(HDRS)
        h["x-engine"] = "browser"
        key = os.environ.get("JINA_KEY", "")
        if key:
            h["Authorization"] = "Bearer " + key
        r = sess.get("https://r.jina.ai/" + url, headers=h, timeout=40)
        if r.status_code == 200 and good(r.text):
            print(" jina")
            return r.text
        print(f" jina {r.status_code}")
    except Exception as e:
        print(f" jina սխալ {type(e).__name__}")

    for p in ("https://api.allorigins.win/raw", "https://api.codetabs.com/v1/proxy"):
        try:
            r = sess.get(p, params={"url": url}, headers=HDRS, timeout=40)
            if r.status_code == 200 and good(r.text):
                print(" proxy")
                return to_md(r.text, url)
        except Exception:
            pass
    print(" չբացվեց:", url)
    return ""

def thumb_urls(u):
    """Մեկ նկարի տարբերակները՝ սկզբում մանրը, վերջում՝ ամբողջականը։"""
    out = []
    if "/images/detailed/" in u:
        tail = u.split("/images/detailed/", 1)[1]
        for size in ("480/480", "800/800", "1024/1024"):
            out.append(f"{BASE}/images/thumbnails/{size}/detailed/{tail}")
    out.append(u)
    return out

def wse_photo_urls(u):
    """wse.am-ի նկարի տարբերակները՝ սկզբում ամբողջականը, վերջում մանրը։"""
    out = []
    if "/uploads/thumbs/" in u:
        out.append(u.replace("/uploads/thumbs/", "/uploads/full/"))
        out.append(u.replace("/uploads/thumbs/", "/uploads/"))
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
        print(f" նկար ուղիղ {r.status_code}, {len(r.content or b'')} բայթ")
    except Exception as e:
        print(f" նկար ուղիղ սխալ {type(e).__name__}")

    for p in ("https://api.allorigins.win/raw", "https://api.codetabs.com/v1/proxy"):
        try:
            r = sess.get(p, params={"url": url}, headers=h, timeout=60)
            if img_ok(r):
                print(" նկար proxy")
                return r.content
            print(f" նկար proxy {r.status_code}")
        except Exception as e:
            print(f" նկար proxy սխալ {type(e).__name__}")

    for p in relay_urls(url):
        try:
            r = sess.get(p, headers=h, timeout=60)
            if img_ok(r):
                print(" նկար փոխանցիչ")
                return r.content
            print(f" նկար փոխանցիչ {r.status_code}")
        except Exception as e:
            print(f" նկար փոխանցիչ սխալ {type(e).__name__}")
    return None

def photo_bytes(sess, url, ref, variants=None):
    """Նկարը ներբեռնում ենք ինքներս։ Ուղիղ հղումով Telegram-ը դա չի անում։"""
    for u in (variants or thumb_urls)(url):
        b = bytes_from(sess, u, ref)
        if b:
            return b
    print(" նկարը չներբեռնվեց:", url)
    return None

# ---------- էջի ընթերցում ----------

def fix_place(p):
    parts = []
    for x in (p or "").split(","):
        x = x.strip()
        if x and x.isupper() and len(x) > 3:
            x = x.title()
        parts.append(x)
    return ", ".join([x for x in parts if x])

def specs(md):
    lines = []
    for l in md.split("\n"):
        l = re.sub(r"^[|*#>\s\-–—]+", "", l.strip())
        l = re.sub(r"[|\s]+$", "", l).strip()
        lines.append(l)
    out = {}
    for i, line in enumerate(lines):
        if not line or len(line) > 45:
            continue
        for lab, key in LABELS.items():
            if key in out:
                continue
            if line == lab or line.startswith(lab + ":"):
                val = line[len(lab):].strip(" :|–—-*")
                if not val:
                    val = next((x for x in lines[i + 1:i + 4] if x), "")
                val = val.strip(" :|–—-*")
                if val and len(val) <= 45 and val not in LABELS:
                    out[key] = val
                break
    # պահուստ՝ եթե էջում պիտակն ու արժեքը մի տողում են
    for lab, key in LABELS.items():
        if key in out:
            continue
        m = re.search(re.escape(lab) + r"\s*:?\s*\n?\s*([^\n|]{1,45})", md)
        if m:
            val = m.group(1).strip(" :|–—-*")
            if val and not any(val.startswith(x) for x in LABELS if x != lab):
                out[key] = val
    return out

def item_data(sess, path, kind):
    md = fetch(sess, BASE + path)
    if not md:
        return None

    cut = md.find("Похожие")
    if cut > 0:
        md = md[:cut]

    v = specs(md)

    photos = []
    for m in PHOTO_RE.finditer(md):
        u = m.group(0).split("]")[0].split(")")[0]
        if u not in photos:
            photos.append(u)

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
    if desc and not has_cyr(desc):
        desc = scrub(translate(sess, desc))

    place = ""
    m = re.search(r"Расположение\s*\n+(.+)", md)
    if m:
        place = fix_place(scrub(" ".join(m.group(1).split()))[:70])
        if place and not has_cyr(place):
            place = fix_place(scrub(translate(sess, place)))

    otype = ""
    for m in re.finditer(r"/(\d{3,4})c-", md):
        if m.group(1) in TYPES:
            otype = TYPES[m.group(1)]
            break

    area = ""
    if v.get("площадь"):
        area = v["площадь"] if "м" in v["площадь"] else v["площадь"] + " м²"
    else:
        m = AREA_RE.search(md)
        if m:
            area = m.group(1).replace(",", ".") + " м²"

    area_land = ""
    if v.get("участок"):
        area_land = v["участок"]

    floor = ""
    m = FLOOR_RE.search(md)
    if m:
        floor = m.group(1) + "/" + m.group(2)
    elif v.get("этаж"):
        floor = re.sub(r"\D", "", v["этаж"]) or ""
        if floor and v.get("этажей"):
            tot = re.sub(r"\D", "", v["этажей"])
            if tot and "/" not in floor:
                floor = floor + "/" + tot

    parents = [p.strip() for p in place.split(",") if p.strip()]
    district = parents[1] if len(parents) >= 2 else ""

    state = ""
    bits = [low1(v.get("состояние", ""))]
    if "мебел" in (v.get("техника", "") + " " + v.get("комфорт", "")).lower():
        bits.append("с мебелью")
    state = ", ".join([x for x in bits if x])

    zalog = kom = ""
    if kind == "rent" and desc:
        m = ZALOG_RE.search(desc)
        if m:
            zalog = scrub(m.group(0))[:45]
        m = KOM_RE.search(desc)
        if m:
            kom = scrub(m.group(0))[:45]

    d = {"title": "", "price": price, "desc": desc, "place": place,
         "district": district, "area": area, "area_land": area_land,
         "floor": floor, "state": state, "zalog": zalog, "kom": kom,
         "specs": v, "otype": otype, "photos": photos[:10],
         "url": BASE + path, "kind": kind}

    print(f" {path}: նկար {len(photos)}, տիպ {otype or '—'}, գին «{price}», "
          f"նկարագրություն {len(desc)}, տեսակ {kind}")
    return d

def headline(d):
    v = d["specs"]
    t = d["otype"] or ""
    m = re.search(r"\d+", v.get("комнат", ""))
    rooms = m.group(0) if m else ""
    if t == "Квартира" and rooms:
        head = rooms + "-комнатная квартира"
    elif t == "Дом" and rooms:
        head = "Дом, " + rooms + " комнат"
    elif t:
        head = t
    else:
        head = "Объявление о недвижимости"
    bits = [head]
    if d["district"]:
        bits.append(d["district"])
    if d["area"]:
        bits.append(d["area"])
    return ", ".join(bits)

def make_caption(sess, d):
    v = d["specs"]
    lines = ["<b>" + esc(headline(d)) + "</b>"]
    lines.append("🔷 " + ("В продаже" if d["kind"] == "sale" else "В аренду"))
    if d["place"]:
        lines.append("📍 Район: " + esc(d["place"]))
    m = re.search(r"\d+", v.get("комнат", ""))
    if m:
        lines.append("🏢 Комнат: " + m.group(0))
    if d["area"]:
        lines.append("📐 Площадь: " + esc(d["area"]))
    if d["area_land"]:
        lines.append("🌿 Участок: " + esc(d["area_land"]))
    if d["floor"]:
        lines.append("🏙 Этаж: " + esc(d["floor"]))
    if v.get("застройка"):
        lines.append("🏗 Застройка: " + esc(low1(v["застройка"])))
    if v.get("коммерция"):
        lines.append("🏬 Назначение: " + esc(low1(v["коммерция"])))
    if d["state"]:
        lines.append("🛋 Состояние: " + esc(d["state"]))
    if v.get("отопление"):
        lines.append("🔥 Отопление: " + esc(low1(v["отопление"])))
    if "балкон" in (v.get("комфорт", "") + " " + v.get("техника", "")).lower():
        lines.append("🏞 Балкон: есть")
    if v.get("дети"):
        lines.append("👫 С детьми: " + esc(low1(v["дети"])))
    if v.get("животные"):
        lines.append("🐶 Животные: " + esc(low1(v["животные"])))
    if d["price"]:
        if d["kind"] == "sale":
            lines.append("💰 Цена: " + esc(d["price"]))
        else:
            lines.append("💰 Цена: " + esc(d["price"]) + " / месяц")
    if d["kind"] == "rent":
        if d["zalog"]:
            lines.append("🔑 Залог: " + esc(d["zalog"]))
        if d["kom"]:
            lines.append("🧾 Коммунальные: " + esc(d["kom"]))
    who = (v.get("кто") or "").lower()
    if who.startswith("собств"):
        lines.append("👤 Собственник")
    elif who.startswith("аген"):
        lines.append("🏢 Агентство")

    cap = "\n".join(lines)
    if d["desc"]:
        cap += "\n\n" + esc(d["desc"][:600])
    return cap[:1024]

# ---------- wse.am ----------

def wse_card(md, start):
    """wse.am-ի քարտի տվյալները հղումից առաջ ընկած հատվածից։
    Գինը քարտում միշտ հասցեից առաջ է, դրա համար հասցեն փնտրում ենք գնից հետո։"""
    pre = md[max(0, start - 900):start]

    photo = ""
    ph = WSE_PHOTO_RE.findall(pre)
    if ph:
        photo = ph[-1]

    price = ""
    tail = pre
    m = None
    for m in re.finditer(r"([\d][\d\s]*)\s*֏", pre):
        pass
    if m:
        price = " ".join(m.group(1).split()) + " ֏"
        tail = pre[m.end():]

    rooms = ""
    m = re.search(r"(\d+)\s*\*{0,2}\s*комн", pre)
    if m:
        rooms = m.group(1)

    area = ""
    m = re.search(r"\*{1,2}(\d{2,4}(?:[.,]\d+)?)\*{1,2}\s*м²", pre)
    if m:
        area = m.group(1).replace(",", ".") + " м²"

    floor = ""
    m = re.search(r"\*{1,2}(\d{1,3})/(\d{1,3})\*{1,2}", pre)
    if m:
        floor = m.group(1) + "/" + m.group(2)

    place = ""
    for m in re.finditer(r"\*{1,2}([^*\n]{4,90})\*{1,2}", tail):
        t = m.group(1).strip()
        if ("," in t and "комн" not in t and "м²" not in t
                and re.search(r"[^\W\d_]", t, re.U)):
            place = t
    place = fix_place(scrub(place))

    parents = [p.strip() for p in place.split(",") if p.strip()]
    district = parents[1] if len(parents) >= 2 else ""

    return {"photo": photo, "price": price, "rooms": rooms, "area": area,
            "floor": floor, "place": place, "district": district}

def wse_collect(sess, deadline):
    """wse.am-ի վարձով բնակարանները Երևանում՝ ցանկի քարտերից։"""
    found = {}
    for page in range(1, WSE_PAGES + 1):
        if time.time() > deadline:
            print(" wse. ժամանակը վերջացավ")
            break
        url = WSE_LIST if page == 1 else WSE_LIST + f"?page={page}"
        md = fetch(sess, url)
        if not md:
            break
        n = 0
        for m in WSE_ITEM_RE.finditer(md):
            link = m.group(0)
            iid = "wse-" + link.rsplit("-", 1)[-1]
            if iid in found:
                continue
            c = wse_card(md, m.start())
            if not c["photo"]:
                continue
            c.update({"id": iid, "src": "wse", "date": WSE_TAG, "kind": "rent",
                      "path": link, "url": link})
            found[iid] = c
            n += 1
        print(f"[wse] էջ {page}: նոր {n}")
        if n == 0:
            break
        time.sleep(1)
    return found

def wse_head(d):
    head = "Квартира"
    if d.get("rooms"):
        head = d["rooms"] + "-комнатная квартира"
    if d.get("district"):
        head += ", " + d["district"]
    return head

def wse_caption(d):
    lines = ["<b>" + esc(wse_head(d)) + "</b>", "🔷 В аренду"]
    if d.get("place"):
        lines.append("📍 Район: " + esc(d["place"]))
    if d.get("rooms"):
        lines.append("🏢 Комнат: " + d["rooms"])
    if d.get("area"):
        lines.append("📐 Площадь: " + esc(d["area"]))
    if d.get("floor"):
        lines.append("🏙 Этаж: " + esc(d["floor"]))
    if d.get("price"):
        lines.append("💰 Цена: " + esc(d["price"]) + " / месяц")
    return "\n".join(lines)[:1024]

def wse_publish(sess, st, e, now):
    url = e.get("url") or e.get("path")
    if st["published"].get(url) == e.get("date", WSE_TAG):
        return "skip"
    blob = photo_bytes(sess, e["photo"], url, wse_photo_urls)
    if not blob:
        print(" wse. նկարը չներբեռնվեց՝", url)
        st["skipped"][url] = e.get("date", WSE_TAG)
        return "drop"
    cap = wse_caption(e)
    markup = {"inline_keyboard": [[{"text": "Смотреть объявление", "url": url}]]}
    if send_photo_bytes(sess, blob, cap, markup):
        st["last_publish"] = now
        st["published"][url] = e.get("date", WSE_TAG)
        led_add(SITE, e.get("id", ""), now)
        print("հրապարակվեց (wse):", url)
        return "ok"
    return "fail"

# ---------- Telegram ----------

def send_photo_bytes(sess, blob, caption, markup):
    """Մեկ նկար ուղարկում ենք ֆայլով։ Չանցավ՝ նույնը ֆայլի տեսքով, որ գրառումը չկորչի։"""
    r = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                  data={"chat_id": CHAT_ID, "caption": caption,
                        "parse_mode": "HTML",
                        "reply_markup": json.dumps(markup)},
                  files={"photo": ("p.jpg", blob)}, timeout=90)
    print(" ուղարկում:", r.status_code, r.text[:200])
    if r.ok:
        return True
    r = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendDocument",
                  data={"chat_id": CHAT_ID, "caption": caption,
                        "parse_mode": "HTML",
                        "reply_markup": json.dumps(markup)},
                  files={"document": ("p.jpg", blob)}, timeout=120)
    print(" ֆայլով:", r.status_code, r.text[:200])
    return bool(r.ok)

def send(sess, d, caption):
    """Նկարները ներբեռնում ենք ինքներս ու ուղարկում ֆայլով. ուղիղ հղումով Telegram-ը
    դրանք չի քաշում։ Նկար չստացվեց՝ չենք հրապարակում, հայտարարությունը կվերադառնա հերթ։"""
    markup = {"inline_keyboard": [[{"text": "Связываться", "url": d["url"]}]]}

    blobs = []
    for u in d["photos"][:10]:
        b = photo_bytes(sess, u, d["url"])
        if b:
            blobs.append(b)

    if not blobs:
        print(" նկարները չներբեռնվեցին, հրապարակում չկա")
        return False

    if len(blobs) >= 2:
        files = {f"p{i}": (f"p{i}.jpg", b) for i, b in enumerate(blobs)}
        media = [{"type": "photo", "media": "attach://p0", "caption": caption,
                  "parse_mode": "HTML"}]
        for i in range(1, len(blobs)):
            media.append({"type": "photo", "media": f"attach://p{i}"})
        r = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendMediaGroup",
                      data={"chat_id": CHAT_ID,
                            "media": json.dumps(media, ensure_ascii=False)},
                      files=files, timeout=120)
        print(" ալբոմ:", r.status_code, r.text[:160])
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
            print(" կոճակ:", r2.status_code, r2.text[:160])
            return bool(r2.ok)
        print(" ալբոմը չանցավ, փորձում ենք մեկ նկարով")

    return send_photo_bytes(sess, blobs[0], caption, markup)

# ---------- վիճակ ----------

def load():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception as e:
            print("state-arenda.json չի կարդացվում:", e)
    return {"queue": [], "last_publish": 0, "published": {}, "failed": {},
            "skipped": {}}

def save(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)

def norm_queue(st):
    q = []
    for e in st.get("queue", []):
        if isinstance(e, dict) and e.get("path"):
            d = dict(e)
            d["id"] = str(d.get("id", ""))
            d["date"] = d.get("date", "")
            d["kind"] = d.get("kind", "rent")
            d["src"] = d.get("src", "gortsarq")
            q.append(d)
    st["queue"] = q

# ---------- հրապարակում ----------

def publish_one(sess, st, e, now):
    if e.get("src") == "wse" or str(e.get("path", "")).startswith("http"):
        return wse_publish(sess, st, e, now)

    path = e["path"]
    if st["published"].get(path) == e.get("date"):
        return "skip"
    d = None
    try:
        d = item_data(sess, path, e.get("kind", "rent"))
    except Exception as ex:
        print("վերլուծության սխալ:", ex)
    if not d:
        print(" էջը չբացվեց՝", path)
        st["skipped"][path] = e.get("date") or today_str()
        return "drop"
    if not d["photos"]:
        print(" առանց նկարի՝", path)
        st["skipped"][path] = e.get("date") or today_str()
        return "drop"
    cap = make_caption(sess, d)
    if not cap:
        print(" առանց վերնագրի՝", path)
        st["skipped"][path] = e.get("date") or today_str()
        return "drop"
    if send(sess, d, cap):
        st["last_publish"] = now
        st["published"][path] = e.get("date") or today_str()
        led_add(SITE, e.get("id", ""), now)
        print("հրապարակվեց:", path, "| նկար", len(d["photos"]))
        return "ok"
    return "fail"

def publish_phase(sess, st, now, limit=1):
    done, tried = 0, 0
    while st["queue"] and done < limit and tried < 5:
        if now - st["last_publish"] < INTERVAL:
            print("հերթական գրառման ժամանակը դեռ չեկավ")
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
    return done

# ---------- հավաքում ----------

def get_items(sess, deadline, seed):
    ok_dates = last_days(FRESH_DAYS)
    found = {}
    for cat, kind in CATS:
        per_cat = 0
        for page in range(1, MAX_PAGES + 1):
            if time.time() > deadline:
                print("հավաք. ժամանակը վերջացավ")
                return found
            md = fetch(sess, list_url(cat, page))
            if not md:
                break
            n, fresh = 0, 0
            for m in ITEM_RE.finditer(md):
                iid = m.group(1)
                path = m.group(0)[len(BASE):].split("]")[0].split(")")[0]
                tail = md[m.end():m.end() + 300]
                dm = DATE_RE.search(tail)
                date = dm.group(0) if dm else ""
                if not seed and date and date not in ok_dates:
                    continue
                if seed or date in ok_dates:
                    fresh += 1
                if iid not in found:
                    found[iid] = {"id": iid, "path": path,
                                  "date": date, "kind": kind}
                    n += 1
            per_cat += n
            print(f"[{cat}] էջ {page}: նոր {n}, թարմ {fresh}")
            if n == 0 or per_cat >= PER_CAT:
                break
            if not seed and fresh == 0 and page > 1:
                break
            time.sleep(1)
return found

# ---------- գլխավոր ----------

def main():
    st = load()
    st.setdefault("failed", {})
    st.setdefault("published", {})
    st.setdefault("skipped", {})
    st.setdefault("last_publish", 0)
    norm_queue(st)
    sess = requests.Session()
    now = time.time()
    deadline = now + MAX_SECONDS

    if RESET_ONCE and st.get("reset_nedv") != 2:
        st["queue"] = []
        st["failed"] = {}
        st["published"] = {}
        st["last_publish"] = 0
        st["reset_nedv"] = 2
        print("մաքրում. հին հերթը դատարկվեց")

    done = publish_phase(sess, st, now, 1)
    save(st)

    if TEST:
        for e in st["queue"][:1]:
            d = item_data(sess, e["path"], e.get("kind", "rent"))
            if d:
                print("նկար:", len(d["photos"]), "| վերնագիր:", headline(d))
        return done

    seed = not st.get("seeded")
    if seed:
        print("առաջին լցնել. վերցնում ենք ամբողջ բաժինը")

    # ամբողջ բաժինը (3650 օր), բայց ժամանակի մի մասը թողնում ենք wse.am-ին
    items = get_items(sess, deadline - WSE_SECONDS, seed)
    print("ընդհանուր գտնված:", len(items))

    qpaths = {e["path"] for e in st["queue"]}
    fresh = []
    for iid, it in items.items():
        if not it["date"]:
            it["date"] = today_str()
        if st["published"].get(it["path"]) == it["date"]:
            continue
        if st["skipped"].get(it["path"]) == it["date"]:
            continue
        if it["path"] in qpaths:
            continue
        fresh.append(it)

    if fresh:
        st["queue"].extend(fresh)
        print("հերթ մտավ:", len(fresh))
    st["seeded"] = True

    # wse.am՝ երկրորդ աղբյուր. միայն վարձով բնակարանները Երևանում
    try:
        w = wse_collect(sess, min(deadline, time.time() + WSE_SECONDS))
        qpaths = {e["path"] for e in st["queue"]}
        new_w = [x for x in w.values()
                 if x["path"] not in qpaths
                 and st["published"].get(x["path"]) != WSE_TAG
                 and st["skipped"].get(x["path"]) != WSE_TAG]
        if new_w:
            st["queue"].extend(new_w)
        print("wse. նոր", len(new_w), "| հերթում", len(st["queue"]))
    except Exception as ex:
        print("wse. սխալ:", ex)

    while len(st["queue"]) > QUEUE_MAX:
        st["queue"].pop(0)
    print("հերթում:", len(st["queue"]))
    save(st)

    random.shuffle(st["queue"])

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
    print(f"պատրաստ. այս գործարկումը՝ {done} | այսօր՝ {n_today}")
    return done

if __name__ == "__main__":
    main()
