#!/usr/bin/env python3
"""Telegram-бот каталога автомобилей (клон @chinacars_market_bot).

Меню и ответы работают через deep-link `/start <payload>`:
  catalog / (пусто)  главное меню
  cat_<ключ>         список авто категории
  brand_<ключ>       список авто марки
  howto              «Как заказать»
  любое другое       запрос менеджеру (спасибо + уведомление админам)

Только стандартная библиотека. Токен: переменная BOT_TOKEN или файл .env рядом с ботом.
"""
import html
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("DATA_DIR", BASE / "data"))
STATE = Path(os.environ.get("STATE_DIR", BASE / "state"))
NBSP = " "
MAX_LEN = 4000

log = logging.getLogger("nezacars")


def load_env():
    env = BASE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


class Store:
    """config.json и catalog.json, перечитываются при изменении файла."""

    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        self._cache = {}

    def _load(self, name):
        path = self.dir / name
        mtime = path.stat().st_mtime
        hit = self._cache.get(name)
        if not hit or hit[0] != mtime:
            hit = (mtime, json.loads(path.read_text(encoding="utf-8")))
            self._cache[name] = hit
        return hit[1]

    @property
    def config(self):
        return self._load("config.json")

    @property
    def catalog(self):
        return self._load("catalog.json")


def fmt_price(price, cfg):
    if price is None:
        return cfg["price_on_request"]
    return f"{price:,}".replace(",", NBSP) + " ₽"


def start_link(username, payload):
    return f"https://t.me/{username}?start={payload}"


def back_markup(cfg, username, extra_rows=()):
    url = cfg.get("back_url") or start_link(username, "catalog")
    rows = [[{"text": cfg["back_button"], "url": url}]]
    rows.extend(extra_rows)
    return {"inline_keyboard": rows}


def render_menu(cfg, username):
    rows = [
        [{"text": b["text"], "url": start_link(username, b["start"])} for b in row]
        for row in cfg["menu"]
    ]
    return [{"text": cfg["catalog_text"], "reply_markup": {"inline_keyboard": rows}}]


def render_howto(cfg, username):
    managers = "\n".join(
        f'<a href="{html.escape(m["url"])}">{html.escape(m["name"])}</a>'
        for m in cfg.get("managers", [])
    )
    text = cfg["howto_text"].format(
        managers=managers, brand=html.escape(cfg["brand_name"]), brand_url=html.escape(cfg["brand_url"])
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return [{"text": text, "reply_markup": back_markup(cfg, username)}]


def car_block(car, cfg):
    title = f"🚗 <b>{html.escape(car['title'])}</b>"
    price = html.escape(fmt_price(car.get("price"), cfg))
    if car.get("url"):
        link = f'<a href="{html.escape(car["url"])}">{html.escape(cfg["card_link_text"])}</a>'
    else:
        link = html.escape(cfg["card_link_text"])
    return f"{title}\n{price}\n{link}"


def render_list(header, car_ids, cars, cfg, username):
    """Заголовок и блоки авто, длинный список режется на несколько сообщений."""
    blocks = [car_block(cars[c], cfg) for c in car_ids if c in cars]
    head = f"<b>{html.escape(header)}</b>"
    chunks, cur = [], head
    for block in blocks:
        if len(cur) + len(block) + 2 > MAX_LEN:
            chunks.append(cur)
            cur = block
        else:
            cur += "\n\n" + block
    chunks.append(cur)
    markup = back_markup(cfg, username)
    return [
        {"text": t, "reply_markup": markup if i == len(chunks) - 1 else None}
        for i, t in enumerate(chunks)
    ]


def render_empty(name, cfg, username, request_payload=None, category=False):
    if category:
        text = f"<b>{html.escape(cfg['empty_category_text'].format(name=name))}</b>"
        return [{"text": text, "reply_markup": back_markup(cfg, username)}]
    text = f"<b>{html.escape(cfg['empty_text'].format(name=name))}</b>\n\n"
    text += f'<a href="{start_link(username, request_payload)}">{html.escape(cfg["request_button"])}</a>'
    return [{"text": text, "reply_markup": back_markup(cfg, username)}]


def render_request(cfg, username):
    brand = f'<a href="{html.escape(cfg["brand_url"])}">{html.escape(cfg["brand_name"])}</a>'
    text = brand.join(html.escape(p) for p in cfg["request_thanks"].split("{brand}"))
    rows = []
    managers = [
        {"text": m["name"], "url": m["url"]} for m in cfg.get("managers", [])
    ]
    if managers:
        rows.append(managers)
    return [{"text": text, "reply_markup": back_markup(cfg, username, rows)}]


def manager_forms(m):
    """Имя менеджера в падежах для текстов: name, name_instr (кем), name_dat (кому)."""
    return {
        "name": m["name"],
        "instr": m.get("name_instr", m["name"]),
        "dat": m.get("name_dat", m["name"]),
    }


def render_followup(cfg, first_name, payload):
    """Сообщение тем, кому менеджер не может написать первым (нет @username)."""
    forms = manager_forms(cfg["managers"][0])
    brand = f'<a href="{html.escape(cfg["brand_url"])}">{html.escape(cfg["brand_name"])}</a>'
    text = html.escape(cfg["followup_text"]).format(
        first_name=html.escape(first_name), payload=html.escape(payload), brand=brand,
        manager=html.escape(forms["instr"]),
    )
    button = {
        "text": cfg["followup_button"].format(manager=forms["dat"]),
        "url": cfg["managers"][0]["url"],
    }
    return {"text": text, "reply_markup": {"inline_keyboard": [[button]]}}


class Followups:
    """Очередь отложенных сообщений, хранится в файле и переживает перезапуск."""

    def __init__(self, state_dir):
        self.path = Path(state_dir) / "followups.json"

    def _read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []

    def _write(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, chat_id, first_name, payload, due):
        items = self._read()
        if any(i["chat_id"] == chat_id and i["payload"] == payload for i in items):
            return False
        items.append({"chat_id": chat_id, "first_name": first_name, "payload": payload, "due": due})
        self._write(items)
        return True

    def pop_due(self, now):
        items = self._read()
        due = [i for i in items if i["due"] <= now]
        if due:
            self._write([i for i in items if i["due"] > now])
        return due


def followup_delay(cfg):
    return int(os.environ.get("FOLLOWUP_DELAY_SEC") or cfg.get("followup_delay_sec", 1200))


def send_due_followups(api, store, followups, now=None):
    cfg = store.config
    for item in followups.pop_due(time.time() if now is None else now):
        if not cfg.get("managers"):
            continue
        try:
            api.send(item["chat_id"], render_followup(cfg, item["first_name"], item["payload"]))
            log.info("followup %s -> %s", item["payload"], item["chat_id"])
        except Exception as e:  # пользователь мог заблокировать бота
            log.warning("followup %s: %s", item["chat_id"], e)


def handle_start(payload, store, username):
    """Возвращает (список ответов, запрос менеджеру или None)."""
    cfg, cat = store.config, store.catalog
    payload = (payload or "").strip()
    if payload in ("", "catalog"):
        return render_menu(cfg, username), None
    if payload == "howto":
        return render_howto(cfg, username), None
    if payload.startswith("cat_"):
        entry = cat["categories"].get(payload)
        if entry is None:
            return render_menu(cfg, username), None
        if not entry["cars"]:
            name = entry["header"].lstrip("🚗🔋⚡🏁🚐 ")
            return render_empty(name, cfg, username, category=True), None
        return render_list(entry["header"], entry["cars"], cat["cars"], cfg, username), None
    if payload.startswith("brand_"):
        key = payload[len("brand_"):]
        entry = cat["brands"].get(key)
        if entry is None:
            return render_menu(cfg, username), None
        if not entry["cars"]:
            return render_empty(key, cfg, username, request_payload=key), None
        return render_list(entry["header"], entry["cars"], cat["cars"], cfg, username), None
    return render_request(cfg, username), payload


class Api:
    def __init__(self, token):
        self.url = f"https://api.telegram.org/bot{token}/"

    def call(self, method, params=None, timeout=20):
        data = json.dumps(params or {}).encode()
        req = urllib.request.Request(
            self.url + method, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            body = json.load(e)
        if not body.get("ok"):
            raise RuntimeError(f"{method}: {body.get('description')}")
        return body["result"]

    def send(self, chat_id, msg):
        params = {
            "chat_id": chat_id,
            "text": msg["text"],
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if msg.get("reply_markup"):
            params["reply_markup"] = msg["reply_markup"]
        return self.call("sendMessage", params)


def who(user):
    name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")]))
    handle = f"@{user['username']}" if user.get("username") else "без username"
    return f"{name} ({handle}, id {user['id']})"


def process_update(api, store, username, admins, followups, update):
    message = update.get("message")
    if not message or message.get("chat", {}).get("type") != "private":
        return
    parts = (message.get("text") or "").split(maxsplit=1)
    if not parts or parts[0].split("@")[0] != "/start":
        return
    payload = parts[1] if len(parts) > 1 else ""
    replies, request = handle_start(payload, store, username)
    for reply in replies:
        api.send(message["chat"]["id"], reply)
    if request is not None:
        note = f"📝 Запрос из бота: <b>{html.escape(request)}</b>\nОт: {html.escape(who(message['from']))}"
        log.info("request %s from %s", request, who(message["from"]))
        for admin in admins:
            try:
                api.send(admin, {"text": note})
            except Exception as e:  # админ мог не запускать бота
                log.warning("admin %s: %s", admin, e)
        user = message["from"]
        cfg = store.config
        if not user.get("username") and cfg.get("managers"):
            due = time.time() + followup_delay(cfg)
            followups.add(message["chat"]["id"], user.get("first_name", ""), request, due)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env()
    token = os.environ.get("BOT_TOKEN")
    if not token:
        sys.exit("BOT_TOKEN не задан (переменная окружения или .env)")
    admins = [int(x) for x in os.environ.get("ADMIN_CHAT_IDS", "").replace(" ", "").split(",") if x]
    api = Api(token)
    store = Store(DATA)
    followups = Followups(STATE)
    username = api.call("getMe")["username"]
    log.info("запущен @%s, админов %d", username, len(admins))
    offset = 0
    while True:
        try:
            for update in api.call("getUpdates", {"offset": offset, "timeout": 20, "allowed_updates": ["message"]}, timeout=40):
                offset = update["update_id"] + 1
                try:
                    process_update(api, store, username, admins, followups, update)
                except Exception:
                    log.exception("update %s", update.get("update_id"))
        except Exception as e:
            log.warning("getUpdates: %s", e)
            time.sleep(3)
        try:
            send_due_followups(api, store, followups)
        except Exception:
            log.exception("followups")


if __name__ == "__main__":
    main()
