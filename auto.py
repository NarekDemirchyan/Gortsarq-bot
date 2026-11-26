            print("telegram album:", r.status_code, r.text[:200])
            if r.ok:
                return True
            print("  альбом не прошёл, пробуем одним снимком")
        r = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                      data={"chat_id": CHAT_ID, "caption": caption},
                      files={"photo": ("p.jpg", blobs[0])}, timeout=120)
        print("telegram фото:", r.status_code, r.text[:200])
        if r.ok:
            return True
        r = sess.post(f"https://api.telegram.org/bot{TOKEN}/sendDocument",
                      data={"chat_id": CHAT_ID, "caption": caption},
                      files={"document": ("p.jpg", blobs[0])}, timeout=120)
        print("telegram файлом:", r.status_code, r.text[:200])
        return bool(r.ok)

    print("  снимки не скачались, пробуем ссылками")
    cand = [photo_urls(u) for u in d["photos"][:10]]
    for variant in (0, 1):
        urls = [c[variant] for c in cand if len(c) > variant]
        if urls and send_url(sess, urls, caption):
            return True
    return send_url(sess, [], caption)

def load():
    if os.path.exists(STATE):
        return json.load(open(STATE, encoding="utf-8"))
    return {"seen": {}, "queue": [], "last_publish": 0,
            "published": {}, "failed": {}}

def save(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)

def main():
    st = load()
    st.setdefault("seen", {})
    if isinstance(st["seen"], list):          # старый формат — просто список номеров
        st["seen"] = {i: "" for i in st["seen"]}
    if st.get("seen_ver", 1) < 2:
        # снимаем метку, которую поставила прошлая версия: дата «сегодня»
        # у неизвестных объявлений означала «дата не менялась» и глушила обновления
        n = sum(1 for v in st["seen"].values() if v == today_iso())
        st["seen"] = {k: ("" if v == today_iso() else v)
                      for k, v in st["seen"].items()}
        st["seen_ver"] = 2
        print("снята метка старого формата:", n)
    st.setdefault("queue", [])
    st.setdefault("failed", {})
    st.setdefault("published", {})
    st.setdefault("last_publish", 0)
    sess = requests.Session()

    if RESET_ONCE and not st.get("reset_done"):
        st["reset_done"] = True
        print("первый запуск новой версии")

    items = get_ids(sess)
    print("всего объявлений в sitemap:", len(items))

    if TEST:
        if items:
            d = item_data(sess, items[0][0])
            send(sess, d, make_caption(sess, d))
        return

    ok_dates = last_days(FRESH_DAYS)
    in_queue = set(st["queue"])
    fresh = []
    for iid, date in items:
        if date and date not in ok_dates:
            continue                   # старше 7 суток — не берём вовсе
        prev = st["seen"].get(iid, "")
        if prev and (not date or date <= prev):
            continue                   # уже знаем и дата не обновилась
        st["seen"][iid] = date or prev or today_iso()
        if iid in in_queue:
            continue
        fresh.append(iid)

    if len(st["seen"]) > 20000:
        keep = list(st["seen"])[-20000:]
        st["seen"] = {k: st["seen"][k] for k in keep}

    if fresh:
        st["queue"].extend(fresh)
        print("новых и обновлённых в очередь:", len(fresh))
    while len(st["queue"]) > QUEUE_MAX:
        st["queue"].pop(0)

    now = time.time()
    print("очередь:", len(st["queue"]))

    if st["queue"] and now - st["last_publish"] >= INTERVAL:
        for _ in range(3):
            if not st["queue"]:
                break
            iid = st["queue"].pop(0)
            ok = False
            try:
                d = item_data(sess, iid)
                if ONLY_PRIVATE and d.get("is_dealer"):
                    print("  пропуск: дилер", iid)
                    st["published"][iid] = now      # чтобы не возвращался в очередь
                    continue
                cap = make_caption(sess, d)
                ok = bool(cap) and send(sess, d, cap)
            except Exception as e:
                print("ошибка публикации:", e)
            if ok:
                st["last_publish"] = now
                st["published"][iid] = now
                print("опубликовано:", iid)
                break
            st["failed"][iid] = st["failed"].get(iid, 0) + 1
            if st["failed"][iid] < 2:
                st["queue"].append(iid)
                print("  вернём в конец очереди:", iid)
            else:
                print("  брошен", iid)

    if len(st["published"]) > 50000:
        keys = list(st["published"])[-20000:]
        st["published"] = {k: st["published"][k] for k in keys}

    save(st)

main()
