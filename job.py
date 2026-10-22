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
VER       = 4

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
DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
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
            "Ինչ ենք առաջարկում": "Առավելություններ",
            "Մեր առաջարկը": "Առավելություններ",
            "Առավելություններ": "Առավելություններ",
            "Աշխատանքային պարտականությունները": "Պարտականություններ",
            "Պարտականություններ": "Պարտականություններ",
            "Պահանջվող որակավորումներ": "Պահանջներ",
            "Պահանջներ": "Պահանջներ",
            "Վարձատրություն և պայմաններ": "Հավելյալ նշումներ",
            "Լրացուցիչ տեղեկություններ": "Հավելյալ նշումներ"}
STOP_LINES = ("Ամբողջությամբ կարդալ", "Տեսնել ավելին", "Դիմել", "Կարդալ ավելին")
STOP_WORDS = ("Ընկերության մասին", "Տարածեք հայտարարությունը")
FORM = ("Անուն Ազգանուն", "Էլ. հասցե", "Նշեք գործող", "Լրացրեք դաշտերը",
        "Ուղարկել", "Կցել", "Ընտրեք ֆայլ", "Գրանցվել", "Ձեր անունը",
        "Հեռախոս *", "Ծածկագիր", "Դիմել այս")
SENT_END = (".", ":", "!", "?", "։", "»", ";", "…")
JUNK = ("$", "(", ")", "{", "}", "[", "]", "->")
BAD_TITLES = ("Նմանատիպ աշխատանքներ", "Ակտիվ աշխատանքներ",
              "Այլ աշխատանքներ", "Վերջին աշխատանքներ", "Աշխատանքներ")

def get(sess, url):
    for _ in range(2):
        try:
            r = sess.get(url, headers=HDRS, timeout=45)
            if r.status_code == 200 and len(r.text) > 2000:
                return r.text
            if r.status_code not in (404, 410):
                print("  պատասխան", r.status_code, url)
            break
        except Exception as e:
            print("  ցանցի սխալ", type(e).__name__, url)
    return ""

def text_of(h):
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", h)
    h = html.unescape(h)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</(div|p|li|h1|h2|h3|h4|h5|tr|section)>", "\n", h)
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
        d, m, y = [int(x) for x in dl.split(".")]
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

def tags_of(ind, n=3):
    tags = []
    for part in re.split(r"[,;/]", ind or ""):
        words = []
        for w in clean(part).split():
            if words and w[:1].isupper():
                break
            words.append(w)
        if not words:
            continue
        tag = "#" + "_".join(words[:3])
        if len(tag) > 26:
            tag = tag[:26]
        if tag not in tags and len(tags) < n:
            tags.append(tag)
    return tags

def cat_of(ind):
    return clean(re.split(r"[,;/]", ind)[0]) if ind else "այլ"

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
            if it not in uniq and ARM.search(it):
                uniq.append(it)
        uniq = uniq[:limits.get(head, 8)]
        if uniq:
            clean_sections.append((head, uniq))

    if not clean_sections:
        plain = []
        for line in body.split("\n"):
            low = clean(line.lstrip("—-•·–🔺"))
            if not low or low in STOP_LINES or low == title:
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
    armenian = len(ARM.findall(title)) * 3 + len(ARM.findall(body))
    latin = len(LAT.findall(body))

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
        "tags": tags_of(ind),
        "hy": armenian >= 30 and armenian >= latin,
        "cat": cat_of(ind),
    }

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
        m = re.search(re.escape(name) + r"\s*`?\s*([^\n]{0,200})", t)
        val = clean(m.group(1)) if m else ""
        return "" if junk(val) else val

    ind = lab("Ոլորտ")
    place = lab("Վայրը").replace(",", ", ")

    sections, cur, started = [], None, False
    for line in t.split("\n"):
        low = clean(line)
        if not low:
            continue
        if low in JF_HEADS:
            cur = [JF_HEADS[low], []]
            sections.append(cur)
            started = True
            continue
        if started and (low.startswith("Դիմել") or low.startswith("Ընկերության մասին")):
            break
        if not started or cur is None:
            continue
        it = clean(low.lstrip("-–•—🔺 "))
        if not it or not ARM.search(it) or len(it) < 3:
            continue
        add_item(cur, it)

    limits = {"Նկարագրություն": 4, "Առավելություններ": 10,
              "Պարտականություններ": 12, "Պահանջներ": 12,
              "Հավելյալ նշումներ": 8}
    clean_sections = []
    for head, items in sections:
        uniq = []
        for it in items:
            if it not in uniq:
                uniq.append(it)
        uniq = uniq[:limits.get(head, 8)]
        if uniq:
            clean_sections.append((head, uniq))

    armenian = len(ARM.findall(title)) * 3 + len(ARM.findall(t))
    latin = len(LAT.findall(t))

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
        "sections": clean_sections,
        "tags": tags_of(ind),
        "hy": armenian >= 30 and armenian >= latin,
        "cat": cat_of(ind),
    }

def listing(sess):
    h = get(sess, SITEMAP)
    if not h:
        return []
    edge = time.strftime("%Y-%m-%d",
                         time.gmtime(time.time() + 4 * 3600 - DAYS * 86400))
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
    h = get(sess, BASE2 + "/hy/jobs")
    ids = []
    for x in JF_LINK.findall(h or ""):
        if x not in ids:
            ids.append(x)
    return ids

def fill_am(sess, st, n):
    if not st.get("jam_list"):
        st["jam_list"] = listing(sess)
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
        if not d["hy"] or not d["phones"] or expired(d["deadline"]) or not d["title"]:
            st["seen"][jid] = 1
            print("  am բաց թողնված՝", jid)
            continue
        out.append({"path": path, "id": jid, "cat": d["cat"], "site": "am",
                    "text": make_caption(d)})
        time.sleep(1)
    save(st)
    return out

def fill_jf(sess, st, n):
    out, tries = [], 0
    while len(out) < n and tries < 25:
        if not st.get("jf_list"):
            fresh = [x for x in jf_listing(sess)
                     if x not in st["seen"] and x not in st["published"]]
            if not fresh:
                break
            st["jf_list"] = fresh
            print("jf ցանկ՝", len(fresh))
        pid = st["jf_list"].pop(0)
        tries += 1
        if pid in st["seen"] or pid in st["published"]:
            continue
        h = get(sess, BASE2 + "/jobs/x/" + pid)
        if not h:
            st["seen"][pid] = 1
            continue
        try:
            d = parse_jf(h, pid)
        except Exception:
            st["seen"][pid] = 1
            continue

        bad = ""
        if not d["title"]:
            bad = "վերնագիր չկա"
        elif not d["hy"]:
            bad = "ոչ հայերեն"
        elif expired(d["deadline"]):
            bad = "ժամկետը լրացել է"
        if bad:
            st["seen"][pid] = 1
            print("  jf բաց թողնված՝", pid, "|", bad)
            continue

        text = make_caption(d)
        if not d["phones"]:
            text += "\n🔗 Դիմելու համար՝ https://jobfinder.am/jobs/x/%s" % pid

        out.append({"path": "/jobs/x/" + pid, "id": pid, "cat": d["cat"], "site": "jf",
                    "text": text})
        time.sleep(1)
    save(st)
    return out

def load():
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        st = {}
    st.setdefault("queue", [])
    st.setdefault("seen", {})
    st.setdefault("published", {})
    if st.get("ver") != VER:
        st["seen"] = {}
        st["jf_list"] = []
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

def publish(sess, st):
    tried = 0
    while st["queue"] and tried < TRIES_MAX:
        idx = pick(st)
        item = st["queue"][idx]
        tried += 1
        text = item.get("text") or ""

        if item.get("v") != VER:
            base = BASE2 if item.get("site") == "jf" else BASE
            h = get(sess, base + item["path"])
            if h:
                d = parse_jf(h, item["id"]) if item.get("site") == "jf" else parse_am(h, item["path"])
                if expired(d["deadline"]) or not d["hy"] or not d["title"]:
                    st["seen"][item["id"]] = 1
                    st["queue"].pop(idx)
                    save(st)
                    continue
                text = make_caption(d)
                if not d["phones"]:
                    text += "\n🔗 Դիմելու համար՝ https://jobfinder.am/jobs/x/%s" % item["id"]
                item["text"] = text
                item["v"] = VER
                save(st)
            elif not text:
                print("  տեքստ դեռ չկա, հերթը մնում է նույնը")
                return False

        fps = st.setdefault("fps", {})
        mk = fp_of(text)
        if fps.get(mk):
            st["seen"][item["id"]] = 1
            st["queue"].pop(idx)
            save(st)
            print("  նույն հայտարարությունն արդեն եղել է, բաց թողնված")
            continue

        if send(sess, text):
            st["published"][item["id"]] = 1
            st["last_site"] = item.get("site", "am")
            fps[mk] = 1
            st["queue"].pop(idx)
            save(st)
            print("հրապարակված՝", item["id"], item.get("site", "am"))
            return True
        print("  չուղարկվեց, մնում է հերթում")
        return False

    print("ուշադրություն՝ ոչինչ չհրապարակվեց,", tried, "փորձից հետո | հերթ:", len(st["queue"]))
    return False

def collect(sess, st):
    q = st["queue"]
    if len(q) >= QUEUE_MAX:
        return

    jf = sum(1 for it in q if it.get("site") == "jf")
    am = len(q) - jf

    if len(q) >= REFILL_AT and jf >= MIN_JF:
        return

    turn = st.get("turn", 0)
    if jf < MIN_JF and am >= jf:
        fn = fill_jf
    elif am < jf:
        fn = fill_am
    else:
        fn = fill_jf if turn % 2 == 0 else fill_am
    st["turn"] = turn + 1

    part = fn(sess, st, STEP)
    if not part and len(st["queue"]) < REFILL_AT:
        other = fill_am if fn is fill_jf else fill_jf
        part = other(sess, st, STEP)

    for it in part:
        if len(st["queue"]) >= QUEUE_MAX:
            break
        it["v"] = VER
        st["queue"].append(it)
    save(st)

    jf_now = sum(1 for it in st["queue"] if it.get("site") == "jf")
    print("հերթագրված՝", len(part), "| հերթ:", len(st["queue"]), "| jobfinder՝", jf_now)

def main():
    st = load()
    sess = requests.Session()
    mode = sys.argv[1].strip().lower() if len(sys.argv) > 1 else ""

    if mode == "test":
        for fn in (fill_am, fill_jf):
            for it in fn(sess, st, 2):
                print("-" * 40)
                print("աղբյուր՝", it["site"], "| id՝", it["id"])
                print(it["text"])
        return

    collect(sess, st)
    publish(sess, st)
    print("վերջ. հերթում՝", len(st["queue"]))

if __name__ == "__main__":
    main()
