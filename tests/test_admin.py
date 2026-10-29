import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import admin
import bot

U = "nezacars_bot"
ADMIN, STRANGER, CHAT = 1001, 2002, 1001

real_cfg = bot.Store(bot.DATA).config
CATALOG = {
    "cars": {
        "a": {"title": "Alpha One", "price": 1250000, "url": "https://t.me/ch/1"},
        "b": {"title": "Beta", "price": None, "url": "https://t.me/ch/2"},
        "c": {"title": "Gamma", "price": 5000000, "url": "https://t.me/ch/3"},
    },
    "categories": {
        "cat_pickup": {"header": "🚖 Пикапы", "cars": ["a", "b"]},
        "cat_sedan": {"header": "🚖 Седаны", "cars": ["c"]},
    },
    "brands": {
        "Alpha": {"header": "🚖 Alpha", "cars": ["a", "c"]},
        "Beta": {"header": "🚖 Beta", "cars": ["b"]},
    },
}


class Api:
    def __init__(self):
        self.calls = []
        self.next_id = 100

    def send(self, chat, msg):
        self.next_id += 1
        self.calls.append(("send", chat, msg))
        return {"message_id": self.next_id}

    def edit(self, chat, message_id, msg):
        self.calls.append(("edit", chat, msg))

    def delete(self, chat, message_id):
        self.calls.append(("delete", chat, message_id))

    def answer(self, cb_id, text=""):
        self.calls.append(("answer", cb_id, text))

    def last_text(self):
        return next(c[2]["text"] for c in reversed(self.calls) if c[0] in ("send", "edit"))

    def last_buttons(self):
        msg = next(c[2] for c in reversed(self.calls) if c[0] in ("send", "edit"))
        return [b for row in msg["reply_markup"]["inline_keyboard"] for b in row]


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        (d / "data").mkdir()
        (d / "data" / "config.json").write_text(json.dumps(real_cfg, ensure_ascii=False), encoding="utf-8")
        (d / "data" / "catalog.json").write_text(json.dumps(CATALOG, ensure_ascii=False), encoding="utf-8")
        self.store = bot.Store(d / "data", d / "state")
        self.admin = admin.Admin(self.store, {ADMIN})
        self.api = Api()
        self.n = 0

    def say(self, text, user=ADMIN, **extra):
        self.n += 1
        msg = {"message_id": self.n, "chat": {"id": CHAT, "type": "private"}, "from": {"id": user, "first_name": "Катя"}, "text": text, **extra}
        bot.process_update(self.api, self.store, U, {"message": msg}, self.admin)

    def press(self, data, user=ADMIN):
        cb = {"id": "cb", "from": {"id": user, "first_name": "Катя"}, "data": data,
              "message": {"message_id": 100, "chat": {"id": CHAT, "type": "private"}}}
        bot.process_update(self.api, self.store, U, {"callback_query": cb}, self.admin)

    def shown(self):
        return bot.handle_start("cat_pickup", self.store, U)[0]["text"]

    def titles(self):
        return [c["title"] for c in self.store.catalog["cars"].values()]


class AccessTest(Case):
    def test_stranger_gets_nothing(self):
        self.say("/admin", user=STRANGER)
        self.press("a:l:all:0", user=STRANGER)
        self.assertEqual(self.api.calls, [])
        self.assertFalse((Path(self.tmp.name) / "state").exists())

    def test_admin_opens_panel(self):
        self.say("/admin")
        self.assertIn("Админка", self.api.last_text())
        self.assertIn("В каталоге: 3", self.api.last_text())

    def test_admin_start_still_gives_menu(self):
        self.say("/start")
        self.assertIn("Каталог автомобилей", self.api.last_text())

    def test_without_admin_object_callbacks_are_ignored(self):
        cb = {"id": "x", "from": {"id": ADMIN}, "data": "a:m", "message": {"message_id": 1, "chat": {"id": CHAT, "type": "private"}}}
        bot.process_update(self.api, self.store, U, {"callback_query": cb})
        self.assertEqual(self.api.calls, [])


class StatusTest(Case):
    def test_hide_show_archive(self):
        self.press("a:s:a:hidden")
        self.assertNotIn("Alpha One", self.shown())
        self.assertIn("Beta", self.shown())
        self.assertEqual(self.store.full_catalog["cars"]["a"]["status"], "hidden")
        self.press("a:s:a:show")
        self.assertIn("Alpha One", self.shown())
        self.press("a:s:a:archived")
        self.assertNotIn("Alpha One", self.shown())
        self.assertIn("в архиве", self.api.last_text())
        self.assertIn("👁 Вернуть в каталог", [b["text"] for b in self.api.last_buttons()])

    def test_hidden_listed_in_admin_scopes(self):
        self.press("a:s:a:hidden")
        self.press("a:s:b:archived")
        self.press("a:l:hid:0")
        self.assertIn("Alpha One", [b["text"][2:] for b in self.api.last_buttons()])
        self.press("a:l:arc:0")
        self.assertIn("Beta", [b["text"][2:] for b in self.api.last_buttons()])

    def test_all_hidden_gives_empty_reply(self):
        self.press("a:s:a:hidden")
        self.press("a:s:b:hidden")
        self.assertIn("нет автомобилей", self.shown())

    def test_base_catalog_untouched(self):
        self.press("a:s:a:hidden")
        on_disk = json.loads((Path(self.tmp.name) / "data" / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk, CATALOG)


class EditTest(Case):
    def test_title_price_url(self):
        self.press("a:e:a:title")
        self.say("Alpha Two")
        self.press("a:e:a:price")
        self.say("1 300 000")
        self.press("a:e:a:url")
        self.say("https://t.me/ch/77")
        car = self.store.catalog["cars"]["a"]
        self.assertEqual((car["title"], car["price"], car["url"]), ("Alpha Two", 1300000, "https://t.me/ch/77"))
        self.assertIn("Alpha Two", self.shown())

    def test_price_on_request(self):
        self.press("a:e:a:price")
        self.say("-")
        self.assertIsNone(self.store.catalog["cars"]["a"]["price"])
        self.assertIn("цена по запросу", self.shown())

    def test_bad_input_is_rejected_and_nothing_saved(self):
        self.press("a:e:a:price")
        self.say("дорого")
        self.assertIn("Не получилось", self.api.last_text())
        self.press("a:e:a:url")
        self.say("http://example.com")
        self.assertEqual(self.store.catalog["cars"]["a"]["price"], 1250000)
        self.assertEqual(self.store.catalog["cars"]["a"]["url"], "https://t.me/ch/1")

    def test_text_without_pending_input_is_ignored(self):
        self.say("привет")
        self.assertEqual([c for c in self.api.calls if c[0] == "send"], [])

    def test_html_in_title_is_escaped_in_replies(self):
        self.press("a:e:a:title")
        self.say("<b>X")
        self.assertIn("&lt;b&gt;X", self.shown())

    def test_move_category_and_brand(self):
        self.press("a:mv:a:c:1")
        self.assertIn("Alpha One", bot.handle_start("cat_sedan", self.store, U)[0]["text"])
        self.assertNotIn("Alpha One", self.shown())
        self.press("a:mv:a:b:1")
        self.assertIn("Alpha One", bot.handle_start("brand_Beta", self.store, U)[0]["text"])
        self.press("a:mv:a:b:-")
        self.assertNotIn("Alpha One", bot.handle_start("brand_Beta", self.store, U)[0]["text"])


class SearchTest(Case):
    def buttons(self):
        return [b["text"][2:] for b in self.api.last_buttons() if b["callback_data"].startswith("a:c:")]

    def test_search_by_title_part(self):
        self.press("a:q")
        self.say("alp")
        self.assertEqual(self.buttons(), ["Alpha One"])
        self.assertIn("Поиск: alp", self.api.last_text())

    def test_search_is_case_insensitive_and_word_based(self):
        self.press("a:q")
        self.say("ONE alpha")
        self.assertEqual(self.buttons(), ["Alpha One"])

    def test_search_finds_hidden_and_archived(self):
        self.press("a:s:a:hidden")
        self.press("a:s:b:archived")
        self.press("a:q")
        self.say("a")
        self.assertEqual(sorted(self.buttons()), ["Alpha One", "Beta", "Gamma"])

    def test_nothing_found(self):
        self.press("a:q")
        self.say("zzz")
        self.assertIn("Ничего не найдено", self.api.last_text())

    def test_card_from_search_goes_back_to_results(self):
        self.press("a:q")
        self.say("gam")
        self.press("a:c:c")
        back = [b for b in self.api.last_buttons() if b["text"] == "◀ К списку"][0]["callback_data"]
        self.assertEqual(back, "a:l:s:0")
        self.press(back)
        self.assertEqual(self.buttons(), ["Gamma"])

    def test_search_button_in_menu_and_lists(self):
        self.say("/admin")
        self.assertIn("a:q", [b["callback_data"] for b in self.api.last_buttons()])
        self.press("a:l:all:0")
        self.assertIn("a:q", [b["callback_data"] for b in self.api.last_buttons()])

    def test_yo_and_hyphen(self):
        self.assertTrue(admin.title_matches("Ёлка-Пикап", "елка пикап"))
        self.assertFalse(admin.title_matches("Alpha", "beta"))


class DeleteUndoTest(Case):
    def test_delete_needs_confirmation(self):
        self.press("a:d:a")
        self.assertIn("Alpha One", self.titles())
        self.press("a:dd:a")
        self.assertNotIn("Alpha One", self.titles())
        self.assertNotIn("a", self.store.full_catalog["cars"])

    def test_undo_delete_status_and_edit(self):
        self.press("a:dd:a")
        self.press("a:u")
        self.assertIn("Alpha One", self.titles())
        self.press("a:s:b:hidden")
        self.press("a:u")
        self.assertIn("Beta", self.titles())
        self.press("a:e:c:title")
        self.say("Gamma 2")
        self.press("a:u")
        self.assertIn("Gamma", self.titles())
        self.assertEqual(self.store.edits["cars"], {})
        self.assertEqual(self.store.edits["deleted"], [])

    def test_journal_lists_changes(self):
        self.press("a:s:a:hidden")
        self.press("a:g")
        self.assertIn("Скрыто: Alpha One", self.api.last_text())
        self.assertIn("Катя", self.api.last_text())

    def test_undo_with_empty_log(self):
        self.press("a:u")
        self.assertIn("Админка", self.api.last_text())


class AddTest(Case):
    def finish(self):
        self.press("a:np:ok")
        self.press("a:np:c:0")
        self.press("a:np:b:1")

    def test_add_by_link(self):
        self.press("a:n")
        self.say("https://t.me/ch/9")
        self.say("Delta")
        self.say("2 000 000")
        self.assertIn("проверьте", self.api.last_text())
        self.finish()
        self.assertIn("Delta", self.shown())
        self.assertIn("от 2 000 000", self.shown())
        self.assertIn("Delta", bot.handle_start("brand_Beta", self.store, U)[0]["text"])
        self.assertIn("https://t.me/ch/9", self.shown())

    def test_manual_add_link_last(self):
        self.press("a:n")
        self.assertIn("a:nm", [b["callback_data"] for b in self.api.last_buttons()])
        self.press("a:nm")
        self.say("Delta")
        self.say("2 000 000")
        self.assertIn("Пришлите ссылку", self.api.last_text())
        self.say("https://t.me/ch/9")
        self.assertIn("проверьте", self.api.last_text())
        self.assertIn("https://t.me/ch/9", self.api.last_text())
        self.finish()
        self.assertIn('<a href="https://t.me/ch/9">Открыть карточку</a>', self.shown())

    def test_manual_add_without_link(self):
        self.press("a:nm")
        self.say("Delta")
        self.say("-")
        self.press("a:np:nu")
        self.assertIn("Ссылка: нет", self.api.last_text())
        self.finish()
        self.assertIn("🚗 <b>Delta</b>\nцена по запросу\nОткрыть карточку\n", self.shown())

    def test_manual_add_bad_link_asks_again_and_duplicate_rejected(self):
        self.press("a:nm")
        self.say("Delta")
        self.say("1000000")
        self.say("не ссылка")
        self.assertIn("Не получилось", self.api.last_text())
        self.assertEqual(len(self.store.full_catalog["cars"]), 3)

    def test_link_can_be_changed_in_preview(self):
        self.press("a:nm")
        self.say("Delta")
        self.say("1000000")
        self.press("a:np:nu")
        self.press("a:np:u")
        self.say("https://t.me/ch/55")
        self.assertIn("https://t.me/ch/55", self.api.last_text())

    def test_duplicate_link_not_added(self):
        self.press("a:n")
        self.say("https://t.me/ch/1")
        self.assertIn("уже есть", self.api.last_text())
        self.assertEqual(len(self.store.full_catalog["cars"]), 3)

    def test_added_car_can_be_hidden_deleted_and_undone(self):
        self.press("a:n")
        self.say("https://t.me/ch/9")
        self.say("Delta")
        self.say("-")
        self.finish()
        cid = next(c for c in self.store.full_catalog["cars"] if c.startswith("new-"))
        self.press(f"a:s:{cid}:hidden")
        self.assertNotIn("Delta", self.shown())
        self.press(f"a:dd:{cid}")
        self.assertNotIn(cid, self.store.full_catalog["cars"])
        self.press("a:u")
        self.press("a:u")
        self.assertIn(cid, self.store.full_catalog["cars"])
        self.assertIn("Delta", self.shown())


POST = (
    "Отдал 100к - а через 45-60 дней уже сидишь за рулем\n"
    "☄️\nOTING Паладин Prestige в продаже\n\n"
    "Было 4 570 000 ₽\nДля подписчиков - 3 449 000 ₽\n"
    "Предоплата - 100к, ждать - полтора месяца."
)


class ForwardTest(Case):
    def forward(self, text=POST, channel="Nezavisimost_sklad", post=61):
        origin = {"type": "channel", "chat": {"id": -1, "username": channel, "type": "channel"}, "message_id": post}
        self.say(text, forward_origin=origin, forward_date=1)

    def test_guesses(self):
        self.assertEqual(admin.guess_title(POST), "OTING Паладин Prestige")
        self.assertEqual(admin.guess_price(POST), 3449000)
        self.assertEqual(admin.guess_title("☄️\nУАЗ Пикап\nцена"), "УАЗ Пикап")
        self.assertIsNone(admin.guess_price("Предоплата 100к, цена по запросу"))
        self.assertEqual(admin.guess_title(""), "")

    def test_forward_builds_draft_from_post(self):
        self.forward()
        text = self.api.last_text()
        self.assertIn("OTING Паладин Prestige", text)
        self.assertIn("https://t.me/Nezavisimost_sklad/61", text)
        self.assertIn("3 449 000", text)
        self.assertIn(("delete", CHAT, self.n), self.api.calls)

    def test_forward_then_add(self):
        self.forward()
        self.press("a:np:ok")
        self.press("a:np:c:1")
        self.press("a:np:b:0")
        car = next(c for c in self.store.catalog["cars"].values() if c["url"].endswith("/61"))
        self.assertEqual((car["title"], car["price"]), ("OTING Паладин Prestige", 3449000))
        self.assertIn("OTING", bot.handle_start("cat_sedan", self.store, U)[0]["text"])

    def test_forward_then_fix_title_and_price(self):
        self.forward()
        self.press("a:np:t")
        self.say("Oting Paladin")
        self.assertIn("проверьте", self.api.last_text())
        self.press("a:np:p")
        self.say("3 500 000")
        self.assertIn("Oting Paladin", self.api.last_text())
        self.assertIn("3 500 000", self.api.last_text())

    def test_forward_of_existing_post(self):
        self.forward(channel="ch", post=1)
        self.assertIn("уже есть", self.api.last_text())

    def test_forward_without_channel_username_asks_for_link(self):
        self.say("текст", forward_origin={"type": "hidden_user", "sender_user_name": "x"}, forward_date=1)
        self.assertIn("Пришлите ссылку", self.api.last_text())
        self.say("https://t.me/ch/50")
        self.assertIn("Пришлите название", self.api.last_text())

    def test_forward_from_stranger_is_ignored(self):
        self.api.calls.clear()
        origin = {"type": "channel", "chat": {"id": -1, "username": "x"}, "message_id": 5}
        self.say("t", user=STRANGER, forward_origin=origin, forward_date=1)
        self.assertEqual(self.api.calls, [])


class ParsePriceTest(unittest.TestCase):
    def test_values(self):
        self.assertEqual(admin.parse_price("1250000"), 1250000)
        self.assertEqual(admin.parse_price("1 250 000 ₽"), 1250000)
        self.assertEqual(admin.parse_price("890000 руб."), 890000)
        for empty in ("-", "0", ""):
            self.assertIsNone(admin.parse_price(empty))
        for bad in ("abc", "12k", "-5", "1.5"):
            with self.assertRaises(ValueError, msg=bad):
                admin.parse_price(bad)


class MergeTest(unittest.TestCase):
    def test_no_edits_equals_base(self):
        self.assertEqual(bot.merge_catalog(CATALOG, bot.empty_edits()), CATALOG)
        real = bot.Store(bot.DATA)
        self.assertEqual(real.full_catalog, json.loads((bot.DATA / "catalog.json").read_text(encoding="utf-8")))
        self.assertEqual(real.catalog, real.full_catalog)


if __name__ == "__main__":
    unittest.main()
