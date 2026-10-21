# -*- coding: utf-8 -*-
"""
job.am -> Telegram «Աշխատանք Հայաստանում | Գործ կա»

Мեկ վազք.
 1. Հրապարակում ենք հերթի գլխից (FIFO, հերթը չի փոխվում).
 2. Հավաքում ենք նորերը, հերթագրում խառը՝ բաժինները հաջորդելով.
 3. Հեռախոս/լեզու/ժամկետ չունեցողը «տեսած» է դառնում, չի հրապարակվում.
"""
import os, re, sys, json, html, time
import requests

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHANNEL = os.environ.get("JOB_CHANNEL", "")
STATE   = "state-job.json"
BASE    = "https://job.am"
MAX_PAGES  = 20
QUEUE_MAX  = 200
TRIES_MAX  = 5
HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "hy,ru;q=0.9,en;q=0.8",
}
ARM = re.compile(r"[\u0531-\u0587]")
LAT = re.compile(r"[A-Za-z]")
JOB_RE = re.compile(r"(?:https?://job\.am)?/hy/job/([a-z0-9\-]+?)-(\d+)", re.I)
DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
PHONE_RE = re.compile(r"\+374[\s\-\(\)0-9]{6,}")

LABELS = ["Աշխատավարձ", "Աշխատանքային գրաֆիկ", "Աշխատանքային փորձ",
          "Վերջնաժամկետ", "Աշխատավայր", "Դրույք", "Ոլորտ"]
HEADS = ["Նկարագրություն", "Պարտականություններ", "Պահանջներ",
         "Առավելություններ", "Հավելյալ նշումներ", "Աշխատավայրերը"]

def get(sess, url):
    try:
        r = sess.get(url, headers=HDRS, timeout=25)
        if r.status_code == 200 and len(r.text) > 2000:
            return r.text
        print("  պատասխան", r.status_code, url)
    except Exception as e:
        print("  ցանցի սխալ", type(e).__name__, url)
    return ""

def text_of(h):
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", h)
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

def field(t, label):
    m = re.search(re.escape(label), t)
    if not m:
        return ""
    tail = t[m.end():m.end() + 400]
    vals = []
    for line in tail.split("\n")[:4]:
        line = clean(line)
        if not line:
            continue
        if any(line.startswith(x) for x in LABELS) or line.startswith("Հեռ"):
            break
        vals.append(line)
        if len(vals) >= 2:
            break
    return clean(" ".join(vals))[:120]

def parse(html_text, path):
    t = text_of(html_text)

    title = ""
    m = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", html_text)
    if m:
        title = clean(text_of(m.group(1)))
    if not title:
        m = re.search(r"(?:^|\n)([^\n]{6,90})\n", t)
        title = clean(m.group(1)) if m else ""

    company = ""
    m = re.search(r'(?is)<a[^>]+href="[^"]*?/hy/company/[^"]*"[^>]*>(.*?)</a>', html_text)
    if m:
        company = clean(text_of(m.group(1)))

    body_start = t.find("Նկարագրություն")
    body_end = t.find("Ընկերության մասին")
    body = t[body_start:body_end] if body_start >= 0 and body_end > body_start else t

    sections, cur = [], None
    for line in body.split("\n"):
        low = clean(line)
        hit = next((h for h in HEADS if low.startswith(h)), None)
        if hit:
            name = "Աշխատավայրեր" if hit == "Աշխատավայրերը" else hit
            cur = [name, []]
            sections.append(cur)
            rest = clean(low[len(hit):])
            if rest and rest not in ("՝", ":"):
                cur[1].append(rest)
        elif cur is not None:
            item = clean(line.lstrip("—-•·"))
            if item and item != "Դիմել" and len(item) > 1:
                cur[1].append(item[:400])

    limits = {"Նկարագրություն": 3, "Պարտականություններ": 12, "Պահանջներ": 12,
              "Առավելություններ": 10, "Հավելյալ նշումներ": 8, "Աշխատավայրեր": 4}
    clean_sections = []
    for head, items in sections:
        uniq = []
        for it in items:
            if it not in uniq:
                uniq.append(it)
        uniq = uniq[:limits.get(head, 8)]
        if uniq:
            clean_sections.append((head, uniq))

    salary = re.sub(r"^Ամսական\s*", "", field(t, "Աշխատավարձ"))

    dl = ""
    m = re.search(r"Վերջնաժամկետ[^\n]{0,40}", t)
    if m:
        d = DATE_RE.search(t[m.start():m.start() + 80])
        if d:
            dl = "%s.%s.%s" % (d.group(1), d.group(2), d.group(3))

    phones = []
    for p in PHONE_RE.findall(t):
        p = " ".join(p.split()).strip(" -")
        if len(re.sub(r"\D", "", p)) >= 9 and p not in phones:
            phones.append(p[:24])
    phones = phones[:2]

    ind = field(t, "Ոլորտ")
    tags = []
    for part in re.split(r"[,;]", ind):
        part = clean(part)
        if part and len(tags) < 3:
            tag = "#" + part.replace(" ", "_")
            if tag not in tags:
                tags.append(tag)

    armenian = len(ARM.findall(title)) * 3 + len(ARM.findall(body))
    latin = len(LAT.findall(body))
    hy = armenian >= 30 and armenian >= latin

    return {
        "id": path.rstrip("/").split("-")[-1],
        "path": path,
        "title": title[:90],
        "company": company[:70],
        "place": field(t, "Աշխատավայր")[:70],
        "salary": salary[:60],
        "type": field(t, "Դրույք")[:50],
        "sched": field(t, "Աշխատանքային գրաֆիկ")[:70],
        "exp": field(t, "Աշխատանքային փորձ")[:50],
        "ind": ind[:90],
        "deadline": dl,
        "phones": phones,
        "sections": clean_sections,
        "tags": tags,
        "hy": hy,
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
    L.append("📞 Հեռ.՝ %s" % esc(", ".join(d["phones"])))
    if d["tags"]:
        L.append("")
        L.append(" ".join(d["tags"]))
    return "\n".join(L).strip()[:4000]

def listing(sess):
    order, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        url = BASE + "/hy/jobs" + ("" if page == 1 else "?page=%d" % page)
        h = get(sess, url)
        if not h:
            break
        found = 0
        for m in JOB_RE.finditer(h):
            slug, jid = m.group(1), m.group(2)
            path = "/hy/job/%s-%s" % (slug, jid)
            if path in seen:
                continue
            seen.add(path)
            order.append(path)
            found += 1
        print("էջ %d՝ %d նոր հասցե" % (page, found))
        if not found:
            break
        time.sleep(1)
    print("ընդամենը ցուցակում:", len(order))
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
    for path in listing(sess):
        jid = path.rstrip("/").split("-")[-1]
        if jid in st["seen"] or jid in st["published"]:
            continue
        h = get(sess, BASE + path)
        if not h:
            continue
        d = parse(h, path)
        if not d["hy"] or not d["phones"] or expired(d["deadline"]) or not d["title"]:
            st["seen"][d["id"]] = 1
            print("  բաց թողնված՝", d["id"])
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
    print("հերթագրուած:", added, "| հերթ:", len(st["queue"]))

def main():
    st = load()
    sess = requests.Session()
    mode = sys.argv[1].strip().lower() if len(sys.argv) > 1 else ""

    if mode == "test":
        for path in listing(sess)[:3]:
            h = get(sess, BASE + path)
            if h:
                print("-" * 40)
                print(make_caption(parse(h, path)))
        return

    publish(sess, st)
    collect(sess, st)
    print("վերջ. հերթում մնացած՝", len(st["queue"]))

if __name__ == "__main__":
    main()
