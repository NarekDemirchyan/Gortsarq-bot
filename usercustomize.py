# -*- coding: utf-8 -*-
"""
Этот файл Python читает сам, при старте — подключать его никуда не нужно.
Одна задача: убрать из альбома повтор одной и той же фотографии.

list.am отдаёт одно и то же фото в разных размерах и форматах
(f/430/100011430.webp и s/430/100011430.jpg). Бот собирает галерею по имени
файла, поэтому такие пары попадают в альбом дважды. Здесь альбом
пересобирается по номеру фотографии: повтор убирается, а из двух вариантов
остаётся полный (папка f).
Всё обёрнуто в try: если что-то не выйдет, запрос уходит как есть.
"""
import json
import re

try:
    import requests
except Exception:
    requests = None

def _key(u):
    """Номер фотографии: .../f/430/100011430.webp -> 100011430"""
    u = (u or "").split("?")[0]
    name = u.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    name = re.sub(r"[^0-9]+$", "", name)
    return name or u

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

def _patch():
    if requests is None:
        return
    old = requests.Session.post

    def post(self, url, data=None, json=None, **kw):
        try:
            if url and "sendMediaGroup" in str(url) and isinstance(data, dict):
                raw = data.get("media")
                if isinstance(raw, str):
                    items = json.loads(raw)
                    if isinstance(items, list) and len(items) > 1:
                        fixed = _clean(items)
                        if len(fixed) != len(items):
                            print("  альбом: убрано повторов", len(items) - len(fixed))
                            data = dict(data, media=json.dumps(fixed, ensure_ascii=False))
        except Exception as e:
            print("  альбом: правка не вышла:", type(e).__name__)
        return old(self, url, data=data, json=json, **kw)

    requests.Session.post = post

_patch()
