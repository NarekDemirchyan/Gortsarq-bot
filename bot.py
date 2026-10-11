# -*- coding: utf-8 -*-
import os
import re
import json
import time
import requests

TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
JINA_KEY = os.environ.get("JINA_KEY", "")

STATE      = "state.json"
INTERVAL = 10 * 60
QUEUE_MAX  = 250          # максимум объявлений в очереди
RESET_ONCE = True         # однократный сброс очереди, сработает один раз
RESET_SEED = 40           # сколько свежих объявлений взять из каждого раздела
TEST       = False

JINA = "https://r.jina.ai/"
BASE = "https://www.list.am"
HDRS = {"User-Agent": "curl/8.5.0"}
if JINA_KEY:
    HDRS["Authorization"] = "Bearer " + JINA_KEY

SECTIONS = {
    "4":   "Электроника",
    "133": "Дом и сад",
    "27":  "Детский мир",
    "393": "Красота и здоровье",
    "39":  "Хобби и спорт",
    "17":  "Мода и стиль",
}

SEED = {"393": 20, "39": 20, "17": 20}

PHOTO_RE = re.compile(
    r"(https?:)?//img\.list\.am/([a-z]+)/\d+/(\d+)\.(?:webp|jpg|jpeg|png)",
    re.I)

BLOCKED = ("just a moment", "attention required", "enable javascript",
           "checking your browser", "cloudflare")

def is_blocked(text):
    t = (text or "").strip()
    return len(t) < 800 or any(m in t.lower() for m in BLOCKED)

def jina(sess, url, extra=None):
    h = dict(HDRS)
    if extra:
        h.update(extra)
    for attempt in range(4):
        try:
            r = sess.get(JINA + url, headers=h, timeout=60)
            if r.status_code == 200 and not is_blocked(r.text):
                return r.text
            print(f"  jina {r.status_code} / защита, попытка {attempt + 1}")
        except Exception as e:
            print(f"  jina ошибка: {type(e).__name__}: {e}")
        time.sleep(10 * (attempt + 1))
    return ""

def collect_photos(text):
    out = {}
    for m in PHOTO_RE.finditer(text):
        kind, pid, url = m.group(2).lower(), m.group(3), m.group(0)
        if url.startswith("//"):
            url = "https:" + url
        if pid not in out or kind in ("f", "l"):
            out[pid] = url
    return list(out.values())[:10]

def translate(sess, text):
    text = (text or "").strip()
    if not text:
        return text
    try:
        r = sess.get("https://translate.googleapis.com/translate_a/single",
                     params={"client": "gtx", "sl": "auto", "tl": "ru",
                             "dt": "t", "q": text[:2000]}, timeout=30)
        data = r.json()
        res = "".join(p[0] for p in data[0] if p and p[0]).strip()
        if res:
            return res
    except Exception as e:
        print("  перевод недоступен:", type(e).__name__)
    return text

def has_cyr(s):
    return bool(re.search(r"[А-Яа-яЁё]", s or ""))

def latin_junk(t):
    t = (t or "").strip()
    if not t or has_cyr(t):
        return False
    return len(t.split()) > 2

def headline(sess, d):
    t = translate(sess, d["title"]).strip()
    if t and not latin_junk(t):
        return t
    alt = translate(sess, d["desc"][:300]).strip()
    if has_cyr(alt) and not alt.rstrip().endswith(".") and len(alt.split()) >= 3:
        return re.split(r"[.!?]\s", alt)[0].strip()[:90]
    return ""

def get_ids(sess):
    by_cat = {}
    for cat in SECTIONS:
        md = jina(sess, f"{BASE}/ru/category/{cat}")
        found = re.findall(r"/ru/item/(\d+)", md)
        uniq = []
        for i in found:
            if i not in uniq:
                uniq.append(i)
        by_cat[cat] = uniq
        print(f"[{cat}] {SECTIONS[cat]}: {len(uniq)} объявлений")
        time.sleep(8)
    return by_cat

def item_data(sess, iid):
    md = jina(sess, f"{BASE}/ru/item/{iid}")
    cut = md.find("Похожие объявления")
    if cut > 0:
        md = md[:cut]

    html = jina(sess, f"{BASE}/ru/item/{iid}", {"x-respond-with": "html"})
    cut2 = html.find("Похожие")
    if cut2 > 0:
        html = html[:cut2]

    title = ""
    m = re.search(r"^#\s+(.+)$", md, re.M)
    if m:
        title = m.group(1).strip()

    price = ""
    m = re.search(r"([\d][\d\s,]*)\s*֏", md)
    if m:
        price = m.group(1).strip() + " ֏"

    desc = ""
    m = re.search(r"Описание\s*\n+(.+?)\n\s*Номер объявления", md, re.S)
    if m:
        desc = " ".join(m.group(1).split())

    place = ""
    m = re.search(r"\[([^\]]*›[^\]]*)\]", md)
    if m:
        place = m.group(1).strip()

    photos = collect_photos(md + "\n" + html)
    print(f"  {iid}: фото {len(photos)}, md {len(md)}, html {len(html)}")

    return {"title": title, "price": price, "desc": desc,
            "place": place, "photos": photos,
            "url": f"{BASE}/ru/item/{iid}"}

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
        lines.append(translate(sess, d["desc"][:700]))
    return "\n\n".join(x for x in lines if x)[:1024]

def send(sess, d, iid, caption):
    url = d["url"]
    markup = {"inline_keyboard": [[{"text": "Связаться", "url": url}]]}
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
            return r1.ok

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
        return r.ok

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
    print("telegram:", r.status_code, r.text[:300])
    return r.ok

def load():
    if os.path.exists(STATE):
        return json.load(open(STATE, encoding="utf-8"))
    return {"seen": [], "queue": [], "last_publish": 0, "seeded": [],
            "published": {}, "failed": {}}

def save(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)

def main():
    st = load()
    st.setdefault("seen", [])
    st.setdefault("queue", [])
    st.setdefault("seeded", [])
    st.setdefault("published", {})
    st.setdefault("failed", {})
    st.setdefault("last_publish", 0)
    sess = requests.Session()
    now = time.time()

    first = False
    if RESET_ONCE and not st.get("reset_done"):
        st["queue"] = []
        st["seen"] = []
        st["failed"] = {}
        st["reset_done"] = True
        first = True
        print("очистка: очередь и список просмотренных сброшены")

    by_cat = get_ids(sess)
    total = sum(len(v) for v in by_cat.values())
    print("всего ссылок:", total)

    if TEST:
        flat = [i for v in by_cat.values() for i in v]
        if flat:
            d = item_data(sess, flat[0])
            print("фото найдено:", len(d["photos"]))
            send(sess, d, flat[0], make_caption(sess, d))
        return

    for cat in SECTIONS:
        cat_ids = by_cat.get(cat, [])
        if cat not in st["seeded"]:
            st["seeded"].append(cat)
            n = SEED.get(cat, 0)
            picked = 0
            for iid in cat_ids:
                if iid not in st["seen"]:
                    st["seen"].append(iid)
                if picked < n and iid not in st["queue"] and iid not in st["published"]:
                    st["queue"].append(iid)
                    picked += 1
            print(f"новый раздел [{cat}] {SECTIONS[cat]}: в очередь {picked}")
            continue

        taken = 0
        for iid in cat_ids:
            if iid in st["seen"]:
                continue
            st["seen"].append(iid)
            if iid in st["published"]:
                continue
            if first and taken >= RESET_SEED:
                continue      # при очистке берём только свежие сверху каждого раздела
            st["queue"].append(iid)
            taken += 1

    st["seen"] = st["seen"][-100000:]

    # лишнее уходит с начала очереди, свежие остаются в конце
    while len(st["queue"]) > QUEUE_MAX:
        st["queue"].pop(0)

    print("очередь:", len(st["queue"]))

    if st["queue"] and now - st["last_publish"] >= INTERVAL:
        for _ in range(3):
            if not st["queue"]:
                break
            iid = st["queue"].pop(0)
            ok = False
            try:
                d = item_data(sess, iid)
                cap = make_caption(sess, d)
                ok = bool(cap) and bool(d["photos"]) and send(sess, d, iid, cap)
            except Exception as e:
                print("ошибка публикации:", e)
                ok = False

            if ok:
                st["last_publish"] = now
                st["published"][iid] = now
                print(f"опубликовано: {iid}")
                break

            st["failed"][iid] = st["failed"].get(iid, 0) + 1
            if st["failed"][iid] < 2 and iid not in st["published"]:
                st["queue"].append(iid)
                print(f"  вернём в конец очереди: {iid}")
            else:
                print(f"  брошен {iid}: не удалось 2 раза")

    if len(st["published"]) > 50000:
        keys = list(st["published"])[-20000:]
        st["published"] = {k: st["published"][k] for k in keys}

    save(st)

# единственное изменение: translate берём из fetcher (с резервными путями)
from fetcher import jina, translate

main()
