# -*- coding: utf-8 -*-
"""
ԱԼԻՔ   ՝ jobs  («Աշխատանք Հայաստանում | Գործ կա»)
ԿԱՅՔԵՐ ՝ job.am + jobfinder.am
ՖԱՅԼ   ՝ job.py | state՝ state-job.json | secrets՝ TELEGRAM_TOKEN, JOB_CHANNEL

Հերթի կանոնները՝
  * հերթում առավելագույնը QUEUE_MAX (400) հայտարարություն
  * հերթը լրացվում է մեկ ու մեջ՝ 1 job.am, 1 jobfinder, 1 job.am ...
  * հերթում հայտարարությունը վերջում պարտադիր հրապարակվում է (ոչ մի ստուգումով դուրս չի գալիս)
  * եթե հերթը լիքն է, հանվում է ամենահինը (հերթի սկզբինը), նորը գնում է պոչ
  * եթե Telegram-ը չընդունեց, նոր փորձ չկա, հայտարարությունը հեռացվում է
  * եթե մի կայքում նոր հայտարարություն չկա, հրապարակվում է մյուսից
"""
import os, re, sys, json, html, time
import requests
from urllib.parse import unquote

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHANNEL = os.environ.get("JOB_CHANNEL", "")
STATE   = "state-job.json"

BASE    = "https://job.am"
SITEMAP = "https://job.am/sitemap/jobs.xml"
BASE2   = "https://jobfinder.am"

TZ_OFFSET  = 4          # Երևան (UTC+4)
HOUR_FROM  = 8          # հրապարակումը սկսվում է 08:00
HOUR_TO    = 23         # մինչև 23:59

DAYS            = 30    # job.am-ից վերցվում է վերջին 30 օրվա sitemap-ը
QUEUE_MAX       = 400   # հերթի առաստաղ
COLLECT_SECONDS = 240   # մեկ գործարկման ընթացքում հավաքելու ժամանակի սահման
MAX_FAILS       = 3     # մեկ գործարկման ընթացքում առավելագույնը այսքան ձախողված հայտարարություն կհեռացվի
JF_PAGES        = 12

# ԶՐՈՅԱՑՈՒՄ՝ այս թիվը փոխելիս state-ը (հերթը, բոլոր ID-ները) ամբողջությամբ մաքրվում է
STATE_VER = 20
# True՝ զրոյացման ժամանակ արդեն հրապարակվածների ID-ները և մատնահետքերը պահվում են,
# որ նույն հայտարարությունները նորից չհրապարակվեն
KEEP_PUBLISHED_ON_RESET = True

SEEN_DAYS = 35          # մերժված/հեռացված ID-ները չեն ստուգվում այսքան օր (> 30 օրվա sitemap)
KEEP_DAYS = 60          # հրապարակվածների ID-ները և մատնահետքերը պահվում են այսքան օր

PAUSE     = 2.5
WAIT_429  = 20
TRIES_429 = 2

LIMIT  = 4000           # Telegram-ի սահմանը 4096 է
BULLET = "🔺"

DRY  = False            # test ռեժիմում state-ը չի պահպանվում
FLAG = {"429": False}   # եթե կայքը 429 տվեց, այս գործարկման հավաքումը կանգնում է
STATS = {}

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
PHONE_RE = re.compile(r"(?<!\d)(?:\+?374|0)(?:[ \t\-\(\)]*\d){8}(?!\d)")
TEL_RE = re.compile(r"""href=["']\s*tel:([^"']{6,40})["']""", re.I)
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
            "Լրացուցիչ տեղեկություններ": "Հավելյալ նշումներ",
            "Հավելյալ նշումներ": "Հավելյալ նշումներ",
            "Հավելյալ տեղեկություններ": "Հավելյալ նշումներ",
            "Աշխատանքային պայմաններ": "Հավելյալ նշումներ",
            "Մենք առաջարկում ենք": "Առավելություններ",
            "Առաջարկում ենք": "Առավելություններ",
            "Նպաստներ": "Առավելություններ",
            "Պահանջվող հմտություններ": "Պահանջներ",
            "Հիմնական պարտականություններ": "Պարտականություններ",
            "Հիմնական պահանջներ": "Պահանջներ"}
# Վերնագրի նման կարճ տողերի ճանաչում (եթե ճշգրիտ անունը ցանկում չկա)
JF_STEMS = (("պարտականություն", "Պարտականություններ"),
            ("գործառույթ", "Պարտականություններ"),
            ("որակավորում", "Պահանջներ"),
            ("պահանջ", "Պահանջներ"),
            ("առավելություն", "Առավելություններ"),
            ("առաջարկ", "Առավելություններ"),
            ("նշումներ", "Հավելյալ նշումներ"),
            ("նկարագր", "Նկարագրություն"))
# Կայքի մենյուի տողեր, որոնք երբեք հայտարարության մաս չեն
NAV_LINES = ("մուտք", "գրանցվել", "մուտք / գրանցվել", "հայ", "eng", "en", "ru", "рус",
             "աշխատանքներ", "գործընկերներ", "գնացուցակ", "մեր մասին", "կապ",
             "գլխավոր", "ռեզյումեներ", "գործատուներ")
# jobfinder-ի վերևի տվյալների պիտակները
JF_LABELS = sorted(("Աշխատանքի տեսակը", "Աշխատանքային գրաֆիկ", "Աշխատանքային փորձ",
                    "Նախընտրելի տարիք", "Հեռ. համար", "Վերջնաժամկետը", "Վերջնաժամկետ",
                    "Աշխատավայր", "Աշխատավարձ", "Կրթություն", "Գրաֆիկ", "Վայրը",
                    "Ոլորտ", "Փորձ", "Սեռ", "Հեռ", "Դրույք"), key=len, reverse=True)
LABEL_SPLIT = re.compile("(" + "|".join(re.escape(x) for x in JF_LABELS) + r")(?=\s*[`\u055d:\u0589])")
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
# Միայն ծրագրային կոդի նշաններ։ Փակագծերը և $ նշանը այլևս չեն մերժվում
JUNK = ("{", "}", "->", "=>", "$(", "function(", "</", "/>")
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


# ---------------------------------------------------------------- ընդհանուր

def local_now():
    return time.gmtime(time.time() + TZ_OFFSET * 3600)

def publish_window():
    h = local_now().tm_hour
    return HOUR_FROM <= h <= HOUR_TO

def bump(key):
    STATS[key] = STATS.get(key, 0) + 1

def get(sess, url):
    for attempt in range(TRIES_429):
        try:
            r = sess.get(url, headers=HDRS, timeout=45)
            if r.status_code == 200 and len(r.text) > 2000:
                return r.text
            if r.status_code == 429:
                if attempt < TRIES_429 - 1:
                    print("  պատասխան 429, սպասում", WAIT_429 * (attempt + 1), "վրկ |", url)
                    time.sleep(WAIT_429 * (attempt + 1))
                    continue
                FLAG["429"] = True
                print("  պատասխան 429, այս գործարկման հավաքումը կանգնում է")
                break
            if r.status_code not in (404, 410):
                print("  պատասխան", r.status_code, url)
            break
        except Exception as e:
            print("  ցանցի սխալ", type(e).__name__, url)
            time.sleep(3)
    print("  չկարդացվեց |", url)
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

def main_text(t):
    """Կտրում է «Նմանատիպ աշխատանքներ» բաժնից սկսած, որ ուրիշ հայտարարության տվյալներ չմտնեն"""
    k = t.find("Նմանատիպ աշխատանքներ")
    return t[:k] if k > 500 else t

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

def norm_phone(raw):
    """Վերադարձնում է միայն ճիշտ հայկական համար (+374 XX XXX XXX), այլապես դատարկ տող"""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("374") and len(digits) == 11:
        d = digits[3:]
    elif digits.startswith("0") and len(digits) == 9:
        d = digits[1:]
    else:
        return ""
    return "+374 %s %s %s" % (d[:2], d[2:5], d[5:])

def phones_of(html_text, t):
    raw = [unquote(m.group(1)) for m in TEL_RE.finditer(html_text)]
    raw += [m.group(0) for m in PHONE_RE.finditer(t)]
    res = []
    for p in raw:
        p = norm_phone(p)
        if p and p not in res:
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


# ---------------------------------------------------------------- գրառման տեսքը

def make_caption(d):
    """Տեսքը նույնն է, ինչ նախկինում։ Եթե երկար է, ավելորդ կետերը դուրս են մնում
    (HTML-ը երբեք չի կտրվում), իսկ վերջին մասը (ժամկետ, համար, հեշթեգեր) միշտ մնում է։"""
    head = ["<b>%s</b>" % esc(d["title"])]
    if d["company"]:
        head.append(esc(d["company"]))
    meta = []
    for k, v in (("Աշխատավայր", d["place"]), ("Աշխատավարձ", d["salary"]),
                 ("Դրույք", d["type"]), ("Գրաֆիկ", d["sched"]),
                 ("Փորձ", d["exp"]), ("Ոլորտ", d["ind"])):
        if v:
            meta.append("• %s՝ %s" % (k, esc(v)))
    if meta:
        head.append("")
        head.extend(meta)

    foot = [""]
    if d["deadline"]:
        foot.append("⏳ Վերջնաժամկետ՝ %s" % d["deadline"])
    if d["phones"]:
        foot.append("📞 Հեռ.՝ %s" % esc(", ".join(d["phones"])))
    if d["tags"]:
        foot.append("")
        foot.append(" ".join(d["tags"]))

    room = LIMIT - len("\n".join(head)) - len("\n".join(foot)) - 10
    body, used, cut = [], 0, False
    for name, items in d["sections"]:
        if not items:
            continue
        hdr = "🔹 <b>%s</b>" % esc(name)
        lines, cost = [], len(hdr) + 2
        for it in items:
            line = "%s %s" % (BULLET, esc(it))
            if used + cost + len(line) + 1 > room:
                cut = True
                break
            lines.append(line)
            cost += len(line) + 1
        if lines:
            body.append("")
            body.append(hdr)
            body.extend(lines)
            used += cost
        if cut:
            break

    out = "\n".join(head + body + foot).strip()
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    if len(out) > 4090:
        out = "\n".join(head + foot).strip()
    return out


# ---------------------------------------------------------------- job.am

def parse_am(html_text, path):
    t = main_text(text_of(html_text))
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
    }


# ---------------------------------------------------------------- jobfinder

def jf_head(line):
    """Եթե տողը բաժնի վերնագիր է՝ վերադարձնում է (բաժնի անուն, նույն տողի մնացած տեքստ), այլապես (None, "")"""
    head = JF_HEADS.get(line)
    if head:
        return head, ""
    for k, v in JF_HEADS.items():
        if line.startswith(k) and len(line) - len(k) < 60:
            if len(line) == len(k) or not line[len(k)].isalpha():
                rest = clean(line[len(k):].lstrip(" `\u055d:\u0589-–"))
                return v, rest
    low = line.lower()
    words = line.split()
    if (1 <= len(words) <= 3 and len(line) <= 40 and line[0].isupper()
            and not re.search(r"[.\u0589:;,!?()\d]", line)
            and not low.startswith("պահանջվում")):
        for stem, v in JF_STEMS:
            if stem in low:
                return v, ""
    return None, ""

def jf_header(t, title):
    """Վերնագրից հետո մինչև առաջին բաժինը եղած տողերը (Վայրը, Աշխատավարձ, Ոլորտ ...)"""
    lines = [clean(x) for x in t.split("\n")]
    lines = [x for x in lines if x]
    tc = clean(title).lower()
    start = -1
    for i, x in enumerate(lines):
        c = re.sub(r"\s+", " ", x.lower()).strip(" «»\"'")
        if tc and (c == tc or (len(c) > 6 and c.startswith(tc))):
            start = i + 1
            break
    if start < 0:
        return []
    out = []
    for x in lines[start:start + 40]:
        if jf_head(x)[0]:
            break
        for part in LABEL_SPLIT.sub(lambda m: "\n" + m.group(1), x).split("\n"):
            part = clean(part)
            if part:
                out.append(part)
    return out

def jf_lab(lines, names):
    """Փնտրում է պիտակը (Վայրը, Ոլորտ ...)։ Արժեքը կարող է լինել նույն տողում (` ՝ : նշաններից հետո)
    կամ հաջորդ տողում"""
    for name in names:
        for i, line in enumerate(lines):
            if not line.startswith(name):
                continue
            rest = line[len(name):]
            if rest and rest[0].isalpha():
                continue
            rest = clean(rest.lstrip(" \t`\u055d:\u0589-–"))
            if rest:
                val = rest
            else:
                nxt = lines[i + 1] if i + 1 < len(lines) else ""
                if (not nxt or nxt.startswith(STOP_LINES)
                        or any(nxt.startswith(x) for x in JF_LABELS)):
                    continue
                val = clean(nxt)
            if val and not junk(val):
                return val
    return ""

def jf_sections(t, title="", company=""):
    lines = [clean(x) for x in t.split("\n")]
    lines = [x for x in lines if x]

    t_core = clean(title).lower()
    meta = [t_core, clean(company).lower()]

    def is_title(x):
        c = re.sub(r"\s+", " ", x.lower()).strip(" «»\"'")
        return bool(t_core) and (c == t_core or (len(c) > 6 and c.startswith(t_core)))

    # Վերնագրից առաջ եղած ամեն ինչ (մենյու, մուտք, լեզու) բաց է թողնվում
    after = not any(is_title(x) for x in lines)
    sections, cur, started, intro = [], None, False, []

    for line in lines:
        low = line.lower()
        core = re.sub(r"\s+", " ", low).strip(" «»\"'")
        if not after:
            if is_title(line):
                after = True
            continue
        if "jobfinder" in low:
            continue
        if core in NAV_LINES:
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

        head, rest0 = jf_head(line)

        if head:
            if not started and intro:
                first = [x for x in intro if ARM.search(x) and not boiler(x)]
                if first:
                    sections.append(["Նկարագրություն", first[:5]])
            started = True
            cur = [head, []]
            sections.append(cur)
            if rest0:
                add_item(cur, rest0)
            continue

        if started and (line.startswith("Դիմել") or line.startswith("Ընկերության մասին")):
            break
        if started and boiler(line):
            break

        if not started:
            if any(line.startswith(x) for x in JF_META):
                continue
            if any(line.startswith(x) for x in JF_LABELS) or LABEL_SPLIT.search(line):
                continue
            if re.search(r"\d{2}[./]\d{2}[./]\d{4}", line):
                continue
            if any(low.startswith(b) for b in INTRO_BAD):
                continue
            # նախաբանում վերցվում են միայն իսկական նախադասությունները (ոչ կարճ մենյուի բառերը)
            if len(line) >= 30 and len(intro) < 14 and line not in intro:
                intro.append(line)
            continue

        if cur is None:
            continue
        it = clean(line.lstrip("-–•—🔺 "))
        if not it or not ARM.search(it) or len(it) < 3:
            continue
        add_item(cur, it)

    if not started and intro:
        first = [x for x in intro if ARM.search(x) and not boiler(x)]
        if first:
            sections.append(["Նկարագրություն", first[:5]])

    # Նույն անունով բաժինները միացվում են (չի լինում երկու «Նկարագրություն»)
    merged = []
    for head, items in sections:
        same = next((m for m in merged if m[0] == head), None)
        if same is None:
            merged.append([head, list(items)])
        else:
            same[1].extend(items)

    limits = {"Նկարագրություն": 6, "Առավելություններ": 10,
              "Պարտականություններ": 12, "Պահանջներ": 12,
              "Հավելյալ նշումներ": 8}
    out = []
    for head, items in merged:
        uniq = []
        for it in items:
            if it not in uniq and not boiler(it):
                uniq.append(it)
        uniq = uniq[:limits.get(head, 8)]
        if uniq:
            out.append((head, uniq))
    return out

def parse_jf(html_text, pid):
    t = main_text(text_of(html_text))
    title = title_of(html_text, ("h1", "h2", "h3", "h4", "h5"))

    company = ""
    for m in re.finditer(r'(?is)<a[^>]+href="[^"]*?/company/[^"]*"[^>]*>(.*?)</a>', html_text):
        cand = clean(text_of(m.group(1)))
        if cand:
            company = cand
            break

    hdr = jf_header(t, title)

    def lab(*names):
        return jf_lab(hdr, names)

    ind = lab("Ոլորտ")
    place = re.sub(r"\s*,\s*", ", ", lab("Վայրը", "Աշխատավայր")).replace("Հայաստան, ", "")

    return {
        "id": str(pid),
        "title": title[:90],
        "company": company[:70],
        "place": place[:70],
        "salary": money(re.sub(r"^Ամսական\s*", "", lab("Աշխատավարձ")))[:60],
        "type": lab("Աշխատանքի տեսակը", "Դրույք")[:50],
        "sched": lab("Աշխատանքային գրաֆիկ", "Գրաֆիկ")[:70],
        "exp": lab("Աշխատանքային փորձ", "Փորձ")[:50],
        "ind": ind[:90],
        "deadline": deadline_of(t),
        "phones": phones_of(html_text, t),
        "sections": jf_sections(t, title, company),
        "tags": tags_of(title, ind),
        "hy": is_armenian(title, t),
    }


# ---------------------------------------------------------------- state

def fresh_state():
    return {"ver": STATE_VER, "queue": [], "seen": {}, "seen_at": {},
            "published": {}, "fps": {}, "jam_list": [], "jf_list": [],
            "turn": "am", "last_site": ""}

def load():
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        st = None
    if not isinstance(st, dict):
        print("state-ը չկա, սկսում եմ զրոյից")
        return fresh_state()
    if st.get("ver") != STATE_VER:
        new = fresh_state()
        if KEEP_PUBLISHED_ON_RESET:
            new["published"] = st.get("published", {})
            new["fps"] = st.get("fps", {})
            new["last_site"] = st.get("last_site", "")
        print("state-ը զրոյացվեց (նոր տարբերակ՝", STATE_VER, "), հերթը մաքրվեց")
        return new
    for k, v in fresh_state().items():
        st.setdefault(k, v)
    return st

def save(st):
    if DRY:
        return
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE)

def mark_seen(st, jid):
    st["seen"][jid] = 1
    st["seen_at"][jid] = time.time()

def prune(st):
    now = time.time()
    old = [k for k, ts in st["seen_at"].items() if now - ts > SEEN_DAYS * 86400]
    for k in old:
        st["seen"].pop(k, None)
        st["seen_at"].pop(k, None)
    for name in ("published", "fps"):
        dead = [k for k, ts in st[name].items()
                if isinstance(ts, (int, float)) and now - ts > KEEP_DAYS * 86400]
        for k in dead:
            st[name].pop(k, None)
    if old:
        print("seen մաքրված՝", len(old))


# ---------------------------------------------------------------- ցանկեր

def listing(sess, st, have):
    """job.am. վերջին 30 օրվա հայտարարությունները, ամենահինից դեպի նորը"""
    h = get(sess, SITEMAP)
    if not h:
        return []
    edge = time.strftime("%Y-%m-%d",
                         time.gmtime(time.time() + TZ_OFFSET * 3600 - DAYS * 86400))
    rows, dup = [], set()
    for m in SITE_RE.finditer(h):
        url, mod = m.group(1), m.group(2)
        if mod < edge:
            continue
        path = url.replace(BASE, "")
        if path in dup:
            continue
        dup.add(path)
        jid = path.rstrip("/").split("-")[-1]
        if jid in st["seen"] or jid in st["published"] or jid in have:
            continue
        rows.append((mod, path))
    rows.sort()
    print("sitemap՝ ստուգելու նոր", len(rows), "(ընդհանուր 30 օրվա մեջ՝", len(dup), ")")
    return [p for _, p in rows]

def jf_listing(sess, st, have):
    """jobfinder. ցանկը էջ առ էջ, վերադարձնում է ամենահինից դեպի նորը"""
    ids = []
    for page in range(1, JF_PAGES + 1):
        url = BASE2 + "/hy/jobs" if page == 1 else BASE2 + "/jobs?page=%d" % page
        h = get(sess, url)
        if not h:
            print("  jobfinder էջ", page, "չբացվեց")
            break
        page_ids = []
        for x in JF_LINK.findall(h):
            if x not in page_ids and x not in ids:
                page_ids.append(x)
        fresh = [x for x in page_ids
                 if x not in st["seen"] and x not in st["published"] and x not in have]
        print("  jf էջ", page, "->", len(page_ids), "| նոր՝", len(fresh))
        if not page_ids:
            print("  jf՝ էջը դատարկ է կամ կրկնում է նախորդը, կանգ")
            break
        ids.extend(fresh)
        if not fresh:
            break
        time.sleep(PAUSE)
    ids.reverse()
    print("jf՝ ստուգելու նոր", len(ids))
    return ids

def ensure_list(sess, st, site, have):
    key = "jam_list" if site == "am" else "jf_list"
    if not st[key]:
        st[key] = listing(sess, st, have) if site == "am" else jf_listing(sess, st, have)
    return st[key]


# ---------------------------------------------------------------- հավաքում

def accept(st, d, jid, site, qfp):
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
        mark_seen(st, jid)
        bump("%s՝ %s" % (site, bad))
        return None
    text = make_caption(d)
    fp = fp_of(text)
    if st["fps"].get(fp) or fp in qfp:
        mark_seen(st, jid)
        bump("%s՝ կրկնություն" % site)
        return None
    bump("%s՝ ընդունվեց" % site)
    return {"id": jid, "site": site, "text": text, "fp": fp}

def check_am(sess, st, path, qfp):
    jid = path.rstrip("/").split("-")[-1]
    if jid in st["seen"] or jid in st["published"]:
        return None
    h = get(sess, BASE + path)
    if not h:
        bump("am՝ չբացվեց")
        return None
    try:
        d = parse_am(h, path)
    except Exception as e:
        print("  am parse սխալ", jid, type(e).__name__)
        mark_seen(st, jid)
        bump("am՝ parse սխալ")
        return None
    item = accept(st, d, jid, "am", qfp)
    time.sleep(PAUSE)
    return item

def check_jf(sess, st, pid, qfp):
    if pid in st["seen"] or pid in st["published"]:
        return None
    h = get(sess, BASE2 + "/jobs/x/" + pid)
    if not h:
        bump("jf՝ չբացվեց")
        return None
    try:
        d = parse_jf(h, pid)
    except Exception as e:
        print("  jf parse սխալ", pid, type(e).__name__)
        mark_seen(st, pid)
        bump("jf՝ parse սխալ")
        return None
    item = accept(st, d, pid, "jf", qfp)
    time.sleep(PAUSE)
    return item

def next_item(sess, st, site, have, qfp, t0, budget):
    """Վերադարձնում է (հայտարարություն կամ None, այս կայքը սպառվել է՞)"""
    while True:
        if time.time() - t0 >= budget or FLAG["429"]:
            return None, False
        lst = ensure_list(sess, st, site, have)
        if not lst:
            return None, True
        ident = lst.pop(0)
        item = (check_am if site == "am" else check_jf)(sess, st, ident, qfp)
        if item:
            return item, False

def add_to_queue(st, item, have, qfp):
    q = st["queue"]
    while len(q) >= QUEUE_MAX:
        old = q.pop(0)                      # ամենահինը՝ հերթի սկզբինը
        have.discard(old["id"])
        qfp.discard(old.get("fp"))
        mark_seen(st, old["id"])
        bump("հերթը լիքն է՝ հեռացվեց ամենահինը")
        print("  հերթը լիքն է, հեռացվեց ամենահինը՝", old["id"], old["site"])
    q.append(item)                          # նորը՝ պոչ
    have.add(item["id"])
    qfp.add(item["fp"])

def collect(sess, st, budget, max_new=None):
    t0 = time.time()
    have = {it["id"] for it in st["queue"]}
    qfp = {it.get("fp") for it in st["queue"]}
    exhausted, added = set(), 0
    turn = st.get("turn", "am")
    while time.time() - t0 < budget and not FLAG["429"]:
        if len(exhausted) == 2:
            break
        if max_new and added >= max_new:
            break
        site = turn
        turn = "jf" if site == "am" else "am"
        if site in exhausted:
            continue
        item, done = next_item(sess, st, site, have, qfp, t0, budget)
        if done:
            exhausted.add(site)
        if item:
            add_to_queue(st, item, have, qfp)
            added += 1
            st["turn"] = turn               # հաջորդը՝ մյուս կայքից
            save(st)
    save(st)
    am_n = sum(1 for it in st["queue"] if it["site"] == "am")
    jf_n = sum(1 for it in st["queue"] if it["site"] == "jf")
    print("հերթագրված՝", added, "| հերթ:", len(st["queue"]),
          "| job.am՝", am_n, "| jobfinder՝", jf_n,
          "| մնացել է ստուգելու՝ am", len(st["jam_list"]), "jf", len(st["jf_list"]))


# ---------------------------------------------------------------- հրապարակում

def send(sess, text):
    try:
        r = sess.post("https://api.telegram.org/bot%s/sendMessage" % TOKEN,
                      data={"chat_id": CHANNEL, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": "true"}, timeout=40)
        if r.ok:
            print("telegram՝ ok")
        else:
            print("telegram սխալ՝", r.status_code, r.text[:300])
        return r.ok
    except Exception as e:
        print("telegram սխալ", type(e).__name__)
        return False

def pick(st):
    """Մեկ ու մեջ. վերցնում է հերթի առաջին այն հայտարարությունը, որը նախորդ կայքից չէ։
    Եթե մյուս կայքից հայտարարություն չկա, վերցնում է հերթի սկզբինը։"""
    q = st["queue"]
    last = st.get("last_site")
    if last:
        for i, it in enumerate(q):
            if it.get("site") != last:
                return i
    return 0

def publish(sess, st):
    fails = 0
    while st["queue"] and fails < MAX_FAILS:
        item = st["queue"].pop(pick(st))    # հերթից հանվում է անմիջապես, նոր փորձ չկա
        if send(sess, item["text"]):
            now = time.time()
            st["published"][item["id"]] = now
            st["fps"][item.get("fp") or fp_of(item["text"])] = now
            st["last_site"] = item["site"]
            save(st)
            print("հրապարակված՝", item["id"], item["site"])
            return True
        mark_seen(st, item["id"])
        fails += 1
        save(st)
        print("  չհրապարակվեց, հեռացվեց հերթից՝", item["id"], item["site"])
    if not st["queue"]:
        print("ուշադրություն՝ հերթը դատարկ է, ոչինչ չհրապարակվեց")
    return False

def print_stats():
    if STATS:
        print("հաշվարկ.")
        for k in sorted(STATS):
            print("   ", k, "=", STATS[k])


# ---------------------------------------------------------------- main

def main():
    global DRY
    mode = sys.argv[1].strip().lower() if len(sys.argv) > 1 else ""
    st = load()
    prune(st)
    sess = requests.Session()

    if mode == "test":
        DRY = True                          # ոչինչ չի պահպանվում և չի հրապարակվում
        st["queue"] = []
        collect(sess, st, 120, max_new=4)
        for it in st["queue"]:
            print("-" * 40)
            print("աղբյուր՝", it["site"], "| id՝", it["id"])
            print(it["text"])
        print_stats()
        return

    if publish_window():
        publish(sess, st)
    else:
        print("գիշերային ժամ, չի հրապարակվում, միայն հավաքում է")

    collect(sess, st, COLLECT_SECONDS)
    print_stats()
    print("վերջ. հերթում՝", len(st["queue"]))

if __name__ == "__main__":
    main()
