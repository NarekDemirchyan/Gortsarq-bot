# -*- coding: utf-8 -*-
"""
Запуск ivi.am -> «Барахолка Երևան».

Отдельный вход для ivi: один запуск = один пост. Расписание задаёт внешний
таймер, он же вызывает workflow ivi.yml. С gortsarq.am этот файл не связан
и ничего общего с run.py не имеет.

Очередь и состояние — свои: state-ivi.json.
"""
import importlib

MOD = "ivi"               # модуль, который запускаем

def run(which):
    try:
        m = importlib.import_module(which)
        return int(m.main() or 0)
    except Exception as e:
        print(f"{which}: сбой {type(e).__name__}: {e}")
        return 0

def main():
    print("запуск -> ivi")
    return run(MOD)

main()
