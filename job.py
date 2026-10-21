# -*- coding: utf-8 -*-
"""
job.am -> Telegram «Աշխատանք Հայաստանում | Գործ կա»
"""
import os, re, sys, json, html, time
import requests

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHANNEL = os.environ.get("JOB_CHANNEL", "")
STATE   = "state-job.json"
BASE    = "https://job.am"
SITEMAP = "https://job.am/sitemap/jobs.xml"
DAYS    = 30
QUEUE_MAX = 200
TRIES_MAX = 5

HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "hy,ru;q=0.9,en;q=0.8",
}

ARM = re.compile(r"[\u0531-\u0587]")
LAT = re.compile(r"[A-Za-z]")
SITE_RE = re.compile(r"<loc>\s*(https?://job\.am/hy/job/[^<\s]+?)\s*</loc>"
                     r"\s*<lastmod>\s*(\d{4}-\d{2}-\d{2})", re.I)
DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
PHONE_RE = re.compile(r"(?:\+?374|\b0)[\s\-\(\)0-9]{7,18}")
TEL_RE = re.compile(r"""tel:([+\d][\d\s\-\(\)]{6,20})""", re.I)

FIELDS = ["Աշխատավայր", "Աշխատավարձ", "Դրույք", "Աշխատանքային գրաֆիկ",
          "Աշխատանքային փորձ", "Ոլորտ", "Վերջնաժամկետ"]
HEADS = ["Նկարագրություն", "Պարտականություններ", "Պահանջներ",
         "Առավելություններ", "Նպաստավորություններ", "Հավելյալ նշումներ",
         "Աշխատավայրերը"]
STOP_LINES = ("Ամբողջությամբ կարդալ", "Տեսնել ավելին", "Դիմել", "Կարդալ ավելին")
STOP_WORDS = ("Ընկերության մասին", "Տարածեք հայտարարությունը")
FORM = ("Անուն Ազգանուն", "Էլ. հասցե", "Նշեք գործող", "Լրացրեք դաշտերը",
        "Ուղարկել", "Կցել", "Ընտրեք ֆայլ", "Գրանցվել", "Ձեր անունը",
        "Հեռախոս *", "Ծածկագիր", "Դիմել այս")

def get(sess, url):
    try:
        r = sess.get(url, headers=HDRS, timeout=30)
        if r.status_code == 200 and len(r.text) > 2000:
            return r.text
        print("  պատասխան", r.status_code, url)
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
        vals.append(line)
        if len(vals) >= 2:
            break
    return clean(" ".join(vals))[:120]

def phones_of(html_text, t):
    raw = [m.group(1) for m in TEL_RE.finditer(html_text)]
    raw += [m.group(0) for m in PHONE_RE.finditer(t)]
    res = []
    for p in raw:
        digits = re.sub(r"\D", "", p)
        if len(digits) < 9 or len(digits) > 15:
            continue
        if digits.startswith("374") and len(digits) == 11:
            p = "+374 %s %s" % (digits[3:5], digits[5:])
        elif digits.startswith("0") and len(digits) == 9:
            p = "+374 %s %s" % (digits[1:3], digits[3:])
        else:
            p = "+" + digits
        if p not in res:
            res.append(p)
    return res[:2]

def parse(html_text, path):
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

    sections, cur, form = [], None, False
    for line in body.split("\n"):
        low = clean(line)
        if not low or low in STOP_LINES:
            continue
        if any(f in low for f in FORM):
            break
        hit = next((h for h in HEADS if low.startswith(h)), None)
        if hit:
            name = "Աշխատավայրեր" if hit == "Աշխատավայրերը" else hit
            cur = [name, []]
            sections.append(cur)
            rest = clean(low[len(hit):])
            if rest and rest not in ("՝", ":"):
                cur[1].append(rest)
        elif cur is not None:
            item = clean(low.lstrip("—-•·–"))
            if item and len(item) > 1 and item not in STOP_LINES:
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

    # պահուստ. եթե բաժիններ չկան, վերցնում ենք առաջին պարբերությունները
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

    salary = re.sub(r"^Ամսական\s*", "", field(block, "Աշխատավարձ"))

    dl = ""
    m = re.search(r"Վերջնաժամկետ", t)
    if m:
        d = DATE_RE.search(t[m.end():m.end() + 80])
        if d:
            dl = "%s.%s.%s" % (d.group(1), d.group(2), d.group(3))

    phones = phones_of(html_text, t)
    ind = field(block, "Ոլորտ")

    tags = []
    for part in re.split(r"[,;]", ind):
        part = clean(part)
        if part and len(tags) < 3:
            tag = "#" + part.replace(" ", "_")
            if tag not in tags:
                tags.append(tag)

    armenian = len(ARM.findall(title)) * 3 + len(ARM.findall(body))
    latin = len(LAT.findall(body))

    return {
        "id": path.rstrip("/").split("-")[-1],
        "path": path,
        "title": title[:90],
        "company": company[:70],
        "place": field(block, "Աշխատավայր")[:70],
        "salary": salary[:60],
        "type": field(block, "Դրույք")[:50],
        "sched": field(block, "Աշխատանքային գրաֆիկ")[:70],
        "exp": field(block, "Աշխատանքային փորձ")[:50],
        "ind": ind[:90],
        "deadline": dl,
        "phones": phones,
        "sections": clean_sections,
        "tags": tags,
        "hy": armenian >= 30 and armenian >= latin,
        "cat": clean(re.split(r"[,;]", ind)[0]) if ind else "այլ",
    }

def expired(dl):
    if not dl:
        return False
    try:
        d, m, y = [int(x) for x in dl.split(".")]
        return (y, m, d) < time.gmtime(time.time() + 4 * 3600)[:3]
    except Exception:
        return False

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
        L.append("▪️ <b>%s</b>" % esc(head))
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

def listing(sess):
    """Все вакансии из sitemap за последние DAYS дней, новые первыми."""
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
    print("sitemap՝", len(order), "հայտարարություն վերջին", DAYS, "օրում")
    return order

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

def publish(sess, st):
    tried = 0
    while st["queue"] and tried < TRIES_MAX:
        item = st["queue"][0]
        tried += 1
        h = get(sess, BASE + item["path"])
        if not h:
            print("  էջը չբացվեց, թողնում ենք հերթում")
            return False
        d = parse(h, item["path"])
        bad = []
        if d["id"] in st["published"]:
            bad.append("կրկնություն")
        if expired(d["deadline"]):
            bad.append("ժամկետ")
        if not d["phones"]:
            bad.append("հեռախոս")
        if not d["hy"]:
            bad.append("լեզու")
        if bad:
            print("  բաց թողնված՝", d["id"], ",".join(bad))
            st["seen"][d["id"]] = 1
            st["queue"].pop(0)
            save(st)
            continue
        if send(sess, make_caption(d)):
            st["published"][d["id"]] = 1
            st["queue"].pop(0)
            save(st)
            print("հրապարակված՝", d["id"], d["title"])
            return True
        return False
    return False

def collect(sess, st):
    groups, order = {}, []
    for path in ([] if len(st["queue"]) >= QUEUE_MAX else listing(sess)):
        jid = path.rstrip("/").split("-")[-1]
        if jid in st["seen"] or jid in st["published"]:
            continue
        h = get(sess, BASE + path)
        if not h:
            continue
        d = parse(h, path)
        if not d["hy"] or not d["phones"] or expired(d["deadline"]) or not d["title"]:
            st["seen"][d["id"]] = 1
            print("  բաց թողնված՝", d["id"],
                  "լեզու" if not d["hy"] else "",
                  "հեռախոս" if not d["phones"] else "",
                  "ժամկետ" if expired(d["deadline"]) else "")
            continue
        cat = d["cat"] or "այլ"
        if cat not in groups:
            groups[cat] = []
            order.append(cat)
        groups[cat].append({"path": path, "id": d["id"], "cat": cat})
        if sum(len(v) for v in groups.values()) >= QUEUE_MAX:
            break

    mix = []
    while any(groups.values()):
        for cat in order:
            if groups[cat]:
                mix.append(groups[cat].pop(0))

    tail_cat = st["queue"][-1]["cat"] if st["queue"] else ""
    if tail_cat and len(mix) > 1 and mix[0]["cat"] == tail_cat:
        swap = next((i for i, x in enumerate(mix) if x["cat"] != tail_cat), 0)
        mix[0], mix[swap] = mix[swap], mix[0]

    added = 0
    for it in mix:
        if len(st["queue"]) >= QUEUE_MAX:
            break
        st["queue"].append(it)
        added += 1
    save(st)
    print("հերթագրված՝", added, "| հերթ:", len(st["queue"]))

def main():
    st = load()
    sess = requests.Session()
    mode = sys.argv[1].strip().lower() if len(sys.argv) > 1 else ""

    if mode == "test":
        paths = listing(sess)[:3]
        for path in paths:
            h = get(sess, BASE + path)
            if not h:
                continue
            d = parse(h, path)
            print("-" * 40)
            print("հեռախոս՝", d["phones"], "| լեզու՝", d["hy"],
                  "| ժամկետ՝", d["deadline"], "| բաժին՝", d["cat"],
                  "| բաժիններ՝", [x[0] for x in d["sections"]])
            print(make_caption(d))
        return

    collect(sess, st)
    publish(sess, st)
    print("վերջ. հերթում՝", len(st["queue"]))

if __name__ == "__main__":
    main()
