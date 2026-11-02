# -*- coding: utf-8 -*-
"""
Запуск gortsarq.am / igma.am -> «Барахолка Երևան».

Один запуск = один пост. Сайт выбирается по минуте запуска:
  :15 и :45 — igma
  :00 и :30 — gortsarq
Если задан SITE (gortsarq | igma), он важнее.
"""
import importlib
import os
import time

MOD = "gortsarq"
SITES = ("gortsarq", "igma")

def pick():
    env = (os.environ.get("SITE") or "").strip().lower()
    if env in SITES:
        return env
    for m in (14, 15, 16, 44, 45, 46):
        if time.localtime().tm_min == m:
            return "igma"
    return MOD

def run(which):
    try:
        m = importlib.import_module(which)
        return int(m.main() or 0)
    except Exception as e:
        print(f"{which}: сбой {type(e).__name__}: {e}")
        return 0

def main():
    which = pick()
    print("запуск ->", which)
    return run(which)

main()
