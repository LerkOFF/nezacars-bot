#!/usr/bin/env python3
"""Telegram-бот каталога автомобилей (клон @chinacars_market_bot).

Меню и ответы работают через deep-link `/start <payload>`:
  catalog / (пусто)  главное меню
  cat_<ключ>         список авто категории
  brand_<ключ>       список авто марки
  howto              «Как заказать»
  любое другое       главное меню

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
    return cfg.get("price_prefix", "") + f"{price:,}".replace(",", NBSP) + " ₽"


def plain_name(header):
    """Заголовок без ведущего эмодзи: «🚖 Peugeot» -> «Peugeot»."""
    return re.sub(r"^[^\w]+", "", header).strip()


def start_link(username, payload):
    return f"https://t.me/{username}?start={payload}"


def back_markup(cfg, username):
    """Кнопка «Вернуться в каталог» под сообщением; пустой back_button её отключает."""
    rows = []
    if cfg.get("back_button"):
        url = cfg.get("back_url") or start_link(username, "catalog")
        rows.append([{"text": cfg["back_button"], "url": url}])
    return {"inline_keyboard": rows} if rows else None


def footer_text(cfg):
    """Ссылки в конце сообщения («Заказать», «Вернуться в каталог») текстом, как в постах канала."""
    links = [
        f'<a href="{html.escape(l["url"])}">{html.escape(l["text"])}</a>'
        for l in cfg.get("footer_links", [])
    ]
    return "\n".join(links)


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
    footer = footer_text(cfg)
    limit = MAX_LEN - len(footer) - 2
    chunks, cur = [], head
    for block in blocks:
        if len(cur) + len(block) + 2 > limit:
            chunks.append(cur)
            cur = block
        else:
            cur += "\n\n" + block
    chunks.append(cur + ("\n\n" + footer if footer else ""))
    markup = back_markup(cfg, username)
    return [
        {"text": t, "reply_markup": markup if i == len(chunks) - 1 else None}
        for i, t in enumerate(chunks)
    ]


def render_empty(name, cfg, username, category=False):
    template = cfg["empty_category_text"] if category else cfg["empty_text"]
    text = f"<b>{html.escape(template.format(name=name))}</b>"
    footer = footer_text(cfg)
    if footer:
        text += "\n\n" + footer
    return [{"text": text, "reply_markup": back_markup(cfg, username)}]


def handle_start(payload, store, username):
    """Список ответов на `/start <payload>`; неизвестный payload открывает меню."""
    cfg, cat = store.config, store.catalog
    payload = (payload or "").strip()
    if payload in ("", "catalog"):
        return render_menu(cfg, username)
    if payload == "howto":
        return render_howto(cfg, username)
    if payload.startswith("cat_"):
        entry = cat["categories"].get(payload)
        if entry is None:
            return render_menu(cfg, username)
        if not entry["cars"]:
            return render_empty(plain_name(entry["header"]), cfg, username, category=True)
        return render_list(entry["header"], entry["cars"], cat["cars"], cfg, username)
    if payload.startswith("brand_"):
        key = payload[len("brand_"):]
        entry = cat["brands"].get(key)
        if entry is None:
            return render_menu(cfg, username)
        if not entry["cars"]:
            return render_empty(plain_name(entry["header"]), cfg, username)
        return render_list(entry["header"], entry["cars"], cat["cars"], cfg, username)
    return render_menu(cfg, username)


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


def process_update(api, store, username, update):
    message = update.get("message")
    if not message or message.get("chat", {}).get("type") != "private":
        return
    parts = (message.get("text") or "").split(maxsplit=1)
    if not parts or parts[0].split("@")[0] != "/start":
        return
    payload = parts[1] if len(parts) > 1 else ""
    for reply in handle_start(payload, store, username):
        api.send(message["chat"]["id"], reply)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env()
    token = os.environ.get("BOT_TOKEN")
    if not token:
        sys.exit("BOT_TOKEN не задан (переменная окружения или .env)")
    api = Api(token)
    store = Store(DATA)
    username = api.call("getMe")["username"]
    log.info("запущен @%s", username)
    offset = 0
    while True:
        try:
            for update in api.call("getUpdates", {"offset": offset, "timeout": 20, "allowed_updates": ["message"]}, timeout=40):
                offset = update["update_id"] + 1
                try:
                    process_update(api, store, username, update)
                except Exception:
                    log.exception("update %s", update.get("update_id"))
        except Exception as e:
            log.warning("getUpdates: %s", e)
            time.sleep(3)


if __name__ == "__main__":
    main()
