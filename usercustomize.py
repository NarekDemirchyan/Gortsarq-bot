# -*- coding: utf-8 -*-
"""
Этот файл бот загружает первым (см. bot.yml), до остальной работы.
Две задачи.

1. Ключ Jina. Читалка r.jina.ai в браузерном режиме открывает страницы,
   которые без ключа отдают проверку защиты. Ключ лежит в секрете JINA_KEY
   и подставляется в каждый запрос к r.jina.ai.

2. Повтор чтения. gortsarq.am то пускает читалку, то закрывает её защитой.
   Если ответ слабый — ошибка, проверка защиты или почти пустой текст —
   запрос повторяется до трёх раз с паузой.

Всё обёрнуто в try: не вышло — запрос уходит как есть.
"""
import os
import time

try:
    import requests
except Exception:
    requests = None

JINA = "https://r.jina.ai/"
KEY = os.environ.get("JINA_KEY", "")
PAGES = ("gortsarq",)
BLOCK_WORDS = ("security verification", "just a moment", "cf-chl",
               "attention required", "captcha", "проверка безопасности")
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
                print(" джина: прочитано со", n + 1, "раза")
            return last
        print(" джина: слабый ответ", getattr(last, "status_code", "?"),
              "-> повтор", n + 1)
        if n < TRIES - 1:
            time.sleep(4 + 4 * n)
    return last

def _patch():
    if requests is None:
        return
    old_get = requests.Session.get

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
            print(" чтение: сбой повтора:", type(e).__name__)
            r = old_get(self, url, **kw)
        return r

    requests.Session.get = get

_patch()
print("подготовка: ключ Jina", "есть" if KEY else "нет")
