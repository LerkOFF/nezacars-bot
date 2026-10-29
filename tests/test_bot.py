import html
import json
import re
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
            (msg,) = self.start(payload)
            rows = msg["reply_markup"]["inline_keyboard"]
            self.assertEqual([len(r) for r in rows], [len(r) for r in cfg["menu"]])
            self.assertEqual(rows[0][0]["url"], f"https://t.me/{U}?start={cfg['menu'][0][0]['start']}")
        self.assertIn("Каталог автомобилей", msg["text"])


class ListTest(FixtureCase):
    def test_category_list(self):
        (msg,) = self.start("cat_pickup")
        self.assertTrue(msg["text"].startswith("<b>🚖 Пикапы</b>"))
        self.assertIn(f"🚗 <b>Alpha One</b>\nот 1{NBSP}250{NBSP}000 ₽\n", msg["text"])
        self.assertIn('<a href="https://t.me/ch/1">Открыть карточку</a>', msg["text"])
        self.assertTrue(msg["text"].endswith(
            '<a href="https://clck.ru/3NG86y">Заказать</a>\n'
            '<a href="https://t.me/Nezavisimost_sklad">Вернуться в каталог</a>'
        ))
        self.assertIsNone(msg["reply_markup"])

    def test_price_on_request_and_escaping(self):
        (msg,) = self.start("cat_pickup")
        self.assertIn("🚗 <b>Beta &lt;Two&gt;</b>\nцена по запросу\n", msg["text"])

    def test_card_without_link_is_plain_text(self):
        (msg,) = self.start("brand_Alpha")
        self.assertIn("Gamma</b>\nот 5", msg["text"])
        self.assertIn(f"5{NBSP}000{NBSP}000 ₽\nОткрыть карточку\n\n", msg["text"])

    def test_empty_brand(self):
        (msg,) = self.start("brand_Peugeot")
        self.assertIn("Сейчас автомобилей Peugeot нет в каталоге.", msg["text"])
        self.assertNotIn(f"?start=", msg["text"])

    def test_empty_category(self):
        (msg,) = self.start("cat_sedan")
        self.assertIn("Седаны", msg["text"])
        self.assertIn("нет автомобилей", msg["text"])

    def test_footer_is_on_empty_replies_too(self):
        for payload in ("brand_Peugeot", "cat_sedan"):
            (msg,) = self.start(payload)
            self.assertTrue(msg["text"].endswith("Вернуться в каталог</a>"), payload)

    def test_back_button_is_optional(self):
        store = TmpStore(FIXTURE, dict(cfg, back_button="📚 Назад", back_url="https://t.me/x", footer_links=[]))
        self.addCleanup(store.tmp.cleanup)
        (msg,) = bot.handle_start("cat_pickup", store, U)
        self.assertEqual(msg["reply_markup"]["inline_keyboard"], [[{"text": "📚 Назад", "url": "https://t.me/x"}]])
        self.assertTrue(msg["text"].endswith("Открыть карточку</a>"))

    def test_long_list_is_split(self):
        cars = {f"c{i}": {"title": "X" * 80, "price": 1000000, "url": "https://t.me/x/1"} for i in range(60)}
        msgs = bot.render_list("H", list(cars), cars, cfg, U)
        self.assertGreater(len(msgs), 1)
        self.assertTrue(all(len(m["text"]) <= 4096 for m in msgs))
        self.assertIn("Заказать", msgs[-1]["text"])
        self.assertNotIn("Заказать", msgs[0]["text"])


class HowtoTest(FixtureCase):
    def test_unknown_payload_opens_menu(self):
        for payload in ("Peugeot", "Zeekr", "request", "cat_nope", "brand_Nope"):
            self.assertEqual(self.start(payload), self.start(""), payload)

    def test_howto(self):
        (msg,) = self.start("howto")
        self.assertIn("КАК ЗАКАЗАТЬ АВТОМОБИЛЬ", msg["text"])
        self.assertNotRegex(msg["text"], r"\n{3,}")


class RealDataTest(unittest.TestCase):
    """Целостность рабочего каталога и меню."""

    catalog = real_store.catalog

    def test_every_menu_button_resolves(self):
        for row in cfg["menu"]:
            for b in row:
                replies = bot.handle_start(b["start"], real_store, U)
                self.assertTrue(replies, b)

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


HREF = re.compile(r'<a href="([^"]+)"')
PAYLOAD = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

def all_payloads():
    return [b["start"] for row in cfg["menu"] for b in row]


def hrefs(replies):
    return [h for r in replies for h in HREF.findall(r["text"])]


class RealMenuTest(unittest.TestCase):
    """Меню целиком: ничего лишнего, каждая кнопка ведёт куда должна."""

    def menu(self, payload=""):
        (msg,) = bot.handle_start(payload, real_store, U)
        return msg

    def test_brands_button_is_same_as_start(self):
        brands = next(b for row in cfg["menu"] for b in row if b["text"] == "БРЕНДЫ")
        self.assertEqual(brands["start"], "catalog")
        self.assertEqual(bot.handle_start(brands["start"], real_store, U), bot.handle_start("", real_store, U))
        self.assertEqual(self.menu("catalog"), self.menu(""))

    def test_exact_buttons_no_extras(self):
        rows = self.menu()["reply_markup"]["inline_keyboard"]
        self.assertEqual([len(r) for r in rows], [1] * 7 + [2] * 9 + [1])
        self.assertEqual(sum(len(r) for r in rows), 7 + 18 + 1)
        self.assertEqual(list(self.menu()["reply_markup"]), ["inline_keyboard"])
        self.assertEqual(
            [b["text"] for r in rows for b in r],
            [b["text"] for r in cfg["menu"] for b in r],
        )
        for b in (b for r in rows for b in r):
            self.assertEqual(set(b), {"text", "url"})

    def test_button_urls_are_deep_links(self):
        rows = self.menu()["reply_markup"]["inline_keyboard"]
        urls = [b["url"] for r in rows for b in r]
        self.assertEqual(len(urls), len(set(urls)))
        for url in urls:
            base, _, payload = url.partition("?start=")
            self.assertEqual(base, f"https://t.me/{U}")
            self.assertRegex(payload, PAYLOAD)

    def test_menu_has_exactly_catalog_sections(self):
        payloads = all_payloads()
        self.assertEqual(len(payloads), len(set(payloads)))
        expected = set(real_store.catalog["categories"]) | {"brand_" + k for k in real_store.catalog["brands"]}
        self.assertEqual(set(payloads) - {"catalog", "howto"}, expected)

    def test_menu_texts_unique_and_not_empty(self):
        texts = [b["text"] for row in cfg["menu"] for b in row]
        self.assertEqual(len(texts), len(set(texts)))
        self.assertTrue(all(t.strip() for t in texts))
        self.assertTrue(all(len(t) <= 64 for t in texts))

    def test_every_button_reply_has_no_extra_buttons(self):
        for payload in all_payloads():
            if payload == "catalog":
                continue
            replies = bot.handle_start(payload, real_store, U)
            for r in replies:
                self.assertIsNone(r["reply_markup"], payload)

    def test_every_link_in_replies_is_known(self):
        catalog = real_store.catalog
        allowed = {c["url"] for c in catalog["cars"].values()}
        allowed |= {f["url"] for f in cfg["footer_links"]} | {cfg["brand_url"]}
        for payload in all_payloads() + ["Peugeot"]:
            replies = bot.handle_start(payload, real_store, U)
            for h in hrefs(replies):
                self.assertTrue(h in allowed or h.startswith(f"https://t.me/{U}?start="), (payload, h))

    def test_every_list_has_cards_and_footer(self):
        catalog = real_store.catalog
        for key, entry in {**catalog["categories"], **catalog["brands"]}.items():
            payload = key if key.startswith("cat_") else "brand_" + key
            replies = bot.handle_start(payload, real_store, U)
            links = hrefs(replies)
            self.assertEqual(len(entry["cars"]) + len(cfg["footer_links"]), len(links), key)
            self.assertEqual(links[-2:], [f["url"] for f in cfg["footer_links"]], key)
            self.assertEqual(links[:-2], [catalog["cars"][c]["url"] for c in entry["cars"]], key)
            for cid in entry["cars"]:
                self.assertIn(f"<b>{html.escape(catalog['cars'][cid]['title'])}</b>", "".join(r["text"] for r in replies))

    def test_howto_has_no_buttons(self):
        (howto,) = bot.handle_start("howto", real_store, U)
        self.assertIsNone(howto["reply_markup"])
        self.assertNotIn("{", howto["text"])

    def test_no_request_flow_in_config(self):
        for key in cfg:
            self.assertFalse(key.startswith(("request_", "followup_")), key)


class RealCatalogTest(unittest.TestCase):
    catalog = real_store.catalog

    def test_every_car_is_listed(self):
        used = {c for g in ("categories", "brands") for e in self.catalog[g].values() for c in e["cars"]}
        self.assertEqual(used, set(self.catalog["cars"]))

    def test_every_car_is_in_its_brand(self):
        in_brand = {c for e in self.catalog["brands"].values() for c in e["cars"]}
        # у Oting в меню отдельной марки нет, машина лежит только в «Рамных внедорожниках»
        self.assertEqual(set(self.catalog["cars"]) - in_brand, {"oting-paladin"})

    def test_no_duplicates_inside_lists(self):
        for g in ("categories", "brands"):
            for key, e in self.catalog[g].items():
                self.assertEqual(len(e["cars"]), len(set(e["cars"])), key)

    def test_titles_unique_and_links_unique(self):
        cars = self.catalog["cars"].values()
        titles = [c["title"] for c in cars]
        self.assertEqual(len(titles), len(set(titles)))
        urls = [c["url"] for c in cars]
        self.assertEqual(len(urls), len(set(urls)))

    def test_link_shape(self):
        for cid, c in self.catalog["cars"].items():
            self.assertRegex(c["url"], r"^https://t\.me/(Nezavisimost_Ru|Nezavisimost_sklad)/\d+$", cid)
            self.assertIsInstance(c["price"], (int, type(None)), cid)
        for f in cfg["footer_links"]:
            self.assertRegex(f["url"], r"^https://\S+$")


class FakeApi:
    def __init__(self):
        self.sent = []

    def send(self, chat_id, msg):
        self.sent.append((chat_id, msg))


class SilenceTest(unittest.TestCase):
    """Бот отвечает только на /start, остального не трогает."""

    def run_update(self, message):
        api = FakeApi()
        bot.process_update(api, real_store, U, {"update_id": 1, "message": message})
        return api.sent

    def msg(self, text, chat_type="private"):
        return {"chat": {"id": 5, "type": chat_type}, "from": {"id": 5, "first_name": "A"}, "text": text}

    def test_start_variants_give_same_menu(self):
        menus = [self.run_update(self.msg(t)) for t in ("/start", "/start catalog", f"/start@{U}", "/start   ")]
        self.assertTrue(all(len(m) == 1 for m in menus))
        self.assertEqual(len({json.dumps(m, sort_keys=True) for m in menus}), 1)

    def test_other_text_is_ignored(self):
        for t in ("привет", "/help", "/startx", "", "БРЕНДЫ"):
            self.assertEqual(self.run_update(self.msg(t)), [], t)
        self.assertEqual(self.run_update(self.msg("/start", "group")), [])
        self.assertEqual(self.run_update({"chat": {"id": 5, "type": "private"}, "from": {"id": 5}}), [])


if __name__ == "__main__":
    unittest.main()
