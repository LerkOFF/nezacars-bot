---
name: nezacars-bot
description: >-
  Work on nezacars-bot: Telegram-бот каталога автомобилей @nezacars_bot (клон меню
  @chinacars_market_bot), репозиторий LerkOFF/nezacars-bot. Use when the user mentions
  nezacars, nzcars, nezacars_bot, бот каталога, chinacars клон, меню авто, каталог авто,
  or works in /Users/lerk/work/python/nezacars-bot. Covers the rule that
  everything goes through git: edit, test, commit, push, deploy only by git pull.
---

# nezacars-bot: всё через git

Бот: `bot.py` (stdlib Python 3, long polling), данные в `data/config.json` и `data/catalog.json`, тесты в `tests/`. Описание работы и формат данных: `README.md`.

Репозиторий: `git@github.com:LerkOFF/nezacars-bot.git`, **публичный**, ветка `main`, работаем прямо в `main`. Карточка проекта в vault: `Lerk/продукты/Chinacars клон`.

## Правило

Любое изменение кода, данных, текстов и этого скилла попадает в бота только через git: правка в папке проекта, тесты, коммит, `git push origin main`. Нельзя править файлы на сервере руками, класть их через scp/rsync/tar, держать на сервере код, которого нет в `origin/main`.

## Порядок работы

1. `git status -sb` и `git pull --ff-only`: начинать с чистого, свежего `main`.
2. Правка. Каталог меняется только в `data/catalog.json` (формат в README), тексты и меню в `data/config.json`. Логика в `bot.py`.
3. Тесты: `python3 -m unittest discover -s tests`. Если менялось поведение, тест добавить или поправить. Падающие тесты не коммитить.
4. Для данных и текстов бот перезапускать не нужно, он перечитывает json при изменении файла. После правки `bot.py` перезапуск нужен.
5. Коммит по скиллу `git-commits`: русский, строчная буква, зачем сделано, автор `lerk <lerk@joulerk.ru>`, без Co-authored-by и упоминаний Cursor/Claude, сообщение через HEREDOC, `env -u GIT_AUTHOR_NAME -u GIT_AUTHOR_EMAIL -u GIT_COMMITTER_NAME -u GIT_COMMITTER_EMAIL git commit ...`. Сразу после: `git log -1 --format='%an <%ae>%n%s%n%b'`.
6. Сразу `git push origin main`. Не копить локальные коммиты.
7. В коммит только свои файлы. Смотреть `git status` перед `git add`, не `git add -A` вслепую.

## Что нельзя коммитить (репозиторий публичный)

- `.env` и любой токен Bot API (в git только `.env.example` без значений). Токен лежит в vault, карточка `Pass/Chinacars клон бот`, в чат и в код не печатать.
- `ADMIN_CHAT_IDS`, личные id, скриншоты и тексты переписок, экспорты чатов Telegram, номера и почты людей.
- `__pycache__/`, логи, экспорт `result.json`, выгрузки аккаунтов.
- Контакты чужих менеджеров. Свои менеджеры задаются в `managers` в `data/config.json`, это публично, добавлять только то, что можно показывать всем.

Если секрет всё же попал в коммит: токен считать скомпрометированным, перевыпустить в BotFather, обновить `.env` и карточку в vault, историю не переписывать без просьбы пользователя.

## Запуск и выкладка

- Локально: `cp .env.example .env`, заполнить `BOT_TOKEN`, `python3 bot.py`.
- На один токен только один работающий бот: два процесса дают конфликт `getUpdates`. Перед запуском на сервере остановить локальный.
- Сервера пока нет. Когда появится (хост выбрать вместе с пользователем): клон `origin/main` в `/opt/nezacars-bot`, служба systemd, `.env` только на сервере и вне git. Обновление: `ssh -A <хост> 'cd /opt/nezacars-bot && git pull --ff-only'`, затем `systemctl restart nezacars-bot`, если менялся `bot.py`. Если `git status` на сервере не чистый, остановиться и разобраться, не затирать.

## После значимых изменений

Обновить карточку `Lerk/продукты/Chinacars клон` в vault (статус, хост, что осталось). Новые факты о людях, каналах и именах ботов записывать туда же, не в репозиторий.
