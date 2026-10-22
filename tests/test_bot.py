import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bot

U = "nezacars_bot"
store = bot.Store(bot.DATA)
cfg = store.config


def start(payload):
    return bot.handle_start(payload, store, U)


class MenuTest(unittest.TestCase):
    def test_menu_layout(self):
        for payload in ("", "catalog", "cat_unknown", "brand_unknown"):
            (msg,), req = start(payload)
            self.assertIsNone(req)
            rows = msg["reply_markup"]["inline_keyboard"]
            self.assertEqual(len(rows), 17)
            self.assertEqual([len(r) for r in rows], [1] * 6 + [2] * 10 + [1])
            self.assertEqual(rows[0][0]["url"], f"https://t.me/{U}?start=cat_do160")

    def test_every_menu_button_resolves(self):
        for row in cfg["menu"]:
            for b in row:
                replies, req = start(b["start"])
                self.assertTrue(replies, b)
                self.assertIsNone(req, b)


class ListTest(unittest.TestCase):
    def test_category(self):
        (msg,), _ = start("cat_do160")
        self.assertTrue(msg["text"].startswith("<b>🚗 Автомобили до 160 л.с.</b>"))
        self.assertIn("Volkswagen Golf 280 TSI</b>\n2 795 000 ₽\n", msg["text"])
        self.assertIn('<a href="https://t.me/chinacarsmarkt/159">Открыть карточку</a>', msg["text"])
        self.assertEqual(msg["reply_markup"]["inline_keyboard"][0][0]["text"], "📚 Вернуться в каталог")

    def test_price_on_request(self):
        (msg,), _ = start("brand_Wuling")
        self.assertIn("Цена по запросу", msg["text"])

    def test_empty_brand_has_request_link(self):
        (msg,), req = start("brand_Zeekr")
        self.assertIsNone(req)
        self.assertIn("Сейчас автомобилей Zeekr нет в каталоге.", msg["text"])
        self.assertIn(f"https://t.me/{U}?start=Zeekr", msg["text"])

    def test_long_list_is_split(self):
        cars = {f"c{i}": {"title": "X" * 80, "price": 1000000, "url": "https://t.me/x/1"} for i in range(60)}
        msgs = bot.render_list("H", list(cars), cars, cfg, U)
        self.assertGreater(len(msgs), 1)
        self.assertTrue(all(len(m["text"]) <= 4096 for m in msgs))
        self.assertTrue(all(m["reply_markup"] is None for m in msgs[:-1]))
        self.assertIsNotNone(msgs[-1]["reply_markup"])


class RequestTest(unittest.TestCase):
    def test_request(self):
        (msg,), req = start("Zeekr")
        self.assertEqual(req, "Zeekr")
        self.assertIn("Спасибо за обращение в", msg["text"])
        self.assertIn("Менеджер уточнит", msg["text"])

    def test_howto(self):
        (msg,), _ = start("howto")
        self.assertIn("КАК ЗАКАЗАТЬ АВТОМОБИЛЬ", msg["text"])
        self.assertNotRegex(msg["text"], r"\n{3,}")


MANAGER = {"name": "Сергей", "name_instr": "Сергеем", "name_dat": "Сергею", "url": "https://t.me/example_manager"}


class FakeStore:
    def __init__(self, config):
        self.config = config
        self.catalog = store.catalog


class FakeApi:
    def __init__(self):
        self.sent = []

    def send(self, chat_id, msg):
        self.sent.append((chat_id, msg))


class FollowupTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.followups = bot.Followups(self.tmp.name)
        self.cfg = dict(cfg, managers=[MANAGER])

    def update(self, user, text="/start Zeekr"):
        return {"update_id": 1, "message": {"chat": {"id": user["id"], "type": "private"}, "from": user, "text": text}}

    def test_render(self):
        msg = bot.render_followup(self.cfg, "Mark", "Zeekr")
        self.assertTrue(msg["text"].startswith("Здравствуйте, Mark! Вы оставили запрос на автомобиль Zeekr в "))
        self.assertIn("с нашим менеджером Сергеем по кнопке ниже", msg["text"])
        button = msg["reply_markup"]["inline_keyboard"][0][0]
        self.assertEqual(button, {"text": "💬 Написать Сергею", "url": MANAGER["url"]})

    def test_render_escapes_html(self):
        msg = bot.render_followup(self.cfg, "<b>x</b>", "A&B")
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", msg["text"])
        self.assertIn("A&amp;B", msg["text"])

    def test_queue_only_for_users_without_username(self):
        S = FakeStore(self.cfg)
        api = FakeApi()
        bot.process_update(api, S, U, [], self.followups, self.update({"id": 5, "first_name": "Mark"}))
        bot.process_update(api, S, U, [], self.followups, self.update({"id": 6, "first_name": "Ann", "username": "ann"}))
        self.assertEqual([i["chat_id"] for i in self.followups._read()], [5])

    def test_no_queue_without_managers(self):
        S = FakeStore(dict(cfg, managers=[]))
        bot.process_update(FakeApi(), S, U, [], self.followups, self.update({"id": 5, "first_name": "Mark"}))
        self.assertEqual(self.followups._read(), [])

    def test_no_duplicates_and_due_time(self):
        self.assertTrue(self.followups.add(5, "Mark", "Zeekr", 100))
        self.assertFalse(self.followups.add(5, "Mark", "Zeekr", 100))
        self.assertEqual(self.followups.pop_due(99), [])
        self.assertEqual(len(self.followups.pop_due(100)), 1)
        self.assertEqual(self.followups.pop_due(1000), [])

    def test_send_due(self):
        S = FakeStore(self.cfg)
        self.followups.add(5, "Mark", "Zeekr", 100)
        api = FakeApi()
        bot.send_due_followups(api, S, self.followups, now=50)
        self.assertEqual(api.sent, [])
        bot.send_due_followups(api, S, self.followups, now=150)
        self.assertEqual(api.sent[0][0], 5)
        self.assertIn("Zeekr", api.sent[0][1]["text"])


if __name__ == "__main__":
    unittest.main()
