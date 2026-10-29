"""Админ-панель бота: команда /admin, кнопки в одном сообщении, правки в state/edits.json.

Скрыть, архив и удалить меняют только слой правок, catalog.json из git не трогается.
Каждое изменение пишется в журнал, последнее можно отменить.
"""
import copy
import html
import logging
import re
import time

import bot

log = logging.getLogger("nezacars")

PAGE = 8
STATUS_ICON = {"hidden": "🙈", "archived": "📦"}
URL_RE = re.compile(r"^https://t\.me/\w+/\d+$")
FIELDS = {
    "title": ("название", "Пришлите новое название."),
    "price": ("цену", "Пришлите цену числом, например 1250000. «-» это «цена по запросу»."),
    "url": ("ссылку", "Пришлите ссылку на пост, например https://t.me/Nezavisimost_sklad/61."),
}


def esc(text):
    return html.escape(str(text))


def kb(*rows):
    return {"inline_keyboard": [list(r) for r in rows if r]}


def btn(text, data):
    return {"text": text, "callback_data": data}


def parse_price(text):
    """«1 250 000» -> 1250000; «-», «0» и пустое -> None (цена по запросу). Мусор -> ValueError."""
    text = re.sub(r"(?i)\s*(₽|руб\.?|р\.?)$", "", text.strip())
    if text in ("-", "0", ""):
        return None
    if not re.fullmatch(r"\d[\d\s]*", text):
        raise ValueError(text)
    return int(re.sub(r"\s", "", text))


def forwarded_post(message):
    """Ссылка на пост канала из пересланного сообщения или None."""
    origin = message.get("forward_origin") or {}
    chat = origin.get("chat") if origin.get("type") == "channel" else message.get("forward_from_chat")
    mid = origin.get("message_id") or message.get("forward_from_message_id")
    if chat and chat.get("username") and mid:
        return f"https://t.me/{chat['username']}/{mid}"
    return None


def is_forward(message):
    return bool(message.get("forward_origin") or message.get("forward_from_chat") or message.get("forward_date"))


def guess_title(text):
    """Название из текста поста: строка «... в продаже/наличии», иначе первая строка."""
    lines = [re.sub(r"^[^\w]+", "", l).strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    for line in lines:
        m = re.match(r"(.+?)\s+в\s+(?:продаже|наличии)\b", line, re.I)
        if m:
            return m.group(1).strip()[:100]
    return lines[0][:100] if lines else ""


def guess_price(text):
    """Самая низкая сумма в рублях от 100 000 (в постах рядом «Было» и цена для подписчиков)."""
    sums = [
        int(re.sub(r"\s", "", m))
        for m in re.findall(r"(\d[\d\s]*\d)\s*(?:₽|руб|р\.)", text)
    ]
    sums = [s for s in sums if s >= 100000]
    return min(sums) if sums else None


class Admin:
    def __init__(self, store, admin_ids):
        self.store = store
        self.admins = set(admin_ids)
        self.state = {}  # chat_id -> {"await", "car", "draft", "panel", "scope", "page"}
        self.back = "all:0"  # куда ведёт «К списку» у текущей карточки

    # --- входящие ---

    def handle_message(self, api, message):
        """True, если сообщение обработано админкой."""
        user = message.get("from", {}).get("id")
        if user not in self.admins:
            return False
        chat = message["chat"]["id"]
        text = (message.get("text") or "").strip()
        st = self.state.setdefault(chat, {})
        if is_forward(message):
            self.take_forward(api, chat, message, st)
            return True
        command = text.split(maxsplit=1)[0].split("@")[0] if text.startswith("/") else ""
        if command == "/admin":
            st["await"] = None
            self.show(api, chat, self.main_menu(), new=True)
            return True
        if command:
            st["await"] = None
            return False
        if not st.get("await") or not text:
            return False
        self.take_input(api, chat, message, text)
        return True

    def handle_callback(self, api, cb):
        if cb.get("from", {}).get("id") not in self.admins or not cb.get("message"):
            return
        chat = cb["message"]["chat"]["id"]
        st = self.state.setdefault(chat, {})
        st["panel"] = cb["message"]["message_id"]
        st["await"] = None
        self.back = f"{st.get('scope', 'all')}:{st.get('page', 0)}"
        notice = ""
        try:
            msg, notice = self.route(chat, st, cb.get("data", ""), cb["from"])
        except KeyError:
            msg, notice = self.main_menu(), "Авто уже нет"
        try:
            api.answer(cb["id"], notice)
        except RuntimeError:
            pass
        self.show(api, chat, msg)

    # --- вывод ---

    def show(self, api, chat, msg, new=False):
        st = self.state.setdefault(chat, {})
        if not new and st.get("panel"):
            try:
                api.edit(chat, st["panel"], msg)
                return
            except RuntimeError as e:
                if "not modified" in str(e):
                    return
                log.warning("edit панели: %s", e)
        st["panel"] = api.send(chat, msg)["message_id"]

    # --- маршрутизация кнопок ---

    def route(self, chat, st, data, who):
        parts = data.split(":")
        if parts[0] != "a" or len(parts) < 2:
            return self.main_menu(), ""
        act, args = parts[1], parts[2:]
        name = who.get("first_name") or str(who.get("id"))
        if act == "m":
            return self.main_menu(), ""
        if act == "l":
            st["scope"], st["page"] = args[0], int(args[1])
            return self.list_view(st["scope"], st["page"]), ""
        if act == "c":
            return self.card(args[0]), ""
        if act == "e":
            st["await"], st["car"] = args[1], args[0]
            label, hint = FIELDS[args[1]]
            return self.prompt(f"Изменить {label}: <b>{esc(self.car(args[0])['title'])}</b>\n\n{hint}", f"a:c:{args[0]}"), ""
        if act == "s":
            return self.set_status(args[0], args[1], name)
        if act == "d":
            car = self.car(args[0])
            return {
                "text": f"Удалить <b>{esc(car['title'])}</b>?\n\nЕсли передумаете, последнее действие можно отменить в журнале.",
                "reply_markup": kb([btn("🗑 Да, удалить", f"a:dd:{args[0]}")], [btn("◀ Отмена", f"a:c:{args[0]}")]),
            }, ""
        if act == "dd":
            return self.delete(args[0], name)
        if act == "mv":
            return self.move(args, name)
        if act == "n":
            st["await"], st["draft"] = "new_url", {}
            return self.prompt("Новое авто.\n\nПришлите ссылку на пост, например https://t.me/Nezavisimost_sklad/61.", "a:m"), ""
        if act == "np":
            return self.new_pick(st, args, name)
        if act == "g":
            return self.journal(), ""
        if act == "u":
            return self.undo(name)
        return self.main_menu(), ""

    # --- данные ---

    def full(self):
        return self.store.full_catalog

    def car(self, cid):
        return self.full()["cars"][cid]

    def where(self, full, cid, group):
        for key, entry in full[group].items():
            if cid in entry["cars"]:
                return key, bot.plain_name(entry["header"])
        return None, "нет"

    def change(self, name, cid, text, fn):
        """Применяет правку к слою и пишет журнал с состоянием до неё."""
        edits = copy.deepcopy(self.store.edits)
        before = {"patch": copy.deepcopy(edits["cars"].get(cid)), "deleted": cid in edits["deleted"]}
        fn(edits)
        edits["log"].append({"at": time.strftime("%d.%m %H:%M"), "by": name, "text": text, "car": cid, "before": before})
        edits["log"] = edits["log"][-200:]
        self.store.save_edits(edits)

    # --- экраны ---

    def main_menu(self):
        full = self.full()
        counts = {"": 0, "hidden": 0, "archived": 0}
        for c in full["cars"].values():
            counts[c.get("status", "")] += 1
        text = (
            "<b>Админка</b>\n\n"
            f"В каталоге: {counts['']}\nСкрыто: {counts['hidden']}\nВ архиве: {counts['archived']}"
        )
        rows = [
            [btn("📋 Все авто", "a:l:all:0")],
            [btn("➕ Добавить авто", "a:n")],
            [btn(f"🙈 Скрытые ({counts['hidden']})", "a:l:hid:0"), btn(f"📦 Архив ({counts['archived']})", "a:l:arc:0")],
            [btn("📂 По категориям", "a:l:cats:0"), btn("🏷 По маркам", "a:l:brands:0")],
            [btn("🕘 Журнал", "a:g")],
        ]
        return {"text": text, "reply_markup": kb(*rows)}

    def scope_cars(self, full, scope):
        if scope == "all":
            return "Все авто", list(full["cars"])
        if scope in ("hid", "arc"):
            status = "hidden" if scope == "hid" else "archived"
            title = "Скрытые" if scope == "hid" else "Архив"
            return title, [c for c, v in full["cars"].items() if v.get("status") == status]
        group = {"c": "categories", "b": "brands"}[scope[0]]
        key = list(full[group])[int(scope[1:])]
        return bot.plain_name(full[group][key]["header"]), full[group][key]["cars"]

    def list_view(self, scope, page):
        full = self.full()
        if scope in ("cats", "brands"):
            group = "categories" if scope == "cats" else "brands"
            rows = []
            for i, entry in enumerate(full[group].values()):
                rows.append([btn(f"{bot.plain_name(entry['header'])} ({len(entry['cars'])})", f"a:l:{scope[0]}{i}:0")])
            rows.append([btn("◀ Назад", "a:m")])
            return {"text": "<b>Категории</b>" if scope == "cats" else "<b>Марки</b>", "reply_markup": kb(*rows)}
        title, ids = self.scope_cars(full, scope)
        pages = max(1, -(-len(ids) // PAGE))
        page = min(max(page, 0), pages - 1)
        rows = []
        for cid in ids[page * PAGE:(page + 1) * PAGE]:
            car = full["cars"][cid]
            icon = STATUS_ICON.get(car.get("status"), "🚗")
            rows.append([btn(f"{icon} {car['title']}", f"a:c:{cid}")])
        nav = []
        if page > 0:
            nav.append(btn("◀", f"a:l:{scope}:{page - 1}"))
        if page < pages - 1:
            nav.append(btn("▶", f"a:l:{scope}:{page + 1}"))
        rows.append(nav)
        rows.append([btn("◀ В меню", "a:m")])
        text = f"<b>{esc(title)}</b>: {len(ids)}" + (f"\nСтраница {page + 1} из {pages}" if pages > 1 else "")
        if not ids:
            text += "\n\nПусто."
        return {"text": text, "reply_markup": kb(*rows)}

    def card(self, cid, note=""):
        full = self.full()
        car = full["cars"][cid]
        cfg = self.store.config
        status = car.get("status")
        state = {"hidden": "скрыто", "archived": "в архиве"}.get(status, "показывается клиентам")
        if status and car.get("status_at"):
            state += f" с {car['status_at']}"
        text = (
            f"<b>{esc(car['title'])}</b>\n"
            f"Цена: {esc(bot.fmt_price(car.get('price'), cfg))}\n"
            f"Ссылка: {esc(car.get('url') or 'нет')}\n"
            f"Категория: {esc(self.where(full, cid, 'categories')[1])}\n"
            f"Марка: {esc(self.where(full, cid, 'brands')[1])}\n"
            f"Статус: {state}"
        )
        if note:
            text = f"{note}\n\n{text}"
        if status is None:
            status_row = [btn("🙈 Скрыть", f"a:s:{cid}:hidden"), btn("📦 В архив", f"a:s:{cid}:archived")]
        elif status == "hidden":
            status_row = [btn("👁 Показать", f"a:s:{cid}:show"), btn("📦 В архив", f"a:s:{cid}:archived")]
        else:
            status_row = [btn("👁 Вернуть в каталог", f"a:s:{cid}:show"), btn("🙈 Скрыть", f"a:s:{cid}:hidden")]
        rows = [
            [btn("✏ Название", f"a:e:{cid}:title"), btn("💰 Цена", f"a:e:{cid}:price")],
            [btn("🔗 Ссылка", f"a:e:{cid}:url")],
            [btn("📂 Категория", f"a:mv:{cid}:c"), btn("🏷 Марка", f"a:mv:{cid}:b")],
            status_row,
            [btn("🗑 Удалить", f"a:d:{cid}")],
            [btn("◀ К списку", f"a:l:{self.back}")],
        ]
        return {"text": text, "reply_markup": kb(*rows)}

    def prompt(self, text, back):
        return {"text": text, "reply_markup": kb([btn("◀ Отмена", back)])}

    # --- действия ---

    def set_status(self, cid, status, name):
        car = self.car(cid)
        today = time.strftime("%d.%m.%Y")

        def fn(edits):
            patch = edits["cars"].setdefault(cid, {})
            if status == "show":
                patch.pop("status", None)
                patch.pop("status_at", None)
            else:
                patch["status"], patch["status_at"] = status, today

        label = {"show": "Показано", "hidden": "Скрыто", "archived": "В архиве"}[status]
        self.change(name, cid, f"{label}: {car['title']}", fn)
        return self.card(cid, f"✅ {label}"), label

    def delete(self, cid, name):
        car = self.car(cid)
        self.change(name, cid, f"Удалено: {car['title']}", lambda e: e["deleted"].append(cid))
        return {
            "text": f"🗑 Удалено: <b>{esc(car['title'])}</b>",
            "reply_markup": kb([btn("↩ Отменить", "a:u")], [btn("◀ В меню", "a:m")]),
        }, "Удалено"

    def move(self, args, name):
        cid, kind = args[0], args[1]
        full = self.full()
        group = "categories" if kind == "c" else "brands"
        field = "category" if kind == "c" else "brand"
        if len(args) == 2:
            rows = [[btn(bot.plain_name(e["header"]), f"a:mv:{cid}:{kind}:{i}")] for i, e in enumerate(full[group].values())]
            rows.append([btn("Без " + ("категории" if kind == "c" else "марки"), f"a:mv:{cid}:{kind}:-")])
            rows.append([btn("◀ Отмена", f"a:c:{cid}")])
            what = "категорию" if kind == "c" else "марку"
            return {"text": f"Выберите {what}: <b>{esc(self.car(cid)['title'])}</b>", "reply_markup": kb(*rows)}, ""
        key = None if args[2] == "-" else list(full[group])[int(args[2])]
        title = self.car(cid)["title"]
        target = bot.plain_name(full[group][key]["header"]) if key else "нет"
        self.change(name, cid, f"Перенос: {title} -> {target}", lambda e: e["cars"].setdefault(cid, {}).update({field: key}))
        return self.card(cid, f"✅ Перенесено: {esc(target)}"), "Готово"

    def journal(self):
        log_ = self.store.edits["log"][-10:][::-1]
        lines = [f"{esc(e['at'])} {esc(e['by'])}: {esc(e['text'])}" for e in log_] or ["Пока пусто."]
        return {
            "text": "<b>Журнал</b> (последние 10)\n\n" + "\n".join(lines),
            "reply_markup": kb([btn("↩ Отменить последнее", "a:u")], [btn("◀ В меню", "a:m")]),
        }

    def undo(self, name):
        edits = copy.deepcopy(self.store.edits)
        if not edits["log"]:
            return self.main_menu(), "Отменять нечего"
        entry = edits["log"].pop()
        cid, before = entry["car"], entry["before"]
        if before["patch"] is None:
            edits["cars"].pop(cid, None)
        else:
            edits["cars"][cid] = before["patch"]
        if before["deleted"] and cid not in edits["deleted"]:
            edits["deleted"].append(cid)
        if not before["deleted"] and cid in edits["deleted"]:
            edits["deleted"].remove(cid)
        self.store.save_edits(edits)
        return self.journal(), f"Отменено: {entry['text']}"[:200]

    # --- ввод текста ---

    def take_input(self, api, chat, message, text):
        st = self.state[chat]
        mode = st["await"]
        name = message["from"].get("first_name") or str(message["from"]["id"])
        self.back = f"{st.get('scope', 'all')}:{st.get('page', 0)}"
        try:
            msg = self.apply_input(st, mode, text, name)
        except ValueError as e:
            msg = self.prompt(f"Не получилось: {esc(e)}. Попробуйте ещё раз.", "a:m")
        except KeyError:
            st["await"] = None
            msg = self.main_menu()
        try:
            api.delete(chat, message["message_id"])
        except (RuntimeError, AttributeError):
            pass
        self.show(api, chat, msg)

    def apply_input(self, st, mode, text, name):
        if mode in ("title", "price", "url"):
            cid = st["car"]
            value = self.check(mode, text)
            title = self.car(cid)["title"]
            st["await"] = None
            self.change(name, cid, f"Изменено ({FIELDS[mode][0]}): {title}", lambda e: e["cars"].setdefault(cid, {}).update({mode: value}))
            return self.card(cid, "✅ Сохранено")
        draft = st.setdefault("draft", {})
        if mode == "new_url":
            url = self.check("url", text)
            dup = self.duplicate(url)
            if dup:
                st["await"] = None
                return self.duplicate_view(dup)
            draft.clear()
            draft["url"] = url
            st["await"] = "new_title"
            return self.prompt("Пришлите название авто.", "a:m")
        if mode == "new_title":
            draft["title"] = self.check("title", text)
            if draft.get("ready"):
                st["await"] = None
                return self.draft_view(draft)
            st["await"] = "new_price"
            return self.prompt(FIELDS["price"][1], "a:m")
        if mode == "new_price":
            draft["price"] = self.check("price", text)
            draft["ready"] = True
            st["await"] = None
            return self.draft_view(draft)
        raise ValueError("неизвестный шаг")

    def check(self, field, text):
        text = text.strip()
        if field == "title":
            if not text or len(text) > 100:
                raise ValueError("название от 1 до 100 символов")
            return text
        if field == "url":
            if not URL_RE.match(text):
                raise ValueError("ссылка должна быть вида https://t.me/канал/123")
            return text
        try:
            return parse_price(text)
        except ValueError:
            raise ValueError("цена числом или «-»")

    def new_choice(self, group, draft):
        full = self.full()
        kind = "c" if group == "categories" else "b"
        rows = [[btn(bot.plain_name(e["header"]), f"a:np:{kind}:{i}")] for i, e in enumerate(full[group].values())]
        rows.append([btn("Без " + ("категории" if kind == "c" else "марки"), f"a:np:{kind}:-")])
        rows.append([btn("◀ Отмена", "a:m")])
        what = "категорию" if kind == "c" else "марку"
        return {"text": f"<b>{esc(draft['title'])}</b>\n\nВыберите {what}.", "reply_markup": kb(*rows)}

    def new_pick(self, st, args, name):
        draft = st.get("draft")
        if not draft or "title" not in draft:
            return self.main_menu(), "Начните заново"
        if args[0] == "v":
            return self.draft_view(draft), ""
        if args[0] == "ok":
            return self.new_choice("categories", draft), ""
        if args[0] in ("t", "p"):
            st["await"] = "new_title" if args[0] == "t" else "new_price"
            hint = "Пришлите название авто." if args[0] == "t" else FIELDS["price"][1]
            return self.prompt(hint, "a:np:v"), ""
        kind, idx = args
        full = self.full()
        group = "categories" if kind == "c" else "brands"
        key = None if idx == "-" else list(full[group])[int(idx)]
        draft["category" if kind == "c" else "brand"] = key
        if kind == "c":
            return self.new_choice("brands", draft), ""
        cid = f"new-{int(time.time())}"
        patch = {k: draft[k] for k in ("title", "price", "url", "category", "brand")}
        self.change(name, cid, f"Добавлено: {draft['title']}", lambda e: e["cars"].__setitem__(cid, patch))
        st["draft"] = {}
        return self.card(cid, "✅ Добавлено"), "Добавлено"

    # --- добавление пересылкой ---

    def duplicate(self, url):
        return next((cid for cid, c in self.full()["cars"].items() if c.get("url") == url), None)

    def duplicate_view(self, cid):
        return {
            "text": f"Это авто уже есть: <b>{esc(self.car(cid)['title'])}</b>",
            "reply_markup": kb([btn("Открыть карточку", f"a:c:{cid}")], [btn("◀ В меню", "a:m")]),
        }

    def draft_view(self, draft):
        cfg = self.store.config
        text = (
            "<b>Новое авто, проверьте</b>\n\n"
            f"Название: {esc(draft['title'])}\n"
            f"Цена: {esc(bot.fmt_price(draft.get('price'), cfg))}\n"
            f"Ссылка: {esc(draft['url'])}"
        )
        return {
            "text": text,
            "reply_markup": kb(
                [btn("✅ Всё верно, дальше", "a:np:ok")],
                [btn("✏ Название", "a:np:t"), btn("💰 Цена", "a:np:p")],
                [btn("◀ Отмена", "a:m")],
            ),
        }

    def take_forward(self, api, chat, message, st):
        """Пост канала, пересланный боту: ссылка берётся из пересылки, название и цена из текста."""
        url = forwarded_post(message)
        try:
            api.delete(chat, message["message_id"])
        except (RuntimeError, AttributeError):
            pass
        if not url:
            st["await"], st["draft"] = "new_url", {}
            msg = self.prompt(
                "Не вижу ссылку: у канала нет публичного имени или пересылка закрыта.\n\nПришлите ссылку на пост текстом.",
                "a:m",
            )
            return self.show(api, chat, msg, new=True)
        dup = self.duplicate(url)
        if dup:
            st["await"] = None
            return self.show(api, chat, self.duplicate_view(dup), new=True)
        text = message.get("text") or message.get("caption") or ""
        title = guess_title(text)
        st["draft"] = {"url": url, "title": title or "Без названия", "price": guess_price(text), "ready": True}
        st["await"] = None
        self.show(api, chat, self.draft_view(st["draft"]), new=True)
