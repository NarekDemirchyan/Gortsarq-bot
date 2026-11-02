# -*- coding: utf-8 -*-
"""
Գործարկում է հերթապահ մոդուլը -> «Բարախոլկա Երևան»։

Մեկ վազք = մեկ գրառում։ Հերթը պտտվում է մեկ-մեկ՝ գործարք, igma, գործարք,
igma։ Եթե հերթապահ կայքի հայտարարությունները վերջացել են, նույն վազքում
հրապարակում է մյուսը, և հաջորդ անգամ նորից նույն հերթականությունն է։
Ում հերթն է՝ գրվում է turn.json-ում։
"""
import json
import importlib

TURN = "turn.json"
ORDER = ("gortsarq", "igma")

def read_turn():
    try:
        t = json.load(open(TURN, encoding="utf-8"))
    except Exception:
        t = {}
    try:
        return int(t.get("i", 0)) % len(ORDER)
    except Exception:
        return 0

def write_turn(i):
    try:
        json.dump({"i": i % len(ORDER)}, open(TURN, "w", encoding="utf-8"))
    except Exception as e:
        print("turn.json չգրվեց:", type(e).__name__)

def run(which):
    try:
        m = importlib.import_module(which)
        return int(m.main() or 0)
    except Exception as e:
        print(f"{which}: сбой {type(e).__name__}: {e}")
        return 0

def main():
    i = read_turn()
    first, second = ORDER[i], ORDER[(i + 1) % len(ORDER)]

    print("запуск ->", first)
    if run(first):
        write_turn((i + 1) % len(ORDER))
        return 1

    print("հերթը դատարկ է, փորձում ենք ->", second)
    if run(second):
        write_turn(i)
        return 1

    write_turn((i + 1) % len(ORDER))
    return 0

main()
