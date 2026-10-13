# -*- coding: utf-8 -*-
"""
Браузерная ступень для всех модулей сразу.

Python сам импортирует sitecustomize при запуске, поэтому достаточно положить
этот файл рядом с run.py — и любой запрос через requests, на который сайт
ответил отказом защиты (403/429/503 или страница-проверка), повторяется
через cloudscraper. Он проходит JS-проверку так же, как обычный браузер, и
возвращает настоящую страницу. Пустой ответ тоже повторяется: иногда защита
отдаёт страницу без текста, а не с проверкой.

Ничего в других файлах менять не нужно. Если cloudscraper не установлен,
файл тихо ничего не делает и всё работает как раньше.
"""
import requests

BLOCK_MARK = ("just a moment", "performing security verification",
              "attention required", "checking your browser",
              "enable javascript", "captcha", "are you a human")

try:
    import cloudscraper
    _BROWSER = cloudscraper.create_scraper()
except Exception as e:
    _BROWSER = None
    print("браузерная ступень выключена:", type(e).__name__)

def _blocked(r):
    if r is None:
        return True
    if r.status_code in (403, 429, 503):
        return True
    text = r.text or ""
    if len(text) < 800:
        return True
    low = text[:4000].lower()
    return any(m in low for m in BLOCK_MARK)

if _BROWSER is not None:
    _get = requests.Session.get          # исходный метод, до подмены

    def get(self, url, **kw):
        r = _get(self, url, **kw)
        if not _blocked(r):
            return r
        try:
            c = _get(_BROWSER, url, headers=kw.get("headers"),
                     params=kw.get("params"), timeout=kw.get("timeout") or 60)
            if c.status_code == 200 and len(c.text or "") > 1500 and not _blocked(c):
                print("  браузер:", c.status_code, len(c.text), "знаков")
                return c
            print("  браузер:", c.status_code, len(c.text or ""), "знаков")
        except Exception as e:
            print("  браузер ошибка:", type(e).__name__)
        return r

    requests.Session.get = get
