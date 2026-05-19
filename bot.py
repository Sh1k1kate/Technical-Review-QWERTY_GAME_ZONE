import os, json, time, requests, base64, threading
from datetime import datetime, timezone, timedelta
from flask import Flask, request

# ==================== КОНФИГУРАЦИЯ (переменные окружения) ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GITHUB_OWNER = os.environ.get("GITHUB_OWNER")
GITHUB_REPO = os.environ.get("GITHUB_REPO")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
NOVOSIBIRSK_TZ = timezone(timedelta(hours=7))

for var in ["BOT_TOKEN", "GITHUB_OWNER", "GITHUB_REPO", "GITHUB_TOKEN", "ADMIN_CHAT_ID"]:
    if not os.environ.get(var):
        raise RuntimeError(f"Не задана переменная окружения {var}")

# ==================== GitHub helpers ====================
def get_github_raw(file_path):
    url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{file_path}"
    resp = requests.get(url)
    if resp.status_code == 200:
        return resp.json()
    return None

def get_github_api(file_path):
    return f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{file_path}"

def read_json_from_github(file_path):
    data = get_github_raw(file_path)
    return data if data is not None else {}

def write_json_to_github(file_path, data, message="update"):
    if not GITHUB_TOKEN:
        return False
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    url = get_github_api(file_path)
    resp = requests.get(url, headers=headers)
    sha = None
    if resp.status_code == 200:
        sha = resp.json().get("sha")
    elif resp.status_code != 404:
        return False

    content = json.dumps(data, indent=2, ensure_ascii=False)
    encoded = base64.b64encode(content.encode('utf-8')).decode('ascii')
    payload = {"message": message, "content": encoded, "branch": GITHUB_BRANCH}
    if sha:
        payload["sha"] = sha
    put_resp = requests.put(url, headers=headers, json=payload)
    return put_resp.status_code in (200, 201)

# ==================== Подписчики и админы ====================
def get_subscribers():
    return read_json_from_github("subscribers.json").get("subscribers", [])

def get_admins():
    adm = read_json_from_github("admins.json").get("admin_ids", [])
    return adm if adm else [ADMIN_CHAT_ID]

def is_admin(chat_id):
    return str(chat_id) in get_admins()

def add_subscriber(chat_id):
    subs = get_subscribers()
    if str(chat_id) not in subs:
        subs.append(str(chat_id))
        write_json_to_github("subscribers.json", {"subscribers": subs}, "add subscriber")
        return True
    return False

def remove_subscriber(chat_id):
    subs = get_subscribers()
    if str(chat_id) in subs:
        subs.remove(str(chat_id))
        write_json_to_github("subscribers.json", {"subscribers": subs}, "remove subscriber")
        return True
    return False

# ==================== Telegram API ====================
def send_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(url, json=payload)

def send_document(chat_id, file_content, filename, caption=""):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    requests.post(url, data={"chat_id": chat_id, "caption": caption},
                  files={"document": (filename, file_content)})

def send_to_all(text):
    for aid in get_admins():
        send_message(aid, text)
    for sub in get_subscribers():
        if sub not in get_admins():
            send_message(sub, text)

def get_reply_keyboard(chat_id):
    is_sub = str(chat_id) in get_subscribers()
    admin = is_admin(chat_id)
    if admin:
        kb = [
            [{"text": "📊 Статус"}, {"text": "📋 Статус всех"}],
            [{"text": "📜 История"}, {"text": "📈 Еженедельный"}],
            [{"text": "🔄 Интервал"}, {"text": "⚡ Проверка"}],
            [{"text": "🧹 Кэш"}, {"text": "📢 Оповещение"}],
            [{"text": "📁 Конфиг"}, {"text": "🔄 Обновить монитор"}],
        ]
    else:
        kb = [
            [{"text": "📊 Статус"}, {"text": "📋 Статус всех"}],
            [{"text": "📜 История"}],
        ]
    if is_sub:
        kb.append([{"text": "❌ Отписаться"}])
    else:
        kb.append([{"text": "✅ Подписаться"}])
    return {"keyboard": kb, "resize_keyboard": True}

# ==================== Команды ====================
def cmd_status(chat_id, pc):
    data = read_json_from_github("games_status.json")
    if pc not in data:
        send_message(chat_id, "Нет данных.", reply_markup=get_reply_keyboard(chat_id))
        return
    d = data[pc]
    games = d.get("games", {})
    lines = [f"Статус ПК {pc}:"]
    for name, info in games.items():
        if info["installed"]:
            icon = "🔄" if info.get("update_available") else "✅"
        else:
            icon = "❌"
        lines.append(f"{icon} {name}")
    send_message(chat_id, "\n".join(lines), reply_markup=get_reply_keyboard(chat_id))

def cmd_status_all(chat_id):
    data = read_json_from_github("games_status.json")
    if not data:
        send_message(chat_id, "Нет данных.", reply_markup=get_reply_keyboard(chat_id))
        return
    lines = ["📊 Сводка по всем ПК:"]
    for pc in sorted(data.keys(), key=lambda x: int(x) if x.isdigit() else 0):
        d = data[pc]
        missing = d.get("missing", [])
        upd = any(g.get("update_available") for g in d.get("games", {}).values())
        icon = "✅" if not missing else "❌"
        upd_icon = " 🔄" if upd else ""
        lines.append(f"{icon} {pc}{upd_icon}: отсутствуют {len(missing)} игр")
    send_message(chat_id, "\n".join(lines), reply_markup=get_reply_keyboard(chat_id))

def cmd_history(chat_id):
    hist = read_json_from_github("game_history.json").get("events", [])
    if not hist:
        send_message(chat_id, "📭 История пуста.", reply_markup=get_reply_keyboard(chat_id))
        return
    lines = ["📜 Последние удаления:"]
    for ev in reversed(hist[-10:]):
        ts = ev["timestamp"][:16].replace("T", " ")
        pc = ev["pc"]
        lines.append(f"\n🖥️ {pc} ({ts})")
        for g in ev["missing"]:
            lines.append(f"  ❌ {g}")
    send_message(chat_id, "\n".join(lines), reply_markup=get_reply_keyboard(chat_id))

def cmd_weekly(chat_id):
    if not is_admin(chat_id):
        send_message(chat_id, "⛔ Только для администратора.", reply_markup=get_reply_keyboard(chat_id))
        return
    all_data = read_json_from_github("games_status.json")
    if not all_data:
        send_message(chat_id, "Нет данных.", reply_markup=get_reply_keyboard(chat_id))
        return
    game_missing = {}
    game_update = {}
    total = 0
    for d in all_data.values():
        total += 1
        for g in d.get("missing", []):
            game_missing[g] = game_missing.get(g, 0) + 1
        for gname, gdata in d.get("games", {}).items():
            if gdata.get("update_available"):
                game_update[gname] = game_update.get(gname, 0) + 1
    now = datetime.now(NOVOSIBIRSK_TZ)
    start = now - timedelta(days=now.weekday() + 1)
    end = start + timedelta(days=7)
    period = f"{start.strftime('%d.%m.%Y')} - {end.strftime('%d.%m.%Y')}"
    lines = [f"📊 Еженедельный отчёт ({period})", f"🖥️ Всего ПК: {total}"]
    if game_missing:
        lines.append("\n🚫 Отсутствуют игры:")
        for name, cnt in sorted(game_missing.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"• {name}: на {cnt} ПК")
    else:
        lines.append("\n✅ Все игры на месте.")
    if game_update:
        lines.append("\n🔄 Доступны обновления (Steam):")
        for name, cnt in sorted(game_update.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"• {name}: на {cnt} ПК")
    else:
        lines.append("\n🔄 Нет доступных обновлений.")
    lines.append("\n🔍 Подробнее: https://sh1k1kate.github.io/Technical-Review-QWERTY_GAME_ZONE/games.html")
    send_to_all("\n".join(lines))
    send_message(chat_id, "📊 Отчёт отправлен.", reply_markup=get_reply_keyboard(chat_id))

def cmd_set_interval(chat_id, minutes):
    if not is_admin(chat_id):
        send_message(chat_id, "⛔ Только для администратора.", reply_markup=get_reply_keyboard(chat_id))
        return
    gc = read_json_from_github("global_config.json") or {}
    gc["check_interval"] = minutes * 60
    if write_json_to_github("global_config.json", gc, f"set interval {minutes} min"):
        send_message(chat_id, f"✅ Глобальный интервал изменён на {minutes} мин.", reply_markup=get_reply_keyboard(chat_id))
    else:
        send_message(chat_id, "❌ Ошибка записи.", reply_markup=get_reply_keyboard(chat_id))

def cmd_force_check(chat_id, pc=None):
    if not is_admin(chat_id):
        send_message(chat_id, "⛔ Только для администратора.", reply_markup=get_reply_keyboard(chat_id))
        return
    flags = read_json_from_github("flags.json") or {}
    flags["force_check"] = {"timestamp": datetime.now().isoformat(), "target": pc}
    if write_json_to_github("flags.json", flags, "force_check"):
        send_message(chat_id, "⏳ Запрос отправлен.", reply_markup=get_reply_keyboard(chat_id))
    else:
        send_message(chat_id, "❌ Ошибка.", reply_markup=get_reply_keyboard(chat_id))

def cmd_clear_cache(chat_id, pc):
    if not is_admin(chat_id):
        send_message(chat_id, "⛔ Только для администратора.", reply_markup=get_reply_keyboard(chat_id))
        return
    flags = read_json_from_github("flags.json") or {}
    if pc.lower() == "все":
        flags["clearcache_all"] = {"timestamp": datetime.now().isoformat()}
        write_json_to_github("flags.json", flags, "clearcache all")
        send_message(chat_id, "⏳ Запрос на очистку кэша для ВСЕХ ПК отправлен.", reply_markup=get_reply_keyboard(chat_id))
    else:
        flags["clearcache"] = {"target": pc, "timestamp": datetime.now().isoformat()}
        write_json_to_github("flags.json", flags, "clearcache")
        send_message(chat_id, f"⏳ Запрос на очистку кэша ПК {pc} отправлен.", reply_markup=get_reply_keyboard(chat_id))

def cmd_announce(chat_id, text):
    if not is_admin(chat_id):
        send_message(chat_id, "⛔ Только для администратора.", reply_markup=get_reply_keyboard(chat_id))
        return
    send_to_all(f"📢 {text}")
    send_message(chat_id, "✅ Объявление отправлено.", reply_markup=get_reply_keyboard(chat_id))

def cmd_add_admin(chat_id, new_admin):
    if not is_admin(chat_id):
        send_message(chat_id, "⛔ Только для администратора.", reply_markup=get_reply_keyboard(chat_id))
        return
    admins = get_admins()
    if new_admin not in admins:
        admins.append(new_admin)
        if write_json_to_github("admins.json", {"admin_ids": admins}, "add admin"):
            send_message(chat_id, f"✅ Администратор {new_admin} добавлен.", reply_markup=get_reply_keyboard(chat_id))
        else:
            send_message(chat_id, "❌ Ошибка.", reply_markup=get_reply_keyboard(chat_id))
    else:
        send_message(chat_id, "Уже админ.", reply_markup=get_reply_keyboard(chat_id))

def cmd_update_monitor(chat_id, file_id):
    if not is_admin(chat_id):
        send_message(chat_id, "⛔ Только для администратора.", reply_markup=get_reply_keyboard(chat_id))
        return
    file_info = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}").json()
    file_path = file_info["result"]["file_path"]
    file_content = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}").content

    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    releases_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases"
    resp = requests.get(releases_url + "/latest", headers=headers)
    if resp.status_code == 200:
        upload_url = resp.json()["upload_url"].replace("{?name,label}", "?name=monitor.exe")
    else:
        new_rel = {"tag_name": "monitor", "name": "Monitor Update", "body": "", "draft": False, "prerelease": False}
        resp = requests.post(releases_url, headers=headers, json=new_rel)
        if resp.status_code != 201:
            send_message(chat_id, "❌ Ошибка создания релиза.", reply_markup=get_reply_keyboard(chat_id))
            return
        upload_url = resp.json()["upload_url"].replace("{?name,label}", "?name=monitor.exe")

    headers["Content-Type"] = "application/octet-stream"
    if requests.put(upload_url, headers=headers, data=file_content).status_code in (200, 201):
        flags = read_json_from_github("flags.json") or {}
        flags["update_monitor"] = {"timestamp": datetime.now().isoformat()}
        write_json_to_github("flags.json", flags, "set monitor update flag")
        send_message(chat_id, "✅ Новый monitor.exe загружен. Мониторы обновятся при следующей проверке.", reply_markup=get_reply_keyboard(chat_id))
    else:
        send_message(chat_id, "❌ Ошибка загрузки файла в релиз.", reply_markup=get_reply_keyboard(chat_id))

def cmd_get_config(chat_id, pc):
    if not is_admin(chat_id):
        send_message(chat_id, "⛔ Только для администратора.", reply_markup=get_reply_keyboard(chat_id))
        return
    flags = read_json_from_github("flags.json") or {}
    flags["get_config"] = {"target": pc, "requested_by": chat_id, "timestamp": datetime.now().isoformat()}
    if write_json_to_github("flags.json", flags, "get_config"):
        send_message(chat_id, f"⏳ Запрос конфига с ПК {pc} отправлен.", reply_markup=get_reply_keyboard(chat_id))
    else:
        send_message(chat_id, "❌ Ошибка.", reply_markup=get_reply_keyboard(chat_id))

# ==================== Периодическая рассылка уведомлений ====================
alert_lock = threading.Lock()

def get_last_notified():
    data = read_json_from_github("game_history.json")
    if data and "_bot_state" in data:
        return data["_bot_state"].get("last_notified")
    return datetime.min.isoformat()

def update_last_notified(ts):
    data = read_json_from_github("game_history.json")
    if not data:
        data = {"events": [], "_bot_state": {}}
    if "_bot_state" not in data:
        data["_bot_state"] = {}
    data["_bot_state"]["last_notified"] = ts
    write_json_to_github("game_history.json", data, "update bot state")

def send_pending_alerts():
    with alert_lock:
        last_ts = get_last_notified()
        hist = read_json_from_github("game_history.json")
        if not hist or "events" not in hist:
            return
        events = hist["events"]
        new_events = [e for e in events if e["timestamp"] > last_ts]
        if not new_events:
            return
        lines = ["🚨 Обнаружены удалённые игры:"]
        for ev in sorted(new_events, key=lambda x: x["timestamp"]):
            ts = ev["timestamp"][:16].replace("T", " ")
            pc = ev["pc"]
            lines.append(f"\n🖥️ {pc} ({ts})")
            for g in ev["missing"]:
                lines.append(f"  ❌ {g}")
        msg = "\n".join(lines)
        send_to_all(msg)
        update_last_notified(new_events[-1]["timestamp"])

def alert_scheduler():
    while True:
        time.sleep(60)
        try:
            send_pending_alerts()
        except Exception as e:
            print(f"Alert scheduler error: {e}")

# ==================== Обработка флагов (ответы мониторов) ====================
def flags_worker():
    while True:
        time.sleep(30)
        try:
            flags = read_json_from_github("flags.json")
            if not flags:
                continue
            if "config_ready" in flags:
                cr = flags["config_ready"]
                pc = cr.get("pc")
                requested_by = cr.get("requested_by")
                config_data = read_json_from_github(f"configs_shared/{pc}.json")
                if config_data:
                    send_document(requested_by, json.dumps(config_data, indent=2, ensure_ascii=False).encode('utf-8'),
                                  f"config_{pc}.json", f"Конфиг с ПК {pc}")
                del flags["config_ready"]
                write_json_to_github("flags.json", flags, "clear config_ready")
            if "status_ready" in flags:
                sr = flags["status_ready"]
                pc = sr.get("pc")
                requested_by = sr.get("requested_by")
                status_data = read_json_from_github(f"status_{pc}.json")
                if status_data:
                    send_message(requested_by, status_data["text"])
                del flags["status_ready"]
                write_json_to_github("flags.json", flags, "clear status_ready")
        except Exception as e:
            print(f"Flags worker error: {e}")

# ==================== Flask Webhook ====================
app = Flask(__name__)
user_states = {}

@app.route("/", methods=["GET"])
def health_check():
    return "Bot is running", 200

@app.route("/", methods=["POST"])
def webhook():
    update = request.get_json()
    if "message" in update:
        msg = update["message"]
        chat_id = str(msg["chat"]["id"])
        text = msg.get("text", "")

        # Обработка force_reply
        state = user_states.get(chat_id)
        if state:
            if state["action"] == "awaiting_status_pc":
                if text.isdigit():
                    cmd_status(chat_id, text)
                else:
                    send_message(chat_id, "Некорректный номер ПК.", reply_markup=get_reply_keyboard(chat_id))
                del user_states[chat_id]
                return "ok"
            elif state["action"] == "awaiting_interval":
                if text.isdigit() and int(text) > 0:
                    cmd_set_interval(chat_id, int(text))
                else:
                    send_message(chat_id, "Некорректное значение.", reply_markup=get_reply_keyboard(chat_id))
                del user_states[chat_id]
                return "ok"
            elif state["action"] == "awaiting_clearcache_pc":
                if text.isdigit() or text.lower() == "все":
                    cmd_clear_cache(chat_id, text)
                else:
                    send_message(chat_id, "Некорректный номер ПК.", reply_markup=get_reply_keyboard(chat_id))
                del user_states[chat_id]
                return "ok"
            elif state["action"] == "awaiting_announcement":
                if text.strip():
                    cmd_announce(chat_id, text.strip())
                else:
                    send_message(chat_id, "Текст не может быть пустым.", reply_markup=get_reply_keyboard(chat_id))
                del user_states[chat_id]
                return "ok"
            elif state["action"] == "awaiting_get_config_pc":
                if text.isdigit():
                    cmd_get_config(chat_id, text)
                else:
                    send_message(chat_id, "Некорректный номер ПК.", reply_markup=get_reply_keyboard(chat_id))
                del user_states[chat_id]
                return "ok"
            elif state["action"] == "awaiting_update_config_pc":
                if text.isdigit() or text.lower() == "все":
                    user_states[chat_id] = {"action": "upload_config", "pc": text}
                    send_message(chat_id, f"Отправьте файл config.json для ПК {text}.")
                else:
                    send_message(chat_id, "Некорректный номер ПК.", reply_markup=get_reply_keyboard(chat_id))
                    del user_states[chat_id]
                return "ok"
            elif state["action"] == "awaiting_force_check_pc":
                if text.isdigit() or text.lower() == "все":
                    pc = None if text.lower() == "все" else text
                    cmd_force_check(chat_id, pc)
                else:
                    send_message(chat_id, "Некорректный номер ПК.", reply_markup=get_reply_keyboard(chat_id))
                del user_states[chat_id]
                return "ok"

        # Обработка кнопок и команд
        if text == "/start" or text == "✅ Подписаться":
            if add_subscriber(chat_id):
                send_message(chat_id, "✅ Вы подписаны.", reply_markup=get_reply_keyboard(chat_id))
            else:
                send_message(chat_id, "Вы уже подписаны.", reply_markup=get_reply_keyboard(chat_id))
        elif text == "/stop" or text == "❌ Отписаться":
            if remove_subscriber(chat_id):
                send_message(chat_id, "❌ Вы отписаны.", reply_markup=get_reply_keyboard(chat_id))
            else:
                send_message(chat_id, "Вы не были подписаны.", reply_markup=get_reply_keyboard(chat_id))
        elif text == "📊 Статус" or text == "/status":
            send_message(chat_id, "Введите номер компьютера:", reply_markup={"force_reply": True})
            user_states[chat_id] = {"action": "awaiting_status_pc"}
        elif text.startswith("/status "):
            parts = text.split()
            if len(parts) == 2 and parts[1].isdigit():
                cmd_status(chat_id, parts[1])
            else:
                send_message(chat_id, "Использование: /status <номер>", reply_markup=get_reply_keyboard(chat_id))
        elif text == "📋 Статус всех" or text == "/status_all":
            cmd_status_all(chat_id)
        elif text == "📜 История" or text == "/history":
            cmd_history(chat_id)
        elif text == "📈 Еженедельный" or text == "/weekly":
            cmd_weekly(chat_id)
        elif text == "🔄 Интервал" or text == "/setinterval":
            if is_admin(chat_id):
                send_message(chat_id, "Введите новый интервал в минутах:", reply_markup={"force_reply": True})
                user_states[chat_id] = {"action": "awaiting_interval"}
            else:
                send_message(chat_id, "⛔ Нет прав.", reply_markup=get_reply_keyboard(chat_id))
        elif text == "⚡ Проверка" or text == "/force_check":
            if is_admin(chat_id):
                send_message(chat_id, "Введите номер ПК (или 'все'):", reply_markup={"force_reply": True})
                user_states[chat_id] = {"action": "awaiting_force_check_pc"}
            else:
                send_message(chat_id, "⛔ Нет прав.", reply_markup=get_reply_keyboard(chat_id))
        elif text == "🧹 Кэш" or text == "/clearcache":
            if is_admin(chat_id):
                send_message(chat_id, "Введите номер ПК (или 'все'):", reply_markup={"force_reply": True})
                user_states[chat_id] = {"action": "awaiting_clearcache_pc"}
            else:
                send_message(chat_id, "⛔ Нет прав.", reply_markup=get_reply_keyboard(chat_id))
        elif text == "📢 Оповещение" or text == "/announce":
            if is_admin(chat_id):
                send_message(chat_id, "Введите текст объявления:", reply_markup={"force_reply": True})
                user_states[chat_id] = {"action": "awaiting_announcement"}
            else:
                send_message(chat_id, "⛔ Нет прав.", reply_markup=get_reply_keyboard(chat_id))
        elif text == "📁 Конфиг":
            if is_admin(chat_id):
                send_message(chat_id, "Выберите действие:", reply_markup={
                    "keyboard": [[{"text": "📥 Получить конфиг"}, {"text": "📤 Обновить конфиг"}], [{"text": "↩️ Назад"}]],
                    "resize_keyboard": True, "one_time_keyboard": True
                })
            else:
                send_message(chat_id, "⛔ Нет прав.", reply_markup=get_reply_keyboard(chat_id))
        elif text == "📥 Получить конфиг":
            if is_admin(chat_id):
                send_message(chat_id, "Введите номер ПК:", reply_markup={"force_reply": True})
                user_states[chat_id] = {"action": "awaiting_get_config_pc"}
            else:
                send_message(chat_id, "⛔ Нет прав.", reply_markup=get_reply_keyboard(chat_id))
        elif text == "📤 Обновить конфиг":
            if is_admin(chat_id):
                send_message(chat_id, "Введите номер ПК (или 'все'):", reply_markup={"force_reply": True})
                user_states[chat_id] = {"action": "awaiting_update_config_pc"}
            else:
                send_message(chat_id, "⛔ Нет прав.", reply_markup=get_reply_keyboard(chat_id))
        elif text == "↩️ Назад":
            send_message(chat_id, "Главное меню", reply_markup=get_reply_keyboard(chat_id))
        elif text == "🔄 Обновить монитор" or text == "/update_monitor":
            if is_admin(chat_id):
                send_message(chat_id, "Отправьте файл monitor.exe")
            else:
                send_message(chat_id, "⛔ Нет прав.", reply_markup=get_reply_keyboard(chat_id))
        elif text.startswith("/addadmin"):
            parts = text.split()
            if len(parts) == 2 and parts[1].isdigit():
                cmd_add_admin(chat_id, parts[1])
            else:
                send_message(chat_id, "Использование: /addadmin <chat_id>", reply_markup=get_reply_keyboard(chat_id))
        elif text == "/help":
            help_text = (
                "Команды:\n"
                "/start - подписаться\n"
                "/stop - отписаться\n"
                "/status [номер_ПК] - статус одного ПК\n"
                "/status_all - все ПК\n"
                "/history - удаления\n"
                "Админские:\n"
                "/setinterval <минуты>\n"
                "/force_check [номер_ПК|все]\n"
                "/clearcache <номер_ПК|все>\n"
                "/announce <текст>\n"
                "/addadmin <chat_id>\n"
                "/update_monitor (пришлите .exe)\n"
                "/get_config <номер_ПК>\n"
                "/update_config <номер_ПК|все> (отправьте config.json)"
            )
            send_message(chat_id, help_text, reply_markup=get_reply_keyboard(chat_id))
        else:
            send_message(chat_id, "Используйте кнопки или /help", reply_markup=get_reply_keyboard(chat_id))

    elif "document" in msg:
        doc = msg["document"]
        file_name = doc.get("file_name", "")
        file_id = doc["file_id"]
        chat_id = str(msg["chat"]["id"])
        if not is_admin(chat_id):
            send_message(chat_id, "⛔ Нет прав.", reply_markup=get_reply_keyboard(chat_id))
            return "ok"
        if file_name.lower() == "monitor.exe":
            cmd_update_monitor(chat_id, file_id)
        elif file_name.lower() == "config.json":
            state = user_states.get(chat_id)
            if state and state.get("action") == "upload_config":
                pc = state["pc"]
                file_info = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}").json()
                file_path = file_info["result"]["file_path"]
                file_content = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}").content
                try:
                    new_config = json.loads(file_content.decode('utf-8'))
                    if pc.lower() == "все":
                        write_json_to_github("global_config.json", new_config, "update global config")
                        flags = read_json_from_github("flags.json") or {}
                        flags["update_config_all"] = {"timestamp": datetime.now().isoformat()}
                        write_json_to_github("flags.json", flags, "update config all")
                        send_message(chat_id, "✅ Глобальный конфиг обновлён. Все мониторы применят его при следующей проверке.", reply_markup=get_reply_keyboard(chat_id))
                    else:
                        write_json_to_github(f"configs/{pc}.json", new_config, f"update config PC {pc}")
                        flags = read_json_from_github("flags.json") or {}
                        flags["update_config"] = {"target": pc, "timestamp": datetime.now().isoformat()}
                        write_json_to_github("flags.json", flags, "update_config")
                        send_message(chat_id, f"✅ Конфиг для ПК {pc} обновлён.", reply_markup=get_reply_keyboard(chat_id))
                except Exception as e:
                    send_message(chat_id, f"❌ Ошибка обработки JSON: {e}", reply_markup=get_reply_keyboard(chat_id))
                del user_states[chat_id]
            else:
                send_message(chat_id, "Сначала укажите ПК через 📤 Обновить конфиг.", reply_markup=get_reply_keyboard(chat_id))
    return "ok"

if __name__ == "__main__":
    threading.Thread(target=alert_scheduler, daemon=True).start()
    threading.Thread(target=flags_worker, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
