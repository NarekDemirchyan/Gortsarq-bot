# -*- coding: utf-8 -*-
"""
Общий модуль: чтение страниц, перевод и общая тетрадь публикаций.

Чтение в три ступени (что первое ответит — то и берём):
  1) r.jina.ai, движок browser — отдаёт markdown, под который написаны парсеры;
  2) прямой requests с браузерными заголовками — HTML переводим в тот же markdown;
  3) открытые прокси (allorigins, codetabs) — тоже HTML -> markdown.
Плюс sitecustomize.py: если запрос получил отказ защиты, он повторяется через
cloudscraper — это ещё одна ступень, включается сама.

Перевод: только армянский текст, цепочкой google -> clients5 -> mymemory.
Google из дата-центра часто отвечает страницей проверки вместо данных, поэтому
тот же запрос повторяется через прокси — оттуда он проходит.

После перевода текст правится в два приёма:
  _polish — словарь деловых слов (GLOSS): по армянскому оригиналу меняем корявый
            русский вариант на правильный («открыта» -> «раскладная»);
  _title  — правила для коротких названий (TITLE_RULES): в армянском определение
            стоит после слова, в русском перед ним, поэтому «Часы висят на стене»
            становится «Настенные часы».
Все переводчики отказали — публикуем оригинал. Исключений модуль не бросает.

published.json — общая тетрадь всех трёх сайтов: "list-22090629", "ivi-24081",
"gorts-2929". Проверка и запись — led_has() / led_add().

Знаки «<» и «>» в разборе HTML набраны кодом символа (LT/GT): в переписке такие
сочетания иногда съедаются, и регэксп остаётся пустым.
"""
import re
import json
import time
import html as htmllib
import urllib.parse as urlparse

LT = chr(60)
GT = chr(62)

JINA = "https://r.jina.ai/"
LEDGER = "published.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HDRS = {
    "User-Agent": UA,
    "Accept-Language": "ru,hy;q=0.9,en;q=0.8",
}

PROXIES = ("https://api.allorigins.win/raw", "https://api.codetabs.com/v1/proxy")

BLOCKED = ("just a moment", "attention required", "enable javascript",
           "checking your browser", "cloudflare", "performing security verification",
           "captcha", "are you a human")

HY_RE  = re.compile(r"[\u0530-\u058F]")
CYR_RE = re.compile(r"[А-Яа-яЁё]")
LAT_RE = re.compile(r"[A-Za-z]")

# армянский, набранный латиницей: «vacharum em heraxos» — такое не публикуем
TRANSLIT_HY = ("vachar", "vacharv", "vacharum", "vacharq", "vcharjum", "vjarjum",
               "himnakanum", "hnaravor", "ogtagvats", "artadr", "andznakan",
               "heraxos", "axos", "nyuter", "nver", "giny", "gumar", "deq",
               "tuft", "pahpan", "hatov", "shukay", "vernagir", "tesak", "guyn",
               "em", "es", "e", "en", "its", "ic", "ev", "kam", "vor", "ayn",
               "ayl", "shat", "lav", "voch", "nor", "erb", "inch", "mek",
               "inchpes", "vorpes", "mijoc")

def is_blocked(text):
    t = (text or "").strip()
    if len(t) < 800:
        return True
    low = t.lower()
    return any(m in low for m in BLOCKED)

def has_hy(s):
    return bool(HY_RE.search(s or ""))

def has_cyr(s):
    return bool(CYR_RE.search(s or ""))

def has_lat(s):
    return bool(LAT_RE.search(s or ""))

def script(s):
    """hy — армянский, ru — кириллица, lat — латиница, none — ни одного."""
    if has_hy(s):
        return "hy"
    if has_cyr(s):
        return "ru"
    if has_lat(s):
        return "lat"
    return "none"

def is_translit_hy(t):
    """Армянское слово латиницей («vacharum em heraxos») — не публикуем.
    Латинский бренд («Adidas Lightblaze») под правило не попадает."""
    t = (t or "").lower()
    if not t or has_hy(t) or has_cyr(t):
        return False
    words = re.findall(r"[a-z']{2,}", t)
    if not words:
        return False
    hit = 0
    for w in words:
        for m in TRANSLIT_HY:
            if len(m) <= 2:
                if w == m:
                    hit += 1
                    break
            elif w.startswith(m) or (len(m) >= 4 and m in w):
                hit += 1
                break
    if hit == 0:
        return False
    if hit >= 2 and hit / len(words) >= 0.3:
        return True
    return len(words) >= 3 and hit / len(words) >= 0.4

# ---------- HTML -> тот же markdown, что отдаёт jina ----------

def _attr(tag, name):
    m = re.search(r"\b" + name + r"\s*=\s*[\"']([^\"']*)[\"']", tag or "", re.I)
    return m.group(1).strip() if m else ""

def _abs(u, base):
    u = htmllib.unescape(u or "").strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return (base or "").rstrip("/") + u
    return u

def _plain(h):
    return " ".join(htmllib.unescape(re.sub(LT + r"[^" + GT + r"]+" + GT, " ", h or "")).split())

def to_md(h, base=""):
    h = re.sub(r"(?is)" + LT + r"(script|style|noscript)[^" + GT + r"]*" + GT +
               r".*?" + LT + r"/\1" + GT, " ", h or "")
    h = re.sub(r"(?is)" + LT + r"head[^" + GT + r"]*" + GT +
               r".*?" + LT + r"/head" + GT, " ", h)

    def _img(m):
        tag = m.group(0)
        src = (_attr(tag, "src") or _attr(tag, "data-src") or
               _attr(tag, "data-original") or _attr(tag, "data-lazy"))
        if not src or src.startswith("data:"):
            return " "
        return "\n![%s](%s)\n" % (_attr(tag, "alt"), _abs(src, base))

    def _a(m):
        tag, text = m.group(0), _plain(m.group(1))
        href = _attr(tag, "href")
        return "[%s](%s)" % (text, _abs(href, base)) if href else text

    h = re.sub(r"(?is)" + LT + r"img[^" + GT + r"]*" + GT, _img, h)
    h = re.sub(r"(?is)" + LT + r"a[^" + GT + r"]*" + GT + r"(.*?)" + LT + r"/a" + GT, _a, h)
    h = re.sub(r"(?is)" + LT + r"h([1-6])[^" + GT + r"]*" + GT + r"(.*?)" + LT + r"/h\1" + GT,
               lambda m: "\n\n%s %s\n\n" % ("#" * int(m.group(1)), _plain(m.group(2))), h)
    h = re.sub(r"(?i)" + LT + r"br\s*/?" + GT, "\n", h)
    h = re.sub(r"(?i)" + LT + r"/p" + GT, "\n", h)
    h = re.sub(LT + r"[^" + GT + r"]+" + GT, " ", h)
    h = htmllib.unescape(h)
    h = re.sub(r"[ \t\u00a0]+", " ", h)
    h = re.sub(r"\n[ \t]+", "\n", h)
    h = re.sub(r"\n{3,}", "\n\n", h)
    return h.strip()

# ---------- чтение ----------

def jina(sess, url, extra=None, tries=2):
    h = dict(HDRS)
    h["x-engine"] = "browser"
    if extra:
        h.update(extra)

    for attempt in range(tries):
        try:
            r = sess.get(JINA + url, headers=h, timeout=90)
            if r.status_code == 200 and not is_blocked(r.text):
                return r.text
            print(f"  jina {r.status_code}, {len(r.text)} знаков")
        except Exception as e:
            print("  jina ошибка:", type(e).__name__)
        time.sleep(2 + 2 * attempt)

    try:
        r = sess.get(url, headers=HDRS, timeout=60)
        if r.status_code == 200 and len(r.text) > 1500 and not is_blocked(r.text):
            print("  прямой вход")
            return to_md(r.text, url)
        print(f"  прямой {r.status_code}, {len(r.text)} знаков")
    except Exception as e:
        print("  прямой ошибка:", type(e).__name__)

    for p in PROXIES:
        try:
            r = sess.get(p, params={"url": url}, headers=HDRS, timeout=90)
            if r.status_code == 200 and len(r.text) > 1500:
                print("  через прокси")
                return to_md(r.text, url)
            print(f"  прокси {r.status_code}, {len(r.text)} знаков")
        except Exception as e:
            print("  прокси ошибка:", type(e).__name__)

    print("  чтение не удалось:", url)
    return ""

# ---------- перевод ----------

def _json_get(sess, url, params, timeout=30):
    """Забираем JSON. Напрямую из дата-центра часто приходит страница проверки,
    поэтому тот же адрес повторяем через прокси — оттуда проходит."""
    try:
        r = sess.get(url, params=params, timeout=timeout)
        if r.status_code == 200:
            try:
                return r.json()
            except Exception:
                print("  перевод: ответ не JSON, идём через прокси")
        else:
            print(f"  перевод: {r.status_code} напрямую")
    except Exception as e:
        print("  перевод напрямую ошибка:", type(e).__name__)

    full = url + "?" + urlparse.urlencode(params)
    for p in PROXIES:
        try:
            r = sess.get(p, params={"url": full}, timeout=timeout)
            if r.status_code != 200:
                print(f"  перевод прокси {r.status_code}")
                continue
            try:
                print("  перевод: получен через прокси")
                return r.json()
            except Exception:
                txt = r.text.strip()
                i = txt.find("{")
                if i > 0:
                    txt = txt[i:]
                try:
                    return json.loads(txt)
                except Exception:
                    print("  перевод прокси: не JSON")
        except Exception as e:
            print("  перевод прокси ошибка:", type(e).__name__)
    return None

def _google(sess, text):
    d = _json_get(sess, "https://translate.googleapis.com/translate_a/single",
                  {"client": "gtx", "sl": "hy", "tl": "ru",
                   "dt": "t", "q": text[:1800]})
    if not d:
        return ""
    try:
        return "".join(p[0] for p in d[0] if p and p[0]).strip()
    except Exception:
        return ""

def _clients5(sess, text):
    d = _json_get(sess, "https://clients5.google.com/translate_a/t",
                  {"client": "dict-chrome-ex", "sl": "hy", "tl": "ru",
                   "q": text[:1800]})
    if isinstance(d, list) and d:
        if isinstance(d[0], list):
            return "".join(x[0] for x in d[0] if x and x[0]).strip()
        if isinstance(d[0], str):
            return "".join(d).strip()
    if isinstance(d, dict):
        sents = d.get("sentences")
        if sents:
            return "".join(s.get("trans", "") for s in sents).strip()
        return (d.get("translation") or "").strip()
    return ""

def _mymemory(sess, text):
    d = _json_get(sess, "https://api.mymemory.translated.net/get",
                  {"q": text[:450], "langpair": "hy|ru"})
    if not isinstance(d, dict):
        return ""
    return ((d.get("responseData") or {}).get("translatedText") or "").strip()

CHAIN = (("google", _google), ("clients5", _clients5), ("mymemory", _mymemory))

# Словарь деловых слов: армянское слово, как его обычно переводит машина
# и как правильно по-русски.
GLOSS = (
    ("բացովի",      ("открыта", "открытая", "открытый", "открывающаяся"), "раскладная"),
    ("զարդատուփ",   ("шкатулка для драгоценностей", "коробка для драгоценностей"), "шкатулка для украшений"),
    ("վաճառվում",   ("продается",), "продаётся"),
    ("օգտագործված", ("используется", "использованная", "использованный"), "б/у"),
    ("չափս",        ("измерение", "размерность", "мера"), "размер"),
    ("վիճակ",       ("положение",), "состояние"),
    ("անվճար",      ("свободный", "свободная", "свободно"), "бесплатно"),
    ("պայուսակ",    ("мешок", "сумка-мешок"), "сумка"),
    ("ականջակալ",   ("наушник",), "наушники"),
    ("մեջը",        ("в нем", "в нём", "в ней"), "внутри"),
)

# обычные опечатки, которые проскакивают в русском тексте
RU_FIX = (("гупка", "губка"), ("гупки", "губки"), ("запчаст", "запчасть"))

def _low(s):
    return s[:1].lower() + s[1:] if s else s

# Названия: в армянском определение после слова, в русском — перед ним.
TITLE_RULES = (
    (r"(?i)^(\S+)\s+висят\s+на\s+стене[.!]?$",  lambda m: "Настенные " + _low(m.group(1))),
    (r"(?i)^(\S+)\s+висит\s+на\s+стене[.!]?$",  lambda m: "Настенный " + _low(m.group(1))),
    (r"(?i)^(\S+)\s+раскладная[.!]?$",          lambda m: "Раскладная " + _low(m.group(1))),
    (r"(?i)^(\S+)\s+раскладной[.!]?$",          lambda m: "Раскладной " + _low(m.group(1))),
    (r"(?i)^прода(?:[её]тся|ются|ю)\s+(.+)$",   lambda m: m.group(1)),
)

def _polish(hy, ru):
    """Правка живого языка: смотрим армянский оригинал и заменяем корявые
    русские варианты на деловые."""
    out = ru
    low = (hy or "").lower()
    for word, bads, good in GLOSS:
        if word.lower() not in low:
            continue
        for bad in bads:
            if bad.lower() == good.lower():
                continue
            out = re.sub(r"(?i)\b" + re.escape(bad) + r"\b", good, out)
    for bad, good in RU_FIX:
        out = re.sub(r"(?i)\b" + re.escape(bad) + r"\b", good, out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out).strip(" \t-—")
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    return out

def _title(ru):
    """Короткое название: ставим слова в русском порядке и убираем лишнее."""
    out = (ru or "").strip()
    if not out:
        return out
    for pat, rep in TITLE_RULES:
        out = re.sub(pat, rep, out).strip()
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    return out

def translate(sess, text):
    """Переводим только армянский. Все отказали — публикуем оригинал."""
    text = (text or "").strip()
    if not text or not has_hy(text):
        return text
    for name, fn in CHAIN:
        if name == "mymemory" and len(text) > 450:
            continue
        try:
            res = fn(sess, text)
        except Exception as e:
            print(f"  перевод {name} недоступен:", type(e).__name__)
            res = ""
        if res and has_cyr(res):
            res = _polish(text, res)
            if len(text) <= 80:
                res = _title(res)
            return res
        if res:
            print(f"  перевод {name} без кириллицы, пробуем дальше")
        time.sleep(1)
    print("  перевод не вышел — публикуем оригинал")
    return text

# ---------- общая тетрадь публикаций ----------

_LED = None

def ledger():
    global _LED
    if _LED is None:
        try:
            _LED = json.load(open(LEDGER, encoding="utf-8"))
        except Exception:
            _LED = {}
    return _LED

def ledger_save():
    try:
        json.dump(ledger(), open(LEDGER, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception as e:
        print("  тетрадь не сохранилась:", type(e).__name__)

def led_key(site, iid):
    return f"{site}-{str(iid).strip()}"

def led_has(site, iid):
    return led_key(site, iid) in ledger()

def led_add(site, iid, ts=None):
    d = ledger()
    d[led_key(site, iid)] = int(ts or time.time())
    if len(d) > 60000:
        keep = sorted(d.items(), key=lambda kv: kv[1])[-40000:]
        globals()["_LED"] = dict(keep)
    ledger_save()
