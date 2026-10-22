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


if __name__ == "__main__":
    unittest.main()
