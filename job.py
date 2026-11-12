# -*- coding: utf-8 -*-
"""
job.am + jobfinder.am -> Telegram «Աշխատանք Հայաստանում | Գործ կա»
"""
import os, re, sys, json, html, time
import requests

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHANNEL = os.environ.get("JOB_CHANNEL", "")
STATE   = "state-job.json"

BASE    = "https://job.am"
SITEMAP = "https://job.am/sitemap/jobs.xml"
BASE2   = "https://jobfinder.am"

DAYS      = 30
QUEUE_MAX = 200
REFILL_AT = 50
MIN_JF    = 12
STEP      = 8
TRIES_MAX = 3
VER        = 13
SEEN_DAYS  = 7
JF_PAGES   = 4

PAUSE     = 2.5
WAIT_429  = 45
TRIES_429 = 3

BULLET = "🔺"

HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "hy,ru;q=0.9,en;q=0.8",
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
}

ARM = re.compile(r"[\u0531-\u0587]")
LAT = re.compile(r"[A-Za-z]")
SITE_RE = re.compile(r"<loc>\s*(https?://job\.am/hy/job/[^<\s]+?)\s*</loc>"
                     r"\s*<lastmod>\s*(\d{4}-\d{2}-\d{2})", re.I)
DATE_ANY = re.compile(r"(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})")
DAYNUM_RE = re.compile(r"^\d{1,2}[\.\-/]")
PHONE_RE = re.compile(r"(?:\+?374|\b0)[\s\-\(\)0-9]{7,18}")
TEL_RE = re.compile(r"""tel:([+\d][\d\s\-\(\)]{6,20})""", re.I)
JF_LINK = re.compile(r"/jobs/[a-z0-9\-]+/(\d+)", re.I)

FIELDS = ["Աշխատավայր", "Աշխատավարձ", "Դրույք", "Աշխատանքային գրաֆիկ",
          "Աշխատանքային փորձ", "Ոլորտ", "Վերջնաժամկետ"]
HEADS = ["Նկարագրություն", "Պարտականություններ", "Պահանջներ",
         "Առավելություններ", "Նպաստավորություններ", "Հավելյալ նշումներ",
         "Աշխատավայրեր"]
HEAD_ALIASES = {
    "Հիմնական պարտականություններ": "Պարտականություններ",
    "Աշխատանքային պարտականություններ": "Պարտականություններ",
    "Աշխատանքի պարտականություններ": "Պարտականություններ",
    "Գործառույթներ": "Պարտականություններ",
    "Հիմնական պահանջներ": "Պահանջներ",
    "Աշխատանքային պահանջներ": "Պահանջներ",
    "Աշխատանքի պահանջներ": "Պահանջներ",
    "Պարտադիր պահանջներ": "Պահանջներ",
}
JF_HEADS = {"Աշխատանքի նկարագրությունը": "Նկարագրություն",
            "Աշխատանքի նկարագրություն": "Նկարագրություն",
            "Ինչ ենք առաջարկում": "Առավելություններ",
            "Մեր առաջարկը": "Առավելություններ",
            "Առավելություններ": "Առավելություններ",
            "Աշխատանքային պարտականությունները": "Պարտականություններ",
            "Աշխատանքային պարտականություններ": "Պարտականություններ",
            "Պարտականությունները": "Պարտականություններ",
            "Պարտականություններ": "Պարտականություններ",
            "Պահանջվող որակավորումները": "Պահանջներ",
            "Պահանջվող որակավորումներ": "Պահանջներ",
            "Պահանջները": "Պահանջներ",
            "Պահանջներ": "Պահանջներ",
            "Վարձատրություն և պայմաններ": "Հավելյալ նշումներ",
            "Լրացուցիչ տեղեկություններ": "Հավելյալ նշումներ"}
STOP_LINES = ("Ամբողջությամբ կարդալ", "Տեսնել ավելին", "Դիմել", "Կարդալ ավելին")
STOP_WORDS = ("Ընկերության մասին", "Տարածեք հայտարարությունը",
              "Ներբեռնեք", "ներբեռնեք", "անվճար հավելված",
              "Օգտագործման պայմաններ", "օգտագործման պայմաններ",
              "Ծառայությունների մատուցման", "կայքի պայմաններ",
              "Գաղտնիության քաղաքականություն", "Բոլոր իրավունքները",
              "Կայքը պատրաստված է", "Օգտվելով մեր կայքից",
              "Դուք ընդունում եք", "դուք համաձայնում եք",
              "Եթե արդեն գրանցված եք")
FORM = ("Անուն Ազգանուն", "Էլ. հասցե", "Նշեք գործող", "Լրացրեք դաշտերը",
        "Ուղարկել", "Կցել", "Ընտրեք ֆայլ", "Գրանցվել", "Ձեր անունը",
        "Հեռախոս *", "Ծածկագիր", "Դիմել այս")
SENT_END = (".", ":", "!", "?", "։", "»", ";", "…")
JUNK = ("$", "(", ")", "{", "}", "[", "]", "->")
BAD_TITLES = ("Նմանատիպ աշխատանքներ", "Ակտիվ աշխատանքներ",
              "Այլ աշխատանքներ", "Վերջին աշխատանքներ", "Աշխատանքներ")
CAT_LINE = re.compile(r"^[Ա-Ֆա-ֆ\w\s,\-]+,\s*[Ա-Ֆա-ֆ]")
CAT_BAD = ("կայք", "ընդունում", "պայման", "իրավունք", "հավելված", "ծառայություն")
JF_META = ("Աշխատանքի տեսակը", "Վայրը", "Սեռ", "Կրթություն", "Ոլորտ",
           "Նախընտրելի տարիք", "Աշխատավարձ", "Վերջնաժամկետ", "Հեռ")
INTRO_BAD = ("նախընտրությամբ", "պահանջվում է", "պահանջվում են",
             "աշխատավարձ", "հասցե", "աշխատանքային օրեր", "ժամեր")

def get(sess, url):
    for attempt in range(TRIES_429):
        try:
            r = sess.get(url, headers=HDRS, timeout=45)
            if r.status_code == 200 and len(r.text) > 2000:
                return r.text
            if r.status_code == 429:
                print("  պատասխան 429, սպասում", WAIT_429, "վրկ |", url)
                time.sleep(WAIT_429 * (attempt + 1))
                continue
            if r.status_code not in (404, 410):
                print("  պատասխան", r.status_code, url)
            break
        except Exception as e:
            print("  ցանցի սխալ", type(e).__name__, url)
            time.sleep(3)
    print("  չկարդացվեց (429/սխալ), թողնում եմ հաջորդ վազքի |", url)
    return ""

def text_of(h):
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", h)
    h = html.unescape(h)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</(div|p|li|h1|h2|h3|h4|h5|h6|tr|section|ul|ol)>", "\n", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = html.unescape(h).replace("\r", "")
    out = []
    for line in h.split("\n"):
        line = " ".join(line.split()).strip(" `:")
        if line:
            out.append(line)
    return "\n".join(out)

def clean(s):
    s = re.sub(r"\s+", " ", s or "").strip(" `:,;")
    return re.sub(r"[,;։]+$", "", s).strip()

def esc(s):
    return html.escape(s or "", quote=False)

def junk(s):
    return any(ch in s for ch in JUNK)

def boiler(s):
    low = (s or "").lower()
    return any(w.lower() in low for w in STOP_WORDS)

def money(s):
    s = clean(s or "")
    if not re.search(r"\d", s):
        return ""
    return re.sub(r"\.0{3,}$", ".000", s)

def fp_of(text):
    t = " ".join((text or "").lower().split())[:300]
    h = 0
    for ch in t:
        h = (h * 131 + ord(ch)) % 1000000007
    return "fp%d" % h

def expired(dl):
    if not dl:
        return False
    try:
        d, m, y = [int(x) for x in re.split(r"[./\-]", dl)]
        return (y, m, d) < time.gmtime(time.time() + 4 * 3600)[:3]
    except Exception:
        return False

def deadline_of(t):
    m = re.search(r"Վերջնաժամկետ", t)
    if not m:
        return ""
    d = DATE_ANY.search(t[m.end():m.end() + 150])
    if not d:
        return ""
    return "%02d.%02d.%s" % (int(d.group(1)), int(d.group(2)), d.group(3))

def title_of(html_text, tags=("h1",)):
    for tag in tags:
        m = re.search(r"(?is)<%s[^>]*>(.*?)</%s>" % (tag, tag), html_text)
        if m:
            cand = clean(text_of(m.group(1)))
            if cand and cand not in BAD_TITLES:
                return cand
    m = re.search(r'(?is)<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html_text)
    if m:
        cand = clean(html.unescape(m.group(1)))
        if cand and cand not in BAD_TITLES:
            return cand
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html_text)
    if m:
        return clean(text_of(m.group(1)).split("|")[0])
    return ""

def phones_of(html_text, t):
    raw = [m.group(1) for m in TEL_RE.finditer(html_text)]
    raw += [m.group(0) for m in PHONE_RE.finditer(t)]
    res = []
    for p in raw:
        digits = re.sub(r"\D", "", p)
        if len(digits) < 9 or len(digits) > 15:
            continue
        if digits.startswith("374") and len(digits) == 11:
            p = "+374 %s %s %s" % (digits[3:5], digits[5:8], digits[8:])
        elif digits.startswith("0") and len(digits) == 9:
            p = "+374 %s %s %s" % (digits[1:3], digits[3:6], digits[6:])
        else:
            p = "+" + digits
        if p not in res:
            res.append(p)
    return res[:2]

def field(block, label):
    m = re.search(re.escape(label), block)
    if not m:
        return ""
    tail = block[m.end():m.end() + 300]
    vals = []
    for line in tail.split("\n")[:4]:
        line = clean(line)
        if not line:
            continue
        if any(line.startswith(x) for x in FIELDS) or line.startswith("Հեռ"):
            break
        if DATE_ANY.search(line) or DAYNUM_RE.match(line):
            break
        vals.append(line)
        if len(vals) >= 2:
            break
    val = clean(" ".join(vals))[:120]
    if junk(val):
        return ""
    return val

def cats_of(t):
    for line in t.split("\n"):
        low = clean(line)
        if not low or len(low) > 90 or low.count(",") < 1:
            continue
        if not CAT_LINE.match(low):
            continue
        if any(w.lower() in low.lower() for w in CAT_BAD):
            continue
        if re.search(r"\d", low) or "#" in low:
            continue
        tags = []
        for part in low.split(","):
            w = clean(part)
            if not w or len(w) > 22:
                continue
            tag = "#" + w.replace(" ", "_")
            if tag not in tags:
                tags.append(tag)
            if len(tags) >= 3:
                break
        if len(tags) >= 2:
            return tags
    return []

def is_armenian(title, body):
    armenian = len(ARM.findall(title)) * 3 + len(ARM.findall(body))
    latin = len(LAT.findall(body))
    if armenian < 30:
        return False
    return armenian >= latin * 0.6

def join_ok(prev):
    return bool(prev) and not prev.rstrip().endswith(SENT_END)

def add_item(cur, item):
    if junk(item):
        return
    if item[0].islower() and cur[1] and join_ok(cur[1][-1]):
        cur[1][-1] = (cur[1][-1] + " " + item)[:400]
    elif len(item) > 3:
        cur[1].append(item[:400])

def make_caption(d):
    L = ["<b>%s</b>" % esc(d["title"])]
    if d["company"]:
        L.append(esc(d["company"]))
    L.append("")
    for k, v in (("Աշխատավայր", d["place"]), ("Աշխատավարձ", d["salary"]),
                 ("Դրույք", d["type"]), ("Գրաֆիկ", d["sched"]),
                 ("Փորձ", d["exp"]), ("Ոլորտ", d["ind"])):
        if v:
            L.append("• %s՝ %s" % (k, esc(v)))
    for head, items in d["sections"]:
        L.append("")
        L.append("🔹 <b>%s</b>" % esc(head))
        for it in items:
            L.append("%s %s" % (BULLET, esc(it)))
    L.append("")
    if d["deadline"]:
        L.append("⏳ Վերջնաժամկետ՝ %s" % d["deadline"])
    if d["phones"]:
        L.append("📞 Հեռ.՝ %s" % esc(", ".join(d["phones"])))
    if d["tags"]:
        L.append("")
        L.append(" ".join(d["tags"]))
    return "\n".join(L).strip()[:4000]

def parse_am(html_text, path):
    t = text_of(html_text)
    title = title_of(html_text, ("h1", "h2"))

    company = ""
    for m in re.finditer(r'(?is)<a[^>]+href="[^"]*?/hy/company/[^"]*"[^>]*>(.*?)</a>', html_text):
        cand = clean(text_of(m.group(1)))
        if cand:
            company = cand
            break

    start = t.find(title) if title else -1
    stop = t.find("Նկարագրություն")
    if stop < 0:
        stop = (start + len(title)) if start >= 0 else 0
    block = t[:stop] if start < 0 else t[start:stop]

    body_end = len(t)
    for w in STOP_WORDS:
        k = t.find(w, stop + 5)
        if k > stop and k < body_end:
            body_end = k
    body = t[stop:body_end]

    sections, cur = [], None
    for line in body.split("\n"):
        low = clean(line)
        if not low or low in STOP_LINES:
            continue
        if boiler(low):
            break
        if any(f in low for f in FORM):
            break
        bare = low.lstrip("—–-•·🔺 ")
        key = next((h for h in HEADS if bare.startswith(h)), None) \
              or next((k for k in HEAD_ALIASES if bare.startswith(k)), None)
        hit = HEAD_ALIASES.get(key, key) if key else None
        if hit:
            same = next((s for s in sections if s[0] == hit), None)
            if same is not None:
                cur = same
            else:
                cur = [hit, []]
                sections.append(cur)
            rest = clean(bare[len(key):])
            if rest and rest not in ("՝", ":"):
                cur[1].append(rest)
        elif cur is not None:
            item = clean(low.lstrip("—-•·–🔺"))
            if not item or item in STOP_LINES:
                continue
            add_item(cur, item)

    limits = {"Նկարագրություն": 4, "Պարտականություններ": 12, "Պահանջներ": 12,
              "Առավելություններ": 10, "Նպաստավորություններ": 10,
              "Հավելյալ նշումներ": 8, "Աշխատավայրեր": 4}
    clean_sections = []
    for head, items in sections:
        uniq = []
        for it in items:
            if it not in uniq and ARM.search(it) and not boiler(it):
                uniq.append(it)
        uniq = uniq[:limits.get(head, 8)]
        if uniq:
            clean_sections.append((head, uniq))

    if not clean_sections:
        plain = []
        for line in body.split("\n"):
            low = clean(line.lstrip("—-•·–🔺"))
            if not low or low in STOP_LINES or low == title or boiler(low):
                continue
            if any(f in low for f in FORM):
                break
            if len(low) > 25 and ARM.search(low) and low not in plain:
                plain.append(low[:400])
            if len(plain) >= 4:
                break
        if plain:
            clean_sections = [("Նկարագրություն", plain)]

    ind = field(block, "Ոլորտ")

    return {
        "id": path.rstrip("/").split("-")[-1],
        "title": title[:90],
        "company": company[:70],
        "place": field(block, "Աշխատավայր")[:70],
        "salary": money(re.sub(r"^Ամսական\s*", "", field(block, "Աշխատավարձ")))[:60],
        "type": field(block, "Դրույք")[:50],
        "sched": field(block, "Աշխատանքային գրաֆիկ")[:70],
        "exp": field(block, "Աշխատանքային փորձ")[:50],
        "ind": ind[:90],
        "deadline": deadline_of(t),
        "phones": phones_of(html_text, t),
        "sections": clean_sections,
        "tags": cats_of(t),
        "hy": is_armenian(title, body),
        "cat": cat_of(ind),
    }
