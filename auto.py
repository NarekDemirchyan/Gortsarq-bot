# -*- coding: utf-8 -*-
import os, re, json, time, requests

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID_AUTO", "")

STATE      = "state_auto.json"
INTERVAL   = 0            # публикуем на каждом запуске
QUEUE_MAX  = 2000         # максимум объявлений в очереди
FRESH_DAYS = 7            # берём объявления за последние 7 суток
RESET_ONCE = True
TEST       = False
ONLY_PRIVATE = True       # только частные продавцы
FORGET_SEEN  = True       # забыть просмотренные, чтобы очередь наполнилась

JINA = "https://r.jina.ai/"
BASE = "https://auto.am"
SITEMAPS = [f"{BASE}/sitemaps/offers-{i}.xml" for i in range(1, 11)]
HDRS = {"User-Agent": "curl/8.5.0"}
