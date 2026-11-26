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
ONLY_PRIVATE = True       # публиковать только объявления частных продавцов

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
    low = text[:4000].lower()
    return any(b in low for b in BLOCKED)


def jina(sess, url, tries=4):
    for attempt in range(1, tries + 1):
        try:
            r = sess.get(JINA + url, timeout=90, headers=HDRS)
            if r.status_code == 200 and not is_blocked(r.text):
                return r.text
            if r.status_code in (429, 451, 403):
                print(f"  jina {r.status_code} / защита, попытка {attempt}")
            else:
                print(f"  jina {r.status_code}, попытка {attempt}")
        except Exception as e:
            print(f"  jina ошибка: {e}, попытка {attempt}")
        time.sleep(3 * attempt)
    return ""


def collect_photos(md, iid):
    """Собираем уникальные ключи картинок для одного объявления."""
    found, seen = [], set()
    for m in PHOTO_RE.finditer(md):
        if m.group(1) != str(iid):
            continue
        key = m.group(2).lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(key)
    return [f"{BASE}/static/offers/{iid}/{k}.webp" for k in found]


def field(md, name):
    m = re.search(rf"{name}\s*[:\-]?\s*([^\n|]+)", md, re.I)
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

    # дилер или частник: у дилера логотип лежит в /static/dealers/,
    # у частного аккаунта — в /static/account/; плюс метка «Дилер» и dealer:"y"
    is_dealer = ("/static/dealers/" in md
                 or re.search(r"sc-badge[^>]*>[\s\S]{0,400}?Дилер", md) is not None
                 or re.search(r'"dealer"\s*:\s*"y"', md) is not None)

    print(f"  {iid}: фото {len(photos)}, цена «{price}», тел {phone or '—'}, "
          f"продавец {'дилер' if is_dealer else 'частный'}")

    return {"title": title, "price": price, "specs": specs, "where": where,
            "desc": desc, "phone": phone, "photos": photos,
            "is_dealer": is_dealer,
            "url": f"{BASE}/ru/offer/{iid}"}
