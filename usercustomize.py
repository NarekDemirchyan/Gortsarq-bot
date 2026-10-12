# -*- coding: utf-8 -*-
"""
Этот файл Python читает сам, при старте — подключать его никуда не нужно.
Одна задача: убрать из альбома повтор одной и той же фотографии.

list.am отдаёт одно и то же фото в разных размерах и форматах
(f/220/99993220.webp и другой размер того же снимка). Бот собирает галерею
по имени файла, поэтому такие пары попадают в альбом дважды. Здесь альбом
пересобирается по номеру фотографии: повтор убирается, а из двух вариантов
остаётся полный (папка f).

В логе видно строку «альбом: фото ...» — по ней понятно, что файл загрузился
и как называются снимки. Всё обёрнуто в try: не вышло — запрос уходит как есть.
"""
import json
import re

try:
    import requests
except Exception:
    requests = None

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

def _patch():
    if requests is None:
        return
    old = requests.Session.post

    def post(self, url, data=None, json=None, **kw):
        if url and "sendMediaGroup" in str(url):
            try:
                raw = (data or {}).get("media")
                items = json.loads(raw) if isinstance(raw, str) else None
                if isinstance(items, list):
                    print("  альбом: фото", len(items), "->", _names(items))
                    fixed = _clean(items)
                    if len(fixed) != len(items):
                        print("  альбом: убрано повторов", len(items) - len(fixed))
                        data = dict(data, media=json.dumps(fixed, ensure_ascii=False))
            except Exception as e:
                print("  альбом: правка не вышла:", type(e).__name__)
        return old(self, url, data=data, json=json, **kw)

    requests.Session.post = post

_patch()
