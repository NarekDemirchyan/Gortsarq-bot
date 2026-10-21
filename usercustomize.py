# -*- coding: utf-8 -*-
"""
Этот файл бот загружает первым (см. bot.yml), до остальной работы.

1. Ключ Jina: если секрет JINA_KEY есть, он подставляется в запросы
   к r.jina.ai.
2. Повтор чтения: слабый ответ повторяется, а последняя неудача больше
   не поднимает исключение наружу — страница уходит в браузер.
3. Браузер: chromium из playwright проходит проверку «Checking your
   browser». Открывается один раз на весь запуск и переиспользуется.
   Картинки ждём только на странице объявления: в списке категорий их
   нет, и ожидание съедало по 15 секунд на каждую страницу.
"""
import os
import re
import time

try:
    import requests
except Exception:
    requests = None

JINA = "https://r.jina.ai/"
KEY = os.environ.get("JINA_KEY", "")
PAGES = ("gortsarq",)
BLOCK_WORDS = ("security verification", "just a moment", "cf-chl",
               "attention required", "captcha", "проверка безопасности",
               "checking your browser", "enable javascript")
TRIES = 3
PAGE_RE = re.compile(r"/\d+p-")

_BROWSER_OK = False
_PW = None
_BRW = None

class Read:
    """Ответ читалки: то же, что requests.Response, но собранный вручную."""

    def __init__(self, text, code=200):
        self.text = text or ""
        self.status_code = code
        self.content = self.text.encode("utf-8", "ignore")
        self.headers = {}

    def json(self):
        import json
        return json.loads(self.text or "{}")

def _reset():
    global _PW, _BRW
    try:
        if _BRW:
            _BRW.close()
    except Exception:
        pass
    try:
        if _PW:
            _PW.stop()
    except Exception:
        pass
    _BRW = None
    _PW = None

def _with_key(url, headers):
    if not KEY or not str(url).startswith(JINA):
        return headers
    h = dict(headers or {})
    h.setdefault("Authorization", "Bearer " + KEY)
    return h

def _weak(r):
    """Ответ читалки слабый: ошибка, проверка защиты или пустое тело."""
    try:
        code = getattr(r, "status_code", 0)
        text = getattr(r, "text", "") or ""
    except Exception:
        return True
    if code != 200:
        return True
    if len(text) < 1500:
        return True
    low = text[:4000].lower()
    return any(w in low for w in BLOCK_WORDS)

def _browser(url):
    """Настоящий браузер: проходит проверку и отдаёт страницу в markdown.
    Браузер открывается один раз на весь запуск и переиспользуется —
    раньше поднимался и закрывался на каждой странице.
    Картинки ждём только на объявлении: в списке их нет, а ожидание там
    съедало по 15 секунд на каждую страницу."""
    global _BRW, _PW
    try:
        import fetcher
    except Exception as e:
        print(" браузер недоступен:", type(e).__name__)
        return ""

    need_img = bool(PAGE_RE.search(url or ""))
    h, md, pg = "", "", None
    try:
        if _BRW is None:
            from playwright.sync_api import sync_playwright
            _PW = sync_playwright().start()
            _BRW = _PW.chromium.launch(args=["--no-sandbox"])
        pg = _BRW.new_page(user_agent=fetcher.UA, locale="ru-RU")
        pg.goto(url, timeout=45000, wait_until="domcontentloaded")
        for _ in range(5 if need_img else 2):
            pg.wait_for_timeout(2500)
            h = pg.content()
            if len(h) < 5000 or any(w in h[:4000].lower() for w in BLOCK_WORDS):
                continue
            md = fetcher.to_md(h, url)
            if not need_img or "![" in md:
                break
    except Exception as e:
        print(" браузер: сбой", type(e).__name__)
        md = ""
        _reset()
    finally:
        try:
            if pg:
                pg.close()
        except Exception:
            pass

    if not md and h:
        md = fetcher.to_md(h, url)
    return md if len(md) > 800 else ""

def _patch():
    if requests is None:
        return
    old_get = requests.Session.get

    def get(self, url, **kw):
        global _BROWSER_OK
        try:
            kw["headers"] = _with_key(url, kw.get("headers"))
        except Exception:
            pass

        if not str(url).startswith(JINA) or not any(p in str(url) for p in PAGES):
            return old_get(self, url, **kw)

        page_url = str(url)[len(JINA):]

        if _BROWSER_OK:
            md = _browser(page_url)
            if md:
                print(" джина: сразу браузером")
                return Read(md)

        last = None
        for n in range(TRIES):
            try:
                last = old_get(self, url, **kw)
            except Exception as e:
                print(" чтение: сбой повтора:", type(e).__name__)
                last = None
            if last is not None and not _weak(last):
                if n:
                    print(" джина: прочитано со", n + 1, "раза")
                return last
            print(" джина: слабый ответ", getattr(last, "status_code", "?"),
                  "-> повтор", n + 1)
            if n < TRIES - 1:
                time.sleep(3 + 3 * n)

        md = _browser(page_url)
        if md:
            _BROWSER_OK = True
            print(" джина: страница открыта браузером")
            return Read(md)

        return last if last is not None else Read("", 599)

    requests.Session.get = get

_patch()
print("подготовка: ключ Jina", "есть" if KEY else "нет")
