# -*- coding: utf-8 -*-
"""
Этот файл бот загружает первым (см. bot.yml), до остальной работы.
Задача одна: в альбоме не должно быть одного и того же снимка дважды.

list.am отдаёт одну фотографию в разных размерах и форматах, бот собирает
галерею по имени файла и берёт обе. Здесь альбом пересобирается по номеру
фотографии: повтор убирается, из вариантов остаётся полный (папка f).

Если после этого остаётся один снимок, альбом отправлять нельзя — Telegram
принимает альбом от двух фото. Тогда снимок уходит обычным сообщением,
а боту возвращается ответ об успехе: он продолжает как обычно и добавляет
«Связаться с продавцом» и кнопку.

Всё обёрнуто в try: не вышло — запрос уходит как есть.
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

class _Answer(object):
    """Ответ, который видит бот, когда снимок ушёл обычным сообщением."""

    def __init__(self, code, text):
        self.status_code = code
        self.text = text

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        return json.loads(self.text)

def _one_photo(sess, old, url, data, one, timeout):
    """Один снимок вместо альбома: обычное фото, а боту — ответ об успехе."""
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
    old = requests.Session.post

    def post(self, url, data=None, json=None, **kw):
        if url and "sendMediaGroup" in str(url):
            try:
                raw = (data or {}).get("media")
                items = json.loads(raw) if isinstance(raw, str) else None
                if isinstance(items, list):
                    print("  альбом: фото", len(items), "->", _names(items))
                    fixed = _clean(items)
                    if len(fixed) == 1:
                        return _one_photo(self, old, url, data, fixed[0],
                                          kw.get("timeout", 60))
                    if len(fixed) != len(items):
                        print("  альбом: убрано повторов", len(items) - len(fixed))
                        data = dict(data, media=json.dumps(fixed, ensure_ascii=False))
            except Exception as e:
                print("  альбом: правка не вышла:", type(e).__name__)
        return old(self, url, data=data, json=json, **kw)

    requests.Session.post = post

_patch()
