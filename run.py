# -*- coding: utf-8 -*-
"""
Запуск gortsarq.am / igma.am -> «Барахолка Երևան».

Один запуск = один пост. Расписание задаёт внешний таймер, он же вызывает
этот workflow. Если у сайта свежего нет — слот остаётся пустым.

SITE = gortsarq | igma — если задан, берём его.
Если пусто — чередуем по turn.json.
"""
import importlib
import json
import os

MOD = "gortsarq"     # модуль по умолчанию
TURN = "turn.json"
SITES = ("gortsarq", "igma")

def pick():
    env = (os.environ.get("SITE") or "").strip().lower()
    if env in SITES:
        return env
    last, n = "", 0
    try:
        with open(TURN, encoding="utf-8") as f:
            st = json.load(f)
        last = str(st.get("last") or "")
        n = int(st.get("n") or 0)
    except Exception:
        pass
    nxt = SITES[(SITES.index(last) + 1) % len(SITES)] if last in SITES else SITES[0]
    try:
        with open(TURN, "w", encoding="utf-8") as f:
            json.dump({"last": nxt, "n": n + 1}, f, ensure_ascii=False)
    except Exception as e:
        print("turn.json: сбой записи:", e)
    return nxt

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
