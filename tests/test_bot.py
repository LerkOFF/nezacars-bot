import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bot

U = "nezacars_bot"
NBSP = " "

# рабочие данные: тексты и меню берём настоящие, каталог для проверок свой
real_store = bot.Store(bot.DATA)
cfg = real_store.config

FIXTURE = {
    "cars": {
        "a": {"title": "Alpha One", "price": 1250000, "url": "https://t.me/ch/1"},
        "b": {"title": "Beta <Two>", "price": None, "url": "https://t.me/ch/2"},
        "c": {"title": "Gamma", "price": 5000000, "url": None},
    },
    "categories": {
        "cat_pickup": {"header": "🚖 Пикапы", "cars": ["a", "b"]},
        "cat_sedan": {"header": "🚖 Седаны", "cars": []},
    },
    "brands": {
        "Alpha": {"header": "🚖 Alpha", "cars": ["a", "c"]},
        "Peugeot": {"header": "🚖 Peugeot", "cars": []},
    },
}


class TmpStore(bot.Store):
    """Настоящий config.json и каталог из фикстуры."""

    def __init__(self, catalog, config=None):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "config.json").write_text(json.dumps(config or cfg, ensure_ascii=False), encoding="utf-8")
        (d / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
        super().__init__(d)


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.store = TmpStore(FIXTURE)
        self.addCleanup(self.store.tmp.cleanup)

    def start(self, payload):
        return bot.handle_start(payload, self.store, U)


class MenuTest(FixtureCase):
    def test_menu_layout(self):
        for payload in ("", "catalog", "cat_unknown", "brand_unknown"):
            (msg,), req = self.start(payload)
            self.assertIsNone(req)
            rows = msg["reply_markup"]["inline_keyboard"]
            self.assertEqual([len(r) for r in rows], [len(r) for r in cfg["menu"]])
            self.assertEqual(rows[0][0]["url"], f"https://t.me/{U}?start={cfg['menu'][0][0]['start']}")
        self.assertIn("Каталог автомобилей", msg["text"])


class ListTest(FixtureCase):
    def test_category_list(self):
        (msg,), req = self.start("cat_pickup")
        self.assertIsNone(req)
        self.assertTrue(msg["text"].startswith("<b>🚖 Пикапы</b>"))
        self.assertIn(f"🚗 <b>Alpha One</b>\nот 1{NBSP}250{NBSP}000 ₽\n", msg["text"])
        self.assertIn('<a href="https://t.me/ch/1">Открыть карточку</a>', msg["text"])
        self.assertTrue(msg["text"].endswith(
            '<a href="https://clck.ru/3NG86y">Заказать</a>\n'
            '<a href="https://t.me/Nezavisimost_sklad">Вернуться в каталог</a>'
        ))
        self.assertIsNone(msg["reply_markup"])

    def test_price_on_request_and_escaping(self):
        (msg,), _ = self.start("cat_pickup")
        self.assertIn("🚗 <b>Beta &lt;Two&gt;</b>\nцена по запросу\n", msg["text"])

    def test_card_without_link_is_plain_text(self):
        (msg,), _ = self.start("brand_Alpha")
        self.assertIn("Gamma</b>\nот 5", msg["text"])
        self.assertIn(f"5{NBSP}000{NBSP}000 ₽\nОткрыть карточку\n\n", msg["text"])

    def test_empty_brand_offers_request(self):
        (msg,), req = self.start("brand_Peugeot")
        self.assertIsNone(req)
        self.assertIn("Сейчас автомобилей Peugeot нет в каталоге.", msg["text"])
        self.assertIn(f"https://t.me/{U}?start=Peugeot", msg["text"])

    def test_empty_category(self):
        (msg,), _ = self.start("cat_sedan")
        self.assertIn("Седаны", msg["text"])
        self.assertIn("нет автомобилей", msg["text"])

    def test_footer_is_on_empty_replies_too(self):
        for payload in ("brand_Peugeot", "cat_sedan"):
            (msg,), _ = self.start(payload)
            self.assertTrue(msg["text"].endswith("Вернуться в каталог</a>"), payload)

    def test_back_button_is_optional(self):
        store = TmpStore(FIXTURE, dict(cfg, back_button="📚 Назад", back_url="https://t.me/x", footer_links=[]))
        self.addCleanup(store.tmp.cleanup)
        (msg,), _ = bot.handle_start("cat_pickup", store, U)
        self.assertEqual(msg["reply_markup"]["inline_keyboard"], [[{"text": "📚 Назад", "url": "https://t.me/x"}]])
        self.assertTrue(msg["text"].endswith("Открыть карточку</a>"))

    def test_long_list_is_split(self):
        cars = {f"c{i}": {"title": "X" * 80, "price": 1000000, "url": "https://t.me/x/1"} for i in range(60)}
        msgs = bot.render_list("H", list(cars), cars, cfg, U)
        self.assertGreater(len(msgs), 1)
        self.assertTrue(all(len(m["text"]) <= 4096 for m in msgs))
        self.assertIn("Заказать", msgs[-1]["text"])
        self.assertNotIn("Заказать", msgs[0]["text"])


class RequestTest(FixtureCase):
    def test_request(self):
        (msg,), req = self.start("Peugeot")
        self.assertEqual(req, "Peugeot")
        self.assertIn("Спасибо за обращение в автосалон", msg["text"])
        self.assertIn("Менеджер уточнит", msg["text"])

    def test_howto(self):
        (msg,), _ = self.start("howto")
        self.assertIn("КАК ЗАКАЗАТЬ АВТОМОБИЛЬ", msg["text"])
        self.assertNotRegex(msg["text"], r"\n{3,}")


class RealDataTest(unittest.TestCase):
    """Целостность рабочего каталога и меню."""

    catalog = real_store.catalog

    def test_every_menu_button_resolves(self):
        for row in cfg["menu"]:
            for b in row:
                replies, req = bot.handle_start(b["start"], real_store, U)
                self.assertTrue(replies, b)
                self.assertIsNone(req, b)

    def test_menu_payloads_exist_in_catalog(self):
        payloads = {b["start"] for row in cfg["menu"] for b in row}
        for key in self.catalog["categories"]:
            self.assertIn(key, payloads)
        for key in self.catalog["brands"]:
            self.assertIn("brand_" + key, payloads)

    def test_references_and_fields(self):
        cars = self.catalog["cars"]
        for group in ("categories", "brands"):
            for key, entry in self.catalog[group].items():
                self.assertTrue(entry["header"], key)
                for cid in entry["cars"]:
                    self.assertIn(cid, cars, (key, cid))
        for cid, car in cars.items():
            self.assertTrue(car["title"].strip(), cid)
            self.assertTrue(car["url"].startswith("https://t.me/"), cid)
            self.assertTrue(car["price"] is None or car["price"] > 0, cid)

    def test_no_message_exceeds_limit(self):
        for key, entry in {**self.catalog["categories"], **self.catalog["brands"]}.items():
            for msg in bot.render_list(entry["header"], entry["cars"], self.catalog["cars"], cfg, U):
                self.assertLessEqual(len(msg["text"]), 4096, key)


MANAGER = {"name": "Сергей", "name_instr": "Сергеем", "name_dat": "Сергею", "url": "https://t.me/example_manager"}


class FakeStore:
    def __init__(self, config):
        self.config = config
        self.catalog = FIXTURE


class FakeApi:
    def __init__(self):
        self.sent = []

    def send(self, chat_id, msg):
        self.sent.append((chat_id, msg))


class FollowupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.followups = bot.Followups(self.tmp.name)
        self.cfg = dict(cfg, managers=[MANAGER])

    def update(self, user, text="/start Peugeot"):
        return {"update_id": 1, "message": {"chat": {"id": user["id"], "type": "private"}, "from": user, "text": text}}

    def test_render(self):
        msg = bot.render_followup(self.cfg, "Mark", "Peugeot")
        self.assertTrue(msg["text"].startswith("Здравствуйте, Mark! Вы оставили запрос на автомобиль Peugeot в автосалоне «"))
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
        self.assertTrue(self.followups.add(5, "Mark", "Peugeot", 100))
        self.assertFalse(self.followups.add(5, "Mark", "Peugeot", 100))
        self.assertEqual(self.followups.pop_due(99), [])
        self.assertEqual(len(self.followups.pop_due(100)), 1)
        self.assertEqual(self.followups.pop_due(1000), [])

    def test_send_due(self):
        S = FakeStore(self.cfg)
        self.followups.add(5, "Mark", "Peugeot", 100)
        api = FakeApi()
        bot.send_due_followups(api, S, self.followups, now=50)
        self.assertEqual(api.sent, [])
        bot.send_due_followups(api, S, self.followups, now=150)
        self.assertEqual(api.sent[0][0], 5)
        self.assertIn("Peugeot", api.sent[0][1]["text"])


if __name__ == "__main__":
    unittest.main()
