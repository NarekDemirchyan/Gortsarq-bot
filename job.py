# -*- coding: utf-8 -*-
"""
job.am + jobfinder.am -> Telegram «Աշխատանք Հայաստանում | Գործ կա»
"""
import os, re, sys, json, html, time, random
import requests

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHANNEL = os.environ.get("JOB_CHANNEL", "")
STATE   = "state-job.json"

BASE    = "https://job.am"
SITEMAP = "https://job.am/sitemap/jobs.xml"
BASE2   = "https://jobfinder.am"

DAYS    = 30
QUEUE_MAX = 200
REFILL_AT = 50
STEP      = 8
TRIES_MAX = 3

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
SLASH_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
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
    "Գործառույթներ": "Պարտականություններ",
    "Հիմնական պահանջներ": "Պահանջներ",
    "Աշխատանքային պահանջներ": "Պահանջներ",
    "Պարտադիր պահանջներ": "Պահանջներ",
}
JF_HEADS = {"Աշխատանքի նկարագրությունը": "Նկարագրություն",
            "Աշխատանքային պարտականությունները": "Պարտականություններ",
            "Պահանջվող որակավորումներ": "Պահանջներ",
            "Վարձատրություն և պայմաններ": "Հավելյալ նշումներ"}
STOP_LINES = ("Ամբողջությամբ կարդալ", "Տեսնել ավելին", "Դիմել", "Կարդալ ավելին")
STOP_WORDS = ("Ընկերության մասին", "Տարածեք հայտարարությունը")
FORM = ("Անուն Ազգանուն", "Էլ. հասցե", "Նշեք գործող", "Լրացրեք դաշտերը",
        "Ուղարկել", "Կցել", "Ընտրեք ֆայլ", "Գրանցվել", "Ձեր անունը",
        "Հեռախոս *", "Ծածկագիր", "Դիմել այս")

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
    h = re.sub(r"(?i)</(div|p|li|h1|h2|h3|h4|tr|section)>", "\n", h)
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

def expired(dl):
    if not dl:
        return False
    try:
        d, m, y = [int(x) for x in dl.split(".")]
        return (y, m, d) < time.gmtime(time.time() + 4 * 3600)[:3]
    except Exception:
        return False

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
        if DATE_RE.search(line) or DAYNUM_RE.match(line):
            break
        vals.append(line)
        if len(vals) >= 2:
            break
    return clean(" ".join(vals))[:120]

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
            L.append("— %s" % esc(it))
    L.append("")
    if d["deadline"]:
        L.append("⏳ Վերջնաժամկետ՝ %s" % d["deadline"])
    if d["phones"]:
        L.append("📞 Հեռ.՝ %s" % esc(", ".join(d["phones"])))
    if d["tags"]:
        L.append("")
        L.append(" ".join(d["tags"]))
    return "\n".join(L).strip()[:4000]

def tags_of(ind, n=3):
    tags = []
    for part in re.split(r"[,;]", ind or ""):
        part = clean(part)
        if part and len(tags) < n:
            tag = "#" + part.replace(" ", "_")
            if tag not in tags:
                tags.append(tag)
    return tags

def cat_of(ind):
    return clean(re.split(r"[,;]", ind)[0]) if ind else "այլ"

def works(html_text, t, title, body):
    a = len(ARM.findall(title)) * 3 + len(ARM.findall(body))
    b = len(LAT.findall(body))
    return a >= 30 and a >= b

def parse_am(html_text, path):
    t = text_of(html_text)

    title = ""
    m = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", html_text)
    if m:
        title = clean(text_of(m.group(1)))

    company = ""
    m = re.search(r'(?is)<a[^>]+href="[^"]*?/hy/company/[^"]*"[^>]*>(.*?)</a>', html_text)
    if m:
        company = clean(text_of(m.group(1)))

    start = t.find(title) if title else -1
    stop = t.find("Նկարագրություն")
    if stop < 0:
        stop = len(t)
    block = t[max(start, 0):stop] if start >= 0 else t[:stop]

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
        bare = low.lstrip("—–-•· ")
        key = next((h for h in HEADS if bare.startswith(h)), None) \
              or next((k for k in HEAD_ALIASES if bare.startswith(k)), None)
        hit = HEAD_ALIASES.get(key, key) if key else None
        if hit:
            name = "Աշխատավայրեր" if hit == "Աշխատավայրերը" else hit
            same = next((s for s in sections if s[0] == name), None)
            if same is not None:
                cur = same
            else:
                cur = [name, []]
                sections.append(cur)
            rest = clean(bare[len(key):])
            if rest and rest not in ("՝", ":"):
                cur[1].append(rest)
        elif cur is not None:
            item = clean(low.lstrip("—-•·–"))
            if not item or item in STOP_LINES:
                continue
            if item[0].islower() and cur[1]:
                cur[1][-1] = (cur[1][-1] + " " + item)[:400]
            elif len(item) > 3:
                cur[1].append(item[:400])

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
        for line in body.split("\n")[1:]:
            low = clean(line.lstrip("—-•·–"))
            if any(f in low for f in FORM):
                break
            if len(low) > 25 and ARM.search(low) and low not in plain:
                plain.append(low[:400])
            if len(plain) >= 4:
                break
        if plain:
            clean_sections = [("Նկարագրություն", plain)]

    dl = ""
    m = re.search(r"Վերջնաժամկետ", t)
    if m:
        d = DATE_RE.search(t[m.end():m.end() + 80])
        if d:
            dl = "%s.%s.%s" % (d.group(1), d.group(2), d.group(3))

    ind = field(block, "Ոլորտ")
    return {
        "id": path.rstrip("/").split("-")[-1],
        "title": title[:90],
        "company": company[:70],
        "place": field(block, "Աշխատավայր")[:70],
        "salary": re.sub(r"^Ամսական\s*", "", field(block, "Աշխատավարձ"))[:60],
        "type": field(block, "Դրույք")[:50],
        "sched": field(block, "Աշխատանքային գրաֆիկ")[:70],
        "exp": field(block, "Աշխատանքային փորձ")[:50],
        "ind": ind[:90],
        "deadline": dl,
        "phones": phones_of(html_text, t),
        "sections": clean_sections,
        "tags": tags_of(ind),
        "hy": works(html_text, t, title, body),
        "cat": cat_of(ind),
    }

def parse_jf(html_text, pid):
    t = text_of(html_text)

    title = ""
    m = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", html_text)
    if m:
        title = clean(text_of(m.group(1)))

    company = ""
    m = re.search(r'(?is)<a[^>]+href="[^"]*?/company/[^"]*"[^>]*>(.*?)</a>', html_text)
    if m:
        company = clean(text_of(m.group(1)))

    def lab(name):
        m = re.search(re.escape(name) + r"\s*`?\s*([^\n]{0,200})", t)
        return clean(m.group(1)) if m else ""

    dl = ""
    d = SLASH_DATE.search(lab("Վերջնաժամկետ"))
    if d:
        dl = "%s.%s.%s" % (d.group(1), d.group(2), d.group(3))

    ind = lab("Ոլորտ")
    place = lab("Վայրը").replace(",", ", ")

    sections, cur, started = [], None, False
    for line in t.split("\n"):
        low = clean(line)
        if not low:
            continue
        if low.startswith("Դիմել") or low.startswith("Ընկերության մասին"):
            break
        if low in JF_HEADS:
            cur = [JF_HEADS[low], []]
            sections.append(cur)
            started = True
            continue
        if not started or cur is None:
            continue
        it = clean(low.lstrip("-–•— "))
        if not it or not ARM.search(it) or len(it) < 3:
            continue
        if it[0].islower() and cur[1]:
            cur[1][-1] = (cur[1][-1] + " " + it)[:400]
        else:
            cur[1].append(it[:400])

    limits = {"Նկարագրություն": 4, "Պարտականություններ": 12,
              "Պահանջներ": 12, "Հավելյալ նշումներ": 8}
    clean_sections = []
    for head, items in sections:
        uniq = []
        for it in items:
            if it not in uniq:
                uniq.append(it)
        uniq = uniq[:limits.get(head, 8)]
        if uniq:
            clean_sections.append((head, uniq))

    return {
        "id": str(pid),
        "title": title[:90],
        "company": company[:70],
        "place": place[:70],
        "salary": lab("Աշխատավարձ")[:60],
        "type": lab("Աշխատանքի տեսակը")[:50],
        "sched": lab("Գրաֆիկ")[:70],
        "exp": lab("Փորձ")[:50],
        "ind": ind[:90],
        "deadline": dl,
        "phones": phones_of(html_text, t),
        "sections": clean_sections,
        "tags": tags_of(ind),
        "hy": works(html_text, t, title, t),
        "cat": cat_of(ind),
    }

def listing(sess):
    h = get(sess, SITEMAP)
    if not h:
        return []
    shift = 4 * 3600
    edge = time.strftime("%Y-%m-%d", time.gmtime(time.time() + shift - DAYS * 86400))
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

def jf_max_id(sess):
    h = get(sess, BASE2 + "/hy/jobs")
    ids = [int(x) for x in JF_LINK.findall(h or "")]
    return max(ids) if ids else 0

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
    cur = st.get("jf_cursor") or jf_max_id(sess)
    if not cur:
        return []
    out, tries = [], 0
    while cur > 1 and len(out) < n and tries < 40:
        cur -= 1
        tries += 1
        pid = str(cur)
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
        if not d["title"] or not d["hy"] or not d["phones"] or expired(d["deadline"]):
            st["seen"][pid] = 1
            print("  jf բաց թողնված՝", pid)
            continue
        out.append({"path": "/jobs/x/" + pid, "id": pid, "cat": d["cat"], "site": "jf",
                    "text": make_caption(d)})
        time.sleep(1)
    st["jf_cursor"] = cur
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
    if last and q[0].get("site", "am") == last:
        for i in range(1, min(len(q), 40)):
            if q[i].get("site", "am") != last:
                return i
    return 0

def publish(sess, st):
    tried = 0
    while st["queue"] and tried < TRIES_MAX:
        idx = pick(st)
        item = st["queue"][idx]
        tried += 1
        text = item.get("text") or ""

        if not text:
            base = BASE2 if item.get("site") == "jf" else BASE
            h = get(sess, base + item["path"])
            if not h:
                print("  տեքստ դեռ չկա, հերթը մնում է նույնը")
                return False
            d = parse_jf(h, item["id"]) if item.get("site") == "jf" else parse_am(h, item["path"])
            if expired(d["deadline"]) or not d["phones"] or not d["hy"]:
                st["seen"][item["id"]] = 1
                st["queue"].pop(idx)
                save(st)
                continue
            text = make_caption(d)
            item["text"] = text
            save(st)

        if send(sess, text):
            st["published"][item["id"]] = 1
            st["last_site"] = item.get("site", "am")
            st["queue"].pop(idx)
            save(st)
            print("հրապարակված՝", item["id"], item.get("site", "am"))
            return True
        print("  չուղարկվեց, մնում է հերթում")
        return False

    print("ուշադրություն՝ ոչինչ չհրապարակվեց,", tried, "փորձից հետո | հերթ:", len(st["queue"]))
    return False

def collect(sess, st):
    if len(st["queue"]) >= REFILL_AT:
        return
    turn = st.get("turn", 0)
    part = []
    for _ in range(2):
        fn = fill_am if turn % 2 == 0 else fill_jf
        part = fn(sess, st, STEP)
        st["turn"] = turn + 1
        if part:
            break
        turn += 1
        time.sleep(2)
    for it in part:
        if len(st["queue"]) >= QUEUE_MAX:
            break
        st["queue"].append(it)
    save(st)
    print("հերթագրված՝", len(part), "| հերթ:", len(st["queue"]))

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
