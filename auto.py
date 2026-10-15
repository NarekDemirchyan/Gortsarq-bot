# -*- coding: utf-8 -*-
import os, re, json, time, requests

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID_AUTO", "")

STATE      = "state_auto.json"
INTERVAL   = 12 * 60      # минимальный промежуток между публикациями (сек)
QUEUE_MAX  = 2000         # максимум объявлений в очереди
FRESH_DAYS = 7            # берём объявления за последние 7 суток
RESET_ONCE = True         # однократный сброс очереди, сработает один раз
TEST       = False

JINA = "https://r.jina.ai/"
BASE = "https://auto.am"
SITEMAPS = [f"{BASE}/sitemaps/offers-{i}.xml" for i in range(1, 11)]
HDRS = {"User-Agent": "curl/8.5.0"}

# для картинок — обычный браузерный заголовок, иначе сайт отдаёт заглушку
IMG_HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "ru,hy;q=0.9,en;q=0.8",
}

DATE_RE = re.compile(r"<lastmod>(\d{4}-\d{2}-\d{2})")
BLOCKED = ("just a moment", "attention required", "enable javascript",
           "checking your browser", "cloudflare")

# один снимок в трёх размерах: полный (без приставки), m- и s-
PHOTO_RE = re.compile(
    r"https://auto\.am/static/offers/(\d+)/(?:[ms]-)?([0-9a-f]{8,})\.(webp|jpg|jpeg|png)",
    re.I)

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

def fetch_sitemap(sess, url):
    try:
        r = sess.get(url, headers=HDRS, timeout=60)
        if r.status_code == 200 and "<urlset" in r.text:
            return r.text
    except Exception as e:
        print("  sitemap ошибка:", type(e).__name__)
    return jina(sess, url)

def today_iso():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 4 * 3600))

def last_days(n):
    # даты сайта (ГГГГ-ММ-ДД) за последние n суток по Еревану (UTC+4)
    out = set()
    base = time.time() + 4 * 3600
    for k in range(n + 1):
        out.add(time.strftime("%Y-%m-%d", time.gmtime(base - k * 24 * 3600)))
    return out

def translate(sess, text):
    text = (text or "").strip()
    if not text:
        return text
    try:
        r = sess.get("https://translate.googleapis.com/translate_a/single",
                     params={"client": "gtx", "sl": "auto", "tl": "ru",
                             "dt": "t", "q": text[:2000]}, timeout=30)
        res = "".join(p[0] for p in r.json()[0] if p and p[0]).strip()
        if res:
            return res
    except Exception as e:
        print("  перевод недоступен:", type(e).__name__)
    return text

def has_cyr(s):
    return bool(re.search(r"[А-Яа-яЁё]", s or ""))

def get_ids(sess):
    """Список (id, дата последнего изменения) из sitemap."""
    items = []
    seen = set()
    for sm in SITEMAPS:
        xml = fetch_sitemap(sess, sm)
        for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
            m = re.search(r"auto\.am/(?:ru/|en/)?offer/(\d+)", block)
            if not m:
                continue
            iid = m.group(1)
            if iid in seen:
                continue
            seen.add(iid)
            d = DATE_RE.search(block)
            items.append((iid, d.group(1) if d else ""))
        print(f"  {sm.rsplit('/', 1)[-1]}: всего {len(items)} объявлений")
        time.sleep(5)
    return items

# ---------- фотографии ----------

def photo_urls(u):
    """Варианты одного снимка: сначала полный, потом m-, потом s-."""
    m = PHOTO_RE.search(u or "")
    if not m:
        return [u]
    base = f"https://auto.am/static/offers/{m.group(1)}/"
    h = m.group(2).lower()
    ext = m.group(3).lower()
    return [f"{base}{h}.{ext}", f"{base}m-{h}.{ext}", f"{base}s-{h}.{ext}"]

def collect_photos(md, iid):
    out, seen = [], set()
    for m in PHOTO_RE.finditer(md):
        h = m.group(2).lower()
        if h in seen:
            continue
        seen.add(h)
        out.append(f"https://auto.am/static/offers/{m.group(1)}/{h}.{m.group(3).lower()}")
    return out[:10]

def img_ok(r):
    b = r.content or b""
    if r.status_code != 200 or len(b) < 1500:
        return False
    return b.lstrip()[:1] != b"<"

def bytes_from(sess, url, ref):
    h = dict(IMG_HDRS)
    h["Referer"] = ref or BASE + "/"
    h["Accept"] = "image/avif,image/webp,image/jpeg,image/*,*/*;q=0.8"
    try:
        r = sess.get(url, headers=h, timeout=40)
        if img_ok(r):
            return r.content
        print(f"  фото напрямую {r.status_code}, {len(r.content or b'')} байт")
    except Exception as e:
        print("  фото напрямую ошибка:", type(e).__name__)
    return None

def photo_bytes(sess, url, ref):
    """Скачиваем сами: сначала полный размер, потом мельче. Через прокси — если напрямую не вышло."""
    for u in photo_urls(url):
        b = bytes_from(sess, u, ref)
        if b:
            return b
        bare = u.split("://", 1)[-1]
        for relay in (f"https://images.weserv.nl/?url={bare}",
                      f"https://wsrv.nl/?url={bare}"):
            try:
                r = sess.get(relay, headers=IMG_HDRS, timeout=60)
                if img_ok(r):
                    return r.content
            except Exception:
                pass
    print("  фото не скачалось:", url)
    return None

def field(md, name):
    m = re.search(rf"^{re.escape(name)}\s+([^\n\"]+?)(?:\s*\"|$)", md, re.M)
    return m.group(1).strip() if m else ""

def item_data(sess, iid):
    md = jina(sess, f"{BASE}/ru/offer/{iid}")
    cut = md.find("Подчеркнутое объявление")
    if cut > 0:
        md = md[:cut]

    title = ""
    m = re.search(r"^Title:\s*(.+?)(?:\s*\|\s*Auto\.am)?\s*$", md, re.M)
    if m:
        title = m.group(1).strip()

    usd = re.search(r"([\d][\d\s]*)\s*\$", md)
    amd = re.search(r"([\d][\d\s]*)\s*֏", md)
    price = " · ".join(x for x in [
        f"{usd.group(1).strip()} $" if usd else "",
        f"{amd.group(1).strip()} ֏" if amd else ""] if x)

    specs = " · ".join(x for x in (
        ("Пробег " + field(md, "Пробег")) if field(md, "Пробег") else "",
        field(md, "Тип кузова"),
        field(md, "Двигатель"),
        field(md, "Коробка передач"),
        ("руль " + field(md, "Руль").lower()) if field(md, "Руль") else "",
        field(md, "Цвет"),
    ) if x)

    where = ""
    m = re.search(r"(\d{2}\.\d{2}\.\d{4})\s+([А-ЯЁ][^\n]{2,60})", md)
    if m:
        where = f"{m.group(1)} · {m.group(2).strip()}"

    desc = ""
    m = re.search(r"Дополнительно\s*\n+(.+?)(?:\n\s*(?:Продавец|Похожие)\b|\Z)",
                  md, re.S)
    if m:
        parts, seen = [], set()
        for p in m.group(1).split("\n"):
            p = " ".join(p.split())
            if p and p not in seen:
                seen.add(p)
                parts.append(p)
        desc = " ".join(parts)[:900]

    phone = ""
    m = re.search(r"tel:(\+?\d+)", md)
    if m:
        phone = "+" + re.sub(r"\D", "", m.group(1))
        if phone.startswith("+374") and len(phone) == 12:
            phone = f"{phone[:4]} {phone[4:6]} {phone[6:]}"

    photos = collect_photos(md, iid)
    print(f"  {iid}: фото {len(photos)}, цена «{price}», тел {phone or '—'}")

    return {"title": title, "price": price, "specs": specs, "where": where,
            "desc": desc, "phone": phone, "photos": photos,
            "url": f"{BASE}/ru/offer/{iid}"}

def make_caption(sess, d):
    lines = []
    if d["title"]:
        lines.append(d["title"])
    if d["price"]:
        lines.append(d["price"])
    if d["specs"]:
        lines.append(d["specs"])
    if d["where"]:
        lines.append(d["where"])
    desc = d["desc"]
    if desc and not has_cyr(desc):
        desc = translate(sess, desc)
    if desc:
        lines.append(desc)
    if d["phone"]:
        lines.append("Телефон: " + d["phone"])
    return "\n\n".join(x for x in lines if x).strip()[:1024]

# ---------- Telegram ----------

def send_url(sess, urls, caption):
    """Запасной путь: отдаём ссылки Telegram-у, если скачать не получилось."""
    if len(urls) >= 2:
        media = [{"type": "photo", "media": urls[0], "caption": caption}]
        for p in urls[1:10]:
            media.append({"type": "photo", "media": p})
        r = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendMediaGroup",
                      data={"chat_id": CHAT_ID,
                            "media": json.dumps(media, ensure_ascii=False)},
                      timeout=90)
        print("telegram album (ссылки):", r.status_code, r.text[:200])
        if r.ok:
            return True
    if urls:
        r = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                      data={"chat_id": CHAT_ID, "photo": urls[0],
                            "caption": caption}, timeout=60)
    else:
        r = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": caption}, timeout=40)
    print("telegram (ссылки):", r.status_code, r.text[:200])
    return r.ok

def send(sess, d, caption):
    """Скачиваем снимки сами и отправляем файлом — так качество полное."""
    blobs = []
    for u in d["photos"][:10]:
        b = photo_bytes(sess, u, d["url"])
        if b:
            blobs.append(b)

    if blobs:
        if len(blobs) >= 2:
            files = {f"p{i}": (f"p{i}.jpg", b) for i, b in enumerate(blobs)}
            media = [{"type": "photo", "media": "attach://p0", "caption": caption}]
            for i in range(1, len(blobs)):
                media.append({"type": "photo", "media": f"attach://p{i}"})
            r = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendMediaGroup",
                          data={"chat_id": CHAT_ID,
                                "media": json.dumps(media, ensure_ascii=False)},
                          files=files, timeout=180)
            print("telegram album:", r.status_code, r.text[:200])
            if r.ok:
                return True
            print("  альбом не прошёл, пробуем одним снимком")
        r = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                      data={"chat_id": CHAT_ID, "caption": caption},
                      files={"photo": ("p.jpg", blobs[0])}, timeout=120)
        print("telegram фото:", r.status_code, r.text[:200])
        if r.ok:
            return True
        r = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendDocument",
                      data={"chat_id": CHAT_ID, "caption": caption},
                      files={"document": ("p.jpg", blobs[0])}, timeout=120)
        print("telegram файлом:", r.status_code, r.text[:200])
        return bool(r.ok)

    print("  снимки не скачались, пробуем ссылками")
    cand = [photo_urls(u) for u in d["photos"][:10]]
    for variant in (0, 1):
        urls = [c[variant] for c in cand if len(c) > variant]
        if urls and send_url(sess, urls, caption):
            return True
    return send_url(sess, [], caption)

def load():
    if os.path.exists(STATE):
        return json.load(open(STATE, encoding="utf-8"))
    return {"seen": {}, "queue": [], "last_publish": 0,
            "published": {}, "failed": {}}

def save(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)

def main():
    st = load()
    st.setdefault("seen", {})
    if isinstance(st["seen"], list):          # старый формат — просто список номеров
        st["seen"] = {i: "" for i in st["seen"]}
    if st.get("seen_ver", 1) < 2:
        # снимаем метку, которую поставила прошлая версия: дата «сегодня»
        # у неизвестных объявлений означала «дата не менялась» и глушила обновления
        n = sum(1 for v in st["seen"].values() if v == today_iso())
        st["seen"] = {k: ("" if v == today_iso() else v)
                      for k, v in st["seen"].items()}
        st["seen_ver"] = 2
        print("снята метка старого формата:", n)
    st.setdefault("queue", [])
    st.setdefault("failed", {})
    st.setdefault("published", {})
    st.setdefault("last_publish", 0)
    sess = requests.Session()

    if RESET_ONCE and not st.get("reset_done"):
        st["reset_done"] = True
        print("первый запуск новой версии")

    items = get_ids(sess)
    print("всего объявлений в sitemap:", len(items))

    if TEST:
        if items:
            d = item_data(sess, items[0][0])
            send(sess, d, make_caption(sess, d))
        return

    ok_dates = last_days(FRESH_DAYS)
    in_queue = set(st["queue"])
    fresh = []
    for iid, date in items:
        if date and date not in ok_dates:
            continue                   # старше 7 суток — не берём вовсе
        prev = st["seen"].get(iid, "")
        if prev and (not date or date <= prev):
            continue                   # уже знаем и дата не обновилась
        st["seen"][iid] = date or prev or today_iso()
        if iid in in_queue:
            continue
        fresh.append(iid)

    if len(st["seen"]) > 20000:
        keep = list(st["seen"])[-20000:]
        st["seen"] = {k: st["seen"][k] for k in keep}

    if fresh:
        st["queue"].extend(fresh)
        print("новых и обновлённых в очередь:", len(fresh))
    while len(st["queue"]) > QUEUE_MAX:
        st["queue"].pop(0)

    now = time.time()
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
                ok = bool(cap) and send(sess, d, cap)
            except Exception as e:
                print("ошибка публикации:", e)
            if ok:
                st["last_publish"] = now
                st["published"][iid] = now
                print("опубликовано:", iid)
                break
            st["failed"][iid] = st["failed"].get(iid, 0) + 1
            if st["failed"][iid] < 2:
                st["queue"].append(iid)
                print("  вернём в конец очереди:", iid)
            else:
                print("  брошен", iid)

    if len(st["published"]) > 50000:
        keys = list(st["published"])[-20000:]
        st["published"] = {k: st["published"][k] for k in keys}

    save(st)

main()
