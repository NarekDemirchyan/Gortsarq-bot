# -*- coding: utf-8 -*-
"""
Диспетчер: один запуск = один пост в «Барахолка Երևան».

Ритм 15 минут (время Еревана):
  :00 :15 :30  -> list.am
  :45          -> ivi.am в чётные часы, gortsarq.am в нечётные

Если у выбранного сайта свежего нет — слот забирает list.am.
Если и у list.am пусто — пробуем ivi, затем gortsarq.
SOURCE=list|ivi|gortsarq (workflow_dispatch) — ручная проверка одного сайта.
"""
import os
import importlib
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=4))          # Ереван
SOURCE = os.environ.get("SOURCE", "").strip().lower()
MODS = {"list": "bot", "ivi": "ivi", "gortsarq": "gortsarq"}

def slot_source():
    if SOURCE in MODS:
        return SOURCE
    now = datetime.now(TZ)
    mins = (now.minute + 7) // 15 * 15     # округляем к ближайшему слоту
    hour = now.hour + (1 if mins >= 60 else 0)
    mins %= 60
    if mins == 45:
        return "ivi" if hour % 2 == 0 else "gortsarq"
    return "list"

def run(which):
    try:
        m = importlib.import_module(MODS[which])
        return int(m.main() or 0)
    except Exception as e:
        print(f"{which}: сбой {type(e).__name__}: {e}")
        return 0

def main():
    now = datetime.now(TZ)
    want = slot_source()
    print(f"слот {now:%H:%M} ({'ручной' if SOURCE in MODS else 'auto'}) -> {want}")

    done = run(want)
    print(f"{want}: опубликовано {done}")
    if done:
        return

    for alt in ("list", "ivi", "gortsarq"):
        if alt == want:
            continue
        print(f"{want} пусто — пробуем {alt}")
        done = run(alt)
        print(f"{alt}: опубликовано {done}")
        if done:
            return
    print("во всех трёх сайтах нечего публиковать")

main()
