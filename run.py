# -*- coding: utf-8 -*-
"""
Диспетчер: один запуск = один пост в «Барахолка Երևան».

Два сайта, поровну по слотам (время Еревана):
  чётный час:  :00 :30 -> ivi.am        :15 :45 -> gortsarq.am
  нечётный час: :00 :30 -> gortsarq.am  :15 :45 -> ivi.am

Если у выбранного сайта свежего нет — слот забирает второй сайт.
SOURCE=ivi|gortsarq (workflow_dispatch) — ручная проверка одного сайта.
list.am из работы исключён.
"""
import os
import importlib
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=4))          # Ереван
SOURCE = os.environ.get("SOURCE", "").strip().lower()
MODS = {"ivi": "ivi", "gortsarq": "gortsarq"}
ALT = ("ivi", "gortsarq")

def slot_source():
    if SOURCE in MODS:
        return SOURCE
    now = datetime.now(TZ)
    mins = (now.minute + 7) // 15 * 15     # округляем к ближайшему слоту
    hour = now.hour + (1 if mins >= 60 else 0)
    mins %= 60
    return ALT[(mins // 15 + hour) % 2]

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

    if run(want):
        return

    other = "gortsarq" if want == "ivi" else "ivi"
    print(f"{want} пусто — пробуем {other}")
    run(other)

main()
