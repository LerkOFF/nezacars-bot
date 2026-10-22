#!/usr/bin/env python3
"""Публикует меню-пост каталога в канал или группу.

  python3 post_menu.py @Nezavisimost_Ru --dry-run   показать, что уйдёт, ничего не отправляя
  python3 post_menu.py @Nezavisimost_Ru --pin       отправить и закрепить

Бот должен быть админом канала с правом публиковать сообщения (и закреплять, если нужен --pin).
Кнопки поста ведут в deep-link бота, как у оригинала. Ссылку на пост впишите в
data/config.json в `back_url`, тогда «Вернуться в каталог» будет вести на этот пост.
"""
import argparse
import json
import os
import sys

import bot


def post_url(chat, message_id):
    if chat.startswith("@"):
        return f"https://t.me/{chat[1:]}/{message_id}"
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("chat", help="@username канала или числовой id чата")
    ap.add_argument("--pin", action="store_true", help="закрепить пост без уведомления")
    ap.add_argument("--dry-run", action="store_true", help="только показать пост")
    args = ap.parse_args()

    bot.load_env()
    token = os.environ.get("BOT_TOKEN")
    if not token:
        sys.exit("BOT_TOKEN не задан (переменная окружения или .env)")
    api = bot.Api(token)
    username = api.call("getMe")["username"]
    (msg,) = bot.render_menu(bot.Store(bot.DATA).config, username)

    if args.dry_run:
        print(msg["text"])
        for row in msg["reply_markup"]["inline_keyboard"]:
            print("  " + " | ".join(f'{b["text"]} -> {b["url"]}' for b in row))
        return

    chat = int(args.chat) if args.chat.lstrip("-").isdigit() else args.chat
    sent = api.send(chat, msg)
    if args.pin:
        api.call("pinChatMessage", {"chat_id": chat, "message_id": sent["message_id"], "disable_notification": True})
    url = post_url(args.chat, sent["message_id"])
    print(json.dumps({"message_id": sent["message_id"], "url": url}, ensure_ascii=False))


if __name__ == "__main__":
    main()
