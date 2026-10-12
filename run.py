# -*- coding: utf-8 -*-
"""
Диспетчер: один запуск = один пост в «Барахолка Երևան».

Сайт list.am из работы убран: он закрывает облачные адреса.
Работают два сайта, и они чередуются по слотам (время Еревана):
  :00 :15 :30 :45 -> ivi.am, gortsarq.am, ivi.am, gortsarq.am и так далее

Если у выбранного сайта свежего нет — пробуем второй сайт.
SOURCE=ivi|gortsarq (workflow_dispatch) — ручная проверка одного сайта.
Очередь list.am остаётся в state.json нетронутой: если когда-нибудь
захочется вернуть его в работу, там всё на месте.
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

    done = run(want)
    print(f"{want}: опубликовано {done}")
    if done:
        return

    for alt in ALT:
        if alt == want:
            continue
        print(f"{want} пусто — пробуем {alt}")
        done = run(alt)
        print(f"{alt}: опубликовано {done}")
        if done:
            return
    print("в двух сайтах нечего публиковать")

main()
