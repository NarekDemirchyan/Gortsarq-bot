# -*- coding: utf-8 -*-
"""
Запуск gortsarq.am -> «Барахолка Երևան».

Один запуск = один пост. Расписание задаёт внешний таймер, он же вызывает
этот workflow. Если у gortsarq.am свежего нет — слот остаётся пустым.
"""
import importlib

MOD = "gortsarq"    # модуль, который запускаем

def run(which):
    try:
        m = importlib.import_module(which)
        return int(m.main() or 0)
    except Exception as e:
        print(f"{which}: сбой {type(e).__name__}: {e}")
        return 0

def main():
    print("запуск -> gortsarq")
    return run(MOD)

main()
