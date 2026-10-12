# -*- coding: utf-8 -*-
"""
Этот файл бот загружает первым (см. bot.yml), до остальной работы.
Четыре задачи.

1. Ключ Jina. Читалка r.jina.ai в браузерном режиме открывает страницы,
   которые без ключа отдают проверку защиты. Ключ лежит в секрете JINA_KEY
   и подставляется в каждый запрос к r.jina.ai.

2. Повтор чтения. list.am то пускает читалку, то закрывает её защитой.
   Если ответ слабый — ошибка, проверка защиты или почти пустой текст —
   запрос повторяется до трёх раз с паузой.

3. Чистка ivi.am. На страницах ivi.am вместе с текстом лежат скрипты
   (счётчики, реклама). Бот убирает теги, но текст внутри script остаётся
   и попадает в описание объявления. Здесь код и стили вырезаются
   из ответа ещё до разбора, а адреса фотографий объявления собираются
   из ответа до вырезания и возвращаются в начало — иначе они пропадают.

4. Альбом без повторов. list.am отдаёт одну фотографию в разных размерах,
   бот собирает галерею по имени файла и берёт обе. Здесь альбом
   пересобирается по номеру фотографии: повтор убирается, из вариантов
   остаётся полный (папка f). Если остаётся один снимок, альбом отправлять
   нельзя — Telegram принимает альбом от двух фото, поэтому снимок уходит
   обычным сообщением, а боту возвращается ответ об успехе: он продолжает
   как обычно и добавляет «Связаться с продавцом» и кнопку.

Всё обёрнуто в try: не вышло — запрос уходит как есть.
"""
import json
import os
import re
import time

try:
    import requests
except Exception:
    requests = None

JINA = "https://r.jina.ai/"
KEY = os.environ.get("JINA_KEY", "")
PAGES = ("list.am", "gortsarq")
CLEAN_HOSTS = ("ivi.am",)
BLOCK_WORDS = ("security verification", "just a moment", "cf-chl",
               "attention required", "captcha", "проверка безопасности")
SCRIPT_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1\s*>", re.S | re.I)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
ITEM_IMG_RE = re.compile(
    r"(?:https?:(?:\\?/){2}ivi\.am)?(?:\\?/)+files(?:\\?/)+images(?:\\?/)+items"
    r"(?:\\?/)+[^\s\"'<>]+?\.(?:jpg|jpeg|png|webp)", re.I)
TRIES = 3

# ---------- ключ Jina ----------

def _with_key(url, headers):
    if not KEY or not str(url).startswith(JINA):
        return headers
    h = dict(headers or {})
    h.setdefault("Authorization", "Bearer " + KEY)
    return h

# ---------- повтор чтения ----------

def _weak(r):
    """Ответ читалки слабый: ошибка, проверка защиты или пустое тело."""
    try:
        code = getattr(r, "status_code", 0)
        text = getattr(r, "text", "") or ""
    except Exception:
        return False
    if code != 200:
        return True
    if len(text) < 1500:
        return True
    low = text[:4000].lower()
    return any(w in low for w in BLOCK_WORDS)

def _read(sess, old, url, kw):
    last = None
    for n in range(TRIES):
        last = old(sess, url, **kw)
        if not _weak(last):
            if n:
                print("  джина: прочитано со", n + 1, "раза")
            return last
        print("  джина: слабый ответ", getattr(last, "status_code", "?"),
              "-> повтор", n + 1)
        if n < TRIES - 1:
            time.sleep(4 + 4 * n)
    return last

# ---------- чистка ivi.am ----------

def _photos(body, url):
    """Адреса фотографий объявления: сначала по номеру объявления."""
    out = []
    tail = url.split("/ru/item/", 1)[1] if "/ru/item/" in url else ""
    nums = re.findall(r"\d{4,}", tail)
    iid = nums[-1] if nums else ""
    for m in ITEM_IMG_RE.finditer(body):
        u = m.group(0).replace("\\/", "/")
        if u.startswith("/"):
            u = "https://ivi.am" + u
        if u in out:
            continue
        if iid and iid in u:
            out.append(u)
    if not out:
        for m in ITEM_IMG_RE.finditer(body[:60000]):
            u = m.group(0).replace("\\/", "/")
            if u.startswith("/"):
                u = "https://ivi.am" + u
            if u not in out:
                out.append(u)
            if len(out) >= 6:
                break
    return out

def _trim(r, url=""):
    """Убрать код и стили, но сохранить фотографии объявления."""
    try:
        body = getattr(r, "text", "") or ""
        if len(body) < 3000 or "script" not in body.lower():
            return r
        saved = _photos(body, url) if "/ru/item/" in url else []
        clean = SCRIPT_RE.sub(" ", body)
        clean = COMMENT_RE.sub(" ", clean)
        if len(clean) < 2000:
            return r
        if saved:
            clean = " ".join('<img src="%s">' % u for u in saved[:10]) + " " + clean
        r._content = clean.encode("utf-8", "replace")
        print("  ivi: код убран, знаков", len(body), "->", len(clean),
              "| фото", len(saved))
    except Exception as e:
        print("  ivi: чистка не вышла:", type(e).__name__)
    return r

# ---------- альбом без повторов ----------

def _key(u):
    """Номер фотографии: любой из .../f/220/99993220.webp и его вариантов -> 99993220"""
    u = (u or "").split("?")[0]
    name = u.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    m = re.search(r"\d{5,}", name)
    return m.group(0) if m else name or u

def _full(u):
    return "/f/" in (u or "")

def _clean(media):
    """По одной фотографии на номер, из вариантов — полный размер."""
    out, seen = [], {}
    for item in media:
        if not isinstance(item, dict) or item.get("type") != "photo":
            out.append(item)
            continue
        url = item.get("media") or ""
        k = _key(url)
        if k not in seen:
            seen[k] = len(out)
            out.append(item)
            continue
        i = seen[k]
        if _full(url) and not _full(out[i].get("media")):
            keep = out[i].get("caption") or item.get("caption")
            out[i] = dict(item, caption=keep)
    return out

def _names(items):
    out = []
    for it in items:
        if isinstance(it, dict):
            u = str(it.get("media") or "")
            out.append(u.rsplit("/", 1)[-1] or u)
    return ", ".join(out)

class _Answer(object):
    """Ответ, который видит бот, когда снимок ушёл обычным сообщением."""

    def init(self, code, text):
        self.status_code = code
        self.text = text

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        return json.loads(self.text)

def _one_photo(sess, old, url, data, one, timeout):
    place = url.replace("sendMediaGroup", "sendPhoto")
    payload = {"chat_id": (data or {}).get("chat_id"),
               "photo": one.get("media"),
               "caption": one.get("caption") or ""}
    r = old(sess, place, data=payload, timeout=timeout)
    print("  альбом: один снимок -> sendPhoto", r.status_code)
    if not r.ok:
        return r
    mid = None
    try:
        res = r.json().get("result") or {}
        if isinstance(res, dict):
            mid = res.get("message_id")
    except Exception:
        mid = None
    body = json.dumps({"ok": True, "result": [{"message_id": mid}]})
    return _Answer(200, body)

def _patch():
    if requests is None:
        return
    old_get = requests.Session.get
    old_post = requests.Session.post

    def get(self, url, **kw):
        try:
            kw["headers"] = _with_key(url, kw.get("headers"))
        except Exception:
            pass
        try:
            if str(url).startswith(JINA) and any(p in str(url) for p in PAGES):
                r = _read(self, old_get, url, kw)
            else:
                r = old_get(self, url, **kw)
        except Exception as e:
            print("  чтение: сбой повтора:", type(e).__name__)
            r = old_get(self, url, **kw)
        if any(h in str(url) for h in CLEAN_HOSTS):
            r = _trim(r, str(url))
        return r

    def post(self, url, data=None, json=None, **kw):
        if url and "sendMediaGroup" in str(url):
            try:
                raw = (data or {}).get("media")
                items = json.loads(raw) if isinstance(raw, str) else None
                if isinstance(items, list):
                    print("  альбом: фото", len(items), "->", _names(items))
                    fixed = _clean(items)
                    if len(fixed) == 1:
                        return _one_photo(self, old_post, url, data, fixed[0],
                                          kw.get("timeout", 60))
                    if len(fixed) != len(items):
                        print("  альбом: убрано повторов", len(items) - len(fixed))
                        data = dict(data, media=json.dumps(fixed, ensure_ascii=False))
            except Exception as e:
                print("  альбом: правка не вышла:", type(e).__name__)
        return old_post(self, url, data=data, json=json, **kw)

    requests.Session.get = get
    requests.Session.post = post

_patch()
print("подготовка: ключ Jina", "есть" if KEY else "нет")
