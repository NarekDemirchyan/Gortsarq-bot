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

TZ_OFFSET  = 4          # Երևան (UTC+4)
HOUR_FROM  = 8          # հրապարակումը սկսվում է 08:00
HOUR_TO    = 23         # մինչև 23:59
DAY_HOURS  = HOUR_TO - HOUR_FROM + 1

DAYS      = 30
QUEUE_MAX = 200
REFILL_AT = 50
MIN_JF    = 12
STEP      = 8
TRIES_MAX = 3
VER        = 18
SEEN_DAYS  = 7
JF_PAGES   = 12

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
JF_META = ("Աշխատանքի տեսակը", "Վայրը", "Սեռ", "Կրթություն", "Ոլորտ",
           "Նախընտրելի տարիք", "Աշխատավարձ", "Վերջնաժամկետ", "Հեռ",
           "Վերջնաժամկետը", "Հեռ.")
INTRO_BAD = ("նախընտրությամբ", "պահանջվում է", "պահանջվում են",
             "աշխատավարձ՝", "հասցե՝", "աշխատանքային օրեր՝")
TAG_STOP = ("որ", "և", "կամ", "մեջ", "համար", "մասին", "հետ", "առանց",
            "աշխատանք", "աշխատող", "մասնագետ", "աշխատակից", "ընկերություն",
            "հայաստան", "երևան", "մարզ", "քաղաք", "թաղամաս", "պաշտոն",
            "պահանջվում", "հրավիրվում")
PLACE_LINE = re.compile(r"^[\u0531-\u0587\s\-]+,\s*[\u0531-\u0587\s\-]+$")

def local_now():
    return time.gmtime(time.time() + TZ_OFFSET * 3600)

def publish_window():
    h = local_now().tm_hour
    return HOUR_FROM <= h <= HOUR_TO

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
    s = re.sub(r"\s+", " ", s or "").strip(" `:,;»\"'")
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
        return (y, m, d) < local_now()[:3]
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

def tags_of(title, ind=""):
    tags = []
    src = re.split(r"[|/(–—-]", clean(title or ""))[0]
    for w in re.split(r"[\s,]+", src):
        w = clean(w)
        if len(w) < 4 or len(w) > 22:
            continue
        if not ARM.search(w):
            continue
        if w.lower() in TAG_STOP:
            continue
        t = "#" + w.replace(" ", "_")
        if t not in tags:
            tags.append(t)
        if len(tags) >= 2:
            break
    for part in re.split(r"[,;/]", clean(ind or "")):
        w = clean(part)
        if len(w) < 5 or len(w) > 22 or not ARM.search(w):
            continue
        if w.lower() in TAG_STOP:
            continue
        t = "#" + w.replace(" ", "_")
        if t not in tags:
            tags.append(t)
        if len(tags) >= 3:
            break
    return tags[:3]

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
    meta = []
    for k, v in (("Աշխատավայր", d["place"]), ("Աշխատավարձ", d["salary"]),
                 ("Դրույք", d["type"]), ("Գրաֆիկ", d["sched"]),
                 ("Փորձ", d["exp"]), ("Ոլորտ", d["ind"])):
        if v:
            meta.append("• %s՝ %s" % (k, esc(v)))
    if meta:
        L.append("")
        L.extend(meta)
    for head, items in d["sections"]:
        if not items:
            continue
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
    out = "\n".join(L).strip()
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out[:4000]

def cat_of(ind):
    return clean(re.split(r"[,;/]", ind)[0]) if ind else "այլ"

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
        "tags": tags_of(title, ind),
        "hy": is_armenian(title, body),
        "cat": cat_of(ind),
    }

def jf_sections(t, title="", company=""):
    lines = [clean(x) for x in t.split("\n")]
    lines = [x for x in lines if x]

    meta = [clean(title).lower(), clean(company).lower()]
    sections, cur, started, intro = [], None, False, []

    for line in lines:
        low = line.lower()
        core = re.sub(r"\s+", " ", low).strip(" «»\"'")
        if "jobfinder" in low:
            continue
        if core in meta:
            continue
        if any(m and (core.startswith(m) or m.startswith(core)) and len(core) > 6
               for m in meta if m):
            continue
        if PLACE_LINE.match(line):
            continue
        if low in [x.lower() for x in JF_META]:
            continue
        if line in STOP_LINES or any(f in line for f in FORM):
            continue
        if "ստեղծել" in low or "ռեզյումե" in low or "տեղադրել հայտարարություն" in low:
            continue

        head = JF_HEADS.get(line)
        rest0 = ""
        if head is None:
            for k, v in JF_HEADS.items():
                if line.startswith(k) and len(line) - len(k) < 60:
                    head = v
                    rest0 = clean(line[len(k):])
                    break

        if head:
            if not started and intro:
                first = [x for x in intro if ARM.search(x) and not boiler(x)]
                if first:
                    sections.append(("Նկարագրություն", first[:5]))
            started = True
            cur = [head, []]
            sections.append(cur)
            if rest0:
                add_item(cur, rest0.lstrip("`:\u0589 "))
            continue

        if started and (line.startswith("Դիմել") or line.startswith("Ընկերության մասին")):
            break
        if started and boiler(line):
            break

        if not started:
            if any(line.startswith(x) for x in JF_META):
                continue
            if re.search(r"\d{2}[./]\d{2}[./]\d{4}", line):
                continue
            if any(low.startswith(b) for b in INTRO_BAD):
                continue
            if len(intro) < 14 and line not in intro:
                intro.append(line)
            continue

        if cur is None:
            continue
        it = clean(line.lstrip("-–•—🔺 "))
        if not it or not ARM.search(it) or len(it) < 3:
            continue
        add_item(cur, it)

    limits = {"Նկարագրություն": 6, "Առավելություններ": 10,
              "Պարտականություններ": 12, "Պահանջներ": 12,
              "Հավելյալ նշումներ": 8}
    out = []
    for head, items in sections:
        uniq = []
        for it in items:
            if it not in uniq and not boiler(it):
                uniq.append(it)
        uniq = uniq[:limits.get(head, 8)]
        if uniq:
            out.append((head, uniq))
    return out

def parse_jf(html_text, pid):
    t = text_of(html_text)
    title = title_of(html_text, ("h1", "h2", "h3", "h4", "h5"))

    company = ""
    for m in re.finditer(r'(?is)<a[^>]+href="[^"]*?/company/[^"]*"[^>]*>(.*?)</a>', html_text):
        cand = clean(text_of(m.group(1)))
        if cand:
            company = cand
            break

    def lab(name):
        m = re.search(re.escape(name) +
r"\s*`?\s*:\s*([^\n]{0,200})", t)
        val = clean(m.group(1)) if m else ""
        return "" if junk(val) else val

    ind = lab("Ոլորտ")
    place = lab("Վայրը").replace(",", ", ").replace("Հայաստան, ", "")

    return {
        "id": str(pid),
        "title": title[:90],
        "company": company[:70],
        "place": place[:70],
        "salary": money(lab("Աշխատավարձ"))[:60],
        "type": lab("Աշխատանքի տեսակը")[:50],
        "sched": lab("Գրաֆիկ")[:70],
        "exp": lab("Փորձ")[:50],
        "ind": ind[:90],
        "deadline": deadline_of(t),
        "phones": phones_of(html_text, t),
        "sections": jf_sections(t, title, company),
        "tags": tags_of(title, ind),
        "hy": is_armenian(title, t),
        "cat": cat_of(ind),
    }

def listing(sess):
    h = get(sess, SITEMAP)
    if not h:
        return []
    edge = time.strftime("%Y-%m-%d",
                         time.gmtime(time.time() + TZ_OFFSET * 3600 - DAYS * 86400))
    order, seen = [], set()
    for m in SITE_RE.finditer(h):
        url, mod = m.group(1), m.group(2)
        if mod < edge:
            continue
        path = url.replace(BASE, "")
        if path in seen:
            continue
        seen.add(path)
        order.append(path)
    print("sitemap՝", len(order))
    return order

def jf_listing(sess):
    ids, empty = [], 0
    for page in range(1, JF_PAGES + 1):
        url = BASE2 + "/hy/jobs" if page == 1 else BASE2 + "/hy/jobs?page=%d" % page
        h = get(sess, url)
        if not h:
            print("  jobfinder էջ", page, "չբացվեց")
            break
        new = 0
        for x in JF_LINK.findall(h):
            if x not in ids:
                ids.append(x)
                new += 1
        print("  jf էջ", page, "->", new)
        if new == 0:
            empty += 1
            if empty >= 2:
                break
            continue
        empty = 0
        time.sleep(PAUSE)
    print("jf ցանկ՝", len(ids))
    return ids

def fill_am(sess, st, n):
    if not st.get("jam_list"):
        st["jam_list"] = listing(sess)
    fps = st.setdefault("fps", {})
    out = []
    while st["jam_list"] and len(out) < n:
        path = st["jam_list"].pop(0)
        jid = path.rstrip("/").split("-")[-1]
        if jid in st["seen"] or jid in st["published"]:
            continue
        h = get(sess, BASE + path)
        if not h:
            continue
        d = parse_am(h, path)
        if not d["hy"]:
            st["seen"][jid] = 1
            st["seen_at"][jid] = time.time()
            print("  am բաց թողնված՝", jid, "| ոչ հայերեն")
            continue
        if not d["phones"]:
            st["seen"][jid] = 1
            st["seen_at"][jid] = time.time()
            print("  am բաց թողնված՝", jid, "| համար չկա")
            continue
        if expired(d["deadline"]):
            st["seen"][jid] = 1
            st["seen_at"][jid] = time.time()
            print("  am բաց թողնված՝", jid, "| ժամկետը լրացել է")
            continue
        if not d["title"]:
            st["seen"][jid] = 1
            st["seen_at"][jid] = time.time()
            print("  am բաց թողնված՝", jid, "| վերնագիր չկա")
            continue
        text = make_caption(d)
        if fps.get(fp_of(text)):
            st["seen"][jid] = 1
            st["seen_at"][jid] = time.time()
            print("  am կրկնություն՝", jid)
            continue
        out.append({"path": path, "id": jid, "cat": d["cat"], "site": "am",
                    "text": text})
        time.sleep(PAUSE)
    save(st)
    return out

def fill_jf(sess, st, n):
    fps = st.setdefault("fps", {})
    out, tries = [], 0
    while len(out) < n and tries < 40:
        if not st.get("jf_list"):
            fresh = [x for x in jf_listing(sess)
                     if x not in st["seen"] and x not in st["published"]]
            if not fresh:
                print("  jf՝ նոր հայտարարություն չմնաց (այս պահին)")
                break
            st["jf_list"] = fresh
        pid = st["jf_list"].pop(0)
        tries += 1
        if pid in st["seen"] or pid in st["published"]:
            continue
        h = get(sess, BASE2 + "/jobs/x/" + pid)
        if not h:
            continue
        try:
            d = parse_jf(h, pid)
        except Exception:
            st["seen"][pid] = 1
            st["seen_at"][pid] = time.time()
            continue

        bad = ""
        if not d["title"]:
            bad = "վերնագիր չկա"
        elif not d["hy"]:
            bad = "ոչ հայերեն"
        elif not d["phones"]:
            bad = "համար չկա"
        elif expired(d["deadline"]):
            bad = "ժամկետը լրացել է"
        if bad:
            st["seen"][pid] = 1
            st["seen_at"][pid] = time.time()
            print("  jf բաց թողնված՝", pid, "|", bad)
            continue

        text = make_caption(d)
        if fps.get(fp_of(text)):
            st["seen"][pid] = 1
            st["seen_at"][pid] = time.time()
            print("  jf կրկնություն՝", pid)
            continue

        out.append({"path": "/jobs/x/" + pid, "id": pid, "cat": d["cat"], "site": "jf",
                    "text": text})
        time.sleep(PAUSE)
    save(st)
    return out

def prune(st):
    now = time.time()
    stamps = st.setdefault("seen_at", {})
    for k in list(st["seen"].keys()):
        stamps.setdefault(k, now)
    old = [k for k, ts in stamps.items() if now - ts > SEEN_DAYS * 86400]
    for k in old:
        st["seen"].pop(k, None)
        stamps.pop(k, None)
    if old:
        print("seen մաքրված՝", len(old))

def load():
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        st = {}
    st.setdefault("queue", [])
    st.setdefault("seen", {})
    st.setdefault("published", {})
    st.setdefault("fps", {})
    st.setdefault("seen_at", {})
    if st.get("ver") != VER:
        st["seen"] = {}
        st["seen_at"] = {}
        st["jam_list"] = []
        st["jf_list"] = []
        st["queue"] = []
        st["ver"] = VER
    return st

def save(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)

def send(sess, text):
    try:
        r = sess.post("https://api.telegram.org/bot%s/sendMessage" % TOKEN,
                      data={"chat_id": CHANNEL, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": "true"}, timeout=40)
        print("telegram:", r.status_code, r.text[:200])
        return r.ok
    except Exception as e:
        print("telegram սխալ", type(e).__name__)
        return False

def pick(st):
    q = st["queue"]
    last = st.get("last_site")
    if last:
        for i, it in enumerate(q):
            if it.get("site", "am") != last:
                return i
    return 0

def drop_id(st, item):
    st["seen"][item["id"]] = 1
    st.setdefault("seen_at", {})[item["id"]] = time.time()
    st["queue"] = [x for x in st["queue"] if x.get("id") != item["id"]]
    save(st)

def publish(sess, st):
    tried, skips = 0, 0
    while st["queue"] and tried < TRIES_MAX and skips < 60:
        idx = pick(st)
        item = st["queue"][idx]
        text = item.get("text") or ""

        if item.get("v") != VER:
            base = BASE2 if item.get("site") == "jf" else BASE
            h = get(sess, base + item["path"])
            if h:
                d = parse_jf(h, item["id"]) if item.get("site") == "jf" else parse_am(h, item["path"])
                if expired(d["deadline"]) or not d["hy"] or not d["title"] or not d["phones"]:
                    drop_id(st, item)
                    skips += 1
                    continue
                text = make_caption(d)
                item["text"] = text
                item["v"] = VER
                save(st)
            elif not text:
                print("  տեքստ դեռ չկա, հերթը մնում է նույնը")
                return False

        fps = st.setdefault("fps", {})
        mk = fp_of(text)
        if fps.get(mk):
            drop_id(st, item)
            skips += 1
            print("  նույն հայտարարությունն արդեն եղել է, բաց թողնված")
            continue

        tried += 1
        if send(sess, text):
            st["published"][item["id"]] = 1
            st["last_site"] = item.get("site", "am")
            fps[mk] = 1
            st["queue"] = [x for x in st["queue"] if x.get("id") != item["id"]]
            save(st)
            print("հրապարակված՝", item["id"], item.get("site", "am"))
            return True
        print("  չուղարկվեց, մնում է հերթում")
        return False

    print("ուշադրություն՝ ոչինչ չհրապարակվեց,", tried, "փորձ,", skips, "կրկնություն | հերթ:", len(st["queue"]))
    return False

def collect(sess, st):
    q = st["queue"]
    if len(q) >= QUEUE_MAX:
        return

    am = sum(1 for it in q if it.get("site") != "jf")
    jf = sum(1 for it in q if it.get("site") == "jf")

    part = []

    if am < STEP:
        part += fill_am(sess, st, STEP - am)

    need_jf = MIN_JF - jf - len([x for x in part if x.get("site") == "jf"])
    if need_jf > 0:
        part += fill_jf(sess, st, need_jf)

    have = {it.get("id") for it in st["queue"]}
    added = 0
    for it in part:
        if len(st["queue"]) >= QUEUE_MAX:
            break
        if it["id"] in have:
            continue
        it["v"] = VER
        st["queue"].append(it)
        have.add(it["id"])
        added += 1
    save(st)

    am_now = sum(1 for it in st["queue"] if it.get("site") != "jf")
    jf_now = sum(1 for it in st["queue"] if it.get("site") == "jf")
    print("հերթագրված՝", added, "| հերթ:", len(st["queue"]),
          "| job.am՝", am_now, "| jobfinder՝", jf_now)

def main():
    st = load()
    prune(st)
    sess = requests.Session()
    mode = sys.argv[1].strip().lower() if len(sys.argv) > 1 else ""

    if mode == "test":
        for fn in (fill_am, fill_jf):
            for it in fn(sess, st, 2):
                print("-" * 40)
                print("աղբյուր՝", it["site"], "| id՝", it["id"])
                print(it["text"])
        return

    if not publish_window():
        collect(sess, st)
        print("գիշերային ժամ, չի հրապարակվում. հերթում՝", len(st["queue"]))
        return

    publish(sess, st)

    if len(st["queue"]) < REFILL_AT:
        collect(sess, st)

    print("վերջ. հերթում՝", len(st["queue"]))

if __name__ == "__main__":
    main()
