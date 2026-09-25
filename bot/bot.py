"""VYRON Telegram bot (aiogram 3).

/start  -> welcome + "Open VYRON" Web App button (and "Admin panel" for admin IDs)
/lang   -> choose language (uz / en / ru)
/balance-> wallet balance
/orders -> open orders in the Web App
/admin  -> admin Web App (only TELEGRAM_ADMIN_IDS)

Runs inside the same process as the web server (started by main.py).
``BotManager.restart()`` is used when an admin changes the bot token in the panel.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.i18n import normalize_lang
from app.logging_config import get_logger
from app.models import AdminSetting, Role, RoleName, TelegramUser, User

log = get_logger("vyron.bot")

TEXT = {
    "welcome": {
        "uz": "<b>{name}</b> — raqamli o'yin bozori\n\nO'yin valyutasi (UC, Diamonds, Robux, Gems...), sovg'a kartalari va raqamli mahsulotlar.\nBalansni karta orqali to'ldiring va bir zumda xarid qiling.\n\nQuyidagi tugma orqali do'konni oching:",
        "en": "<b>{name}</b> — digital gaming marketplace\n\nGame currency (UC, Diamonds, Robux, Gems...), gift cards and digital products.\nTop up your balance by card and buy instantly.\n\nOpen the store with the button below:",
        "ru": "<b>{name}</b> — цифровой игровой маркетплейс\n\nИгровая валюта (UC, Diamonds, Robux, Gems...), подарочные карты и цифровые товары.\nПополните баланс картой и покупайте мгновенно.\n\nОткройте магазин кнопкой ниже:",
    },
    "open": {"uz": "{name} ni ochish", "en": "Open {name}", "ru": "Открыть {name}"},
    "admin": {"uz": "Admin panel", "en": "Admin panel", "ru": "Админ панель"},
    "wallet": {"uz": "Balansni to'ldirish", "en": "Top up balance", "ru": "Пополнить баланс"},
    "orders": {"uz": "Buyurtmalarim", "en": "My orders", "ru": "Мои заказы"},
    "lang_prompt": {"uz": "Tilni tanlang:", "en": "Choose language:", "ru": "Выберите язык:"},
    "lang_set": {"uz": "Til o'zgartirildi.", "en": "Language changed.", "ru": "Язык изменён."},
    "balance": {"uz": "Balansingiz: <b>{amount} so'm</b>", "en": "Your balance: <b>{amount} UZS</b>",
                "ru": "Ваш баланс: <b>{amount} сум</b>"},
    "denied": {"uz": "Ruxsat yo'q.", "en": "Access denied.", "ru": "Доступ запрещён."},
    "no_https": {
        "uz": "Web App uchun HTTPS manzil kerak. Admin panelda PUBLIC_BASE_URL ni https:// bilan sozlang.",
        "en": "The Web App needs an HTTPS address. Set PUBLIC_BASE_URL with https:// in the admin panel.",
        "ru": "Для Web App нужен HTTPS адрес. Укажите PUBLIC_BASE_URL с https:// в админ панели.",
    },
    "help": {
        "uz": "/start — do'konni ochish\n/balance — balans\n/orders — buyurtmalar\n/lang — til",
        "en": "/start — open the store\n/balance — balance\n/orders — orders\n/lang — language",
        "ru": "/start — открыть магазин\n/balance — баланс\n/orders — заказы\n/lang — язык",
    },
}


def tr(key: str, lang: str, **params) -> str:
    text = TEXT[key].get(lang) or TEXT[key]["en"]
    return text.format(**params) if params else text


def _webapp_url() -> str:
    settings = get_settings()
    return settings.webapp_url or settings.public_base_url or ""


def _site_name() -> str:
    try:
        with session_scope() as session:
            row = session.execute(
                select(AdminSetting).where(AdminSetting.key == "branding")
            ).scalar_one_or_none()
            if row and isinstance(row.value, dict) and row.value.get("site_name"):
                return str(row.value["site_name"])[:40]
    except Exception:
        pass
    return "VYRON"


def _is_admin(telegram_id: Optional[int]) -> bool:
    return bool(telegram_id) and telegram_id in get_settings().telegram_admin_ids


def sync_telegram_user(tg_id: int, username: Optional[str], first_name: Optional[str],
                       language_code: Optional[str]) -> tuple[str, int]:
    """Persist the Telegram user, link/create a VYRON user. Returns (lang, balance)."""
    lang = normalize_lang(language_code)
    with session_scope() as session:
        row = session.execute(
            select(TelegramUser).where(TelegramUser.telegram_id == tg_id)
        ).scalar_one_or_none()
        if row is None:
            row = TelegramUser(telegram_id=tg_id, language=lang)
            session.add(row)
        row.username = username
        row.first_name = first_name
        user = session.execute(select(User).where(User.telegram_id == tg_id)).scalar_one_or_none()
        admin = _is_admin(tg_id)
        if user is None:
            role = session.execute(
                select(Role).where(Role.name == (RoleName.ADMIN if admin else RoleName.CUSTOMER))
            ).scalar_one()
            base = "".join(ch for ch in (username or f"tg{tg_id}") if ch.isalnum() or ch in "_.-")[:28]
            base = base or f"tg{tg_id}"
            candidate, i = base, 1
            while session.execute(select(User).where(User.username == candidate)).scalar_one_or_none():
                candidate = f"{base}{i}"
                i += 1
            user = User(username=candidate, telegram_id=tg_id, role_id=role.id, language=lang)
            session.add(user)
            session.flush()
        elif admin and not user.is_admin:
            role = session.execute(select(Role).where(Role.name == RoleName.ADMIN)).scalar_one()
            user.role_id = role.id
        row.user_id = user.id
        row.language = user.language or lang
        return (user.language or lang), int(user.balance or 0)


async def run_bot() -> None:
    """Run the aiogram bot. Safe no-op when TELEGRAM_BOT_TOKEN is absent."""
    settings = get_settings()
    if not settings.telegram_configured():
        log.info("Telegram bot NOT CONFIGURED (TELEGRAM_BOT_TOKEN empty) — bot disabled")
        return

    from aiogram import Bot, Dispatcher, F
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from aiogram.filters import Command, CommandStart
    from aiogram.types import (
        BotCommand,
        CallbackQuery,
        InlineKeyboardButton,
        InlineKeyboardMarkup,
        MenuButtonWebApp,
        Message,
        WebAppInfo,
    )

    bot = Bot(token=settings.telegram_bot_token,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    def _button(text: str, path: str = "") -> Optional[InlineKeyboardButton]:
        base = _webapp_url()
        if not base:
            return None
        url = f"{base}/{path.lstrip('/')}" if path else base
        if base.startswith("https://"):
            return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))
        return InlineKeyboardButton(text=text, url=url)  # dev fallback (no Web App on http)

    def _keyboard(lang: str, admin: bool) -> InlineKeyboardMarkup:
        name = _site_name()
        rows: list[list[InlineKeyboardButton]] = []
        main = _button(tr("open", lang, name=name))
        if main:
            rows.append([main])
        pair = [b for b in (_button(tr("wallet", lang), "#/wallet"),
                            _button(tr("orders", lang), "#/account/orders")) if b]
        if pair:
            rows.append(pair)
        if admin:
            adm = _button(tr("admin", lang), "#/admin")
            if adm:
                rows.append([adm])
        rows.append([
            InlineKeyboardButton(text="O'zbekcha", callback_data="lang:uz"),
            InlineKeyboardButton(text="English", callback_data="lang:en"),
            InlineKeyboardButton(text="Русский", callback_data="lang:ru"),
        ])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _sync(message: Message) -> tuple[str, int]:
        tg = message.from_user
        return sync_telegram_user(tg.id, tg.username, tg.first_name, tg.language_code)

    @dp.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        lang, _ = await asyncio.to_thread(_sync, message)
        admin = _is_admin(message.from_user.id)
        text = tr("welcome", lang, name=_site_name())
        if not _webapp_url().startswith("https://") and admin:
            text += "\n\n" + tr("no_https", lang)
        await message.answer(text, reply_markup=_keyboard(lang, admin))

    @dp.message(Command("lang"))
    async def cmd_lang(message: Message) -> None:
        lang, _ = await asyncio.to_thread(_sync, message)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="O'zbekcha", callback_data="lang:uz"),
            InlineKeyboardButton(text="English", callback_data="lang:en"),
            InlineKeyboardButton(text="Русский", callback_data="lang:ru"),
        ]])
        await message.answer(tr("lang_prompt", lang), reply_markup=kb)

    @dp.callback_query(F.data.startswith("lang:"))
    async def cb_lang(callback: CallbackQuery) -> None:
        lang = normalize_lang(callback.data.split(":", 1)[1])

        def _save() -> None:
            with session_scope() as session:
                for model in (TelegramUser, User):
                    row = session.execute(
                        select(model).where(model.telegram_id == callback.from_user.id)
                    ).scalar_one_or_none()
                    if row is not None:
                        row.language = lang

        await asyncio.to_thread(_save)
        await callback.answer(tr("lang_set", lang))
        admin = _is_admin(callback.from_user.id)
        await callback.message.answer(tr("welcome", lang, name=_site_name()),
                                      reply_markup=_keyboard(lang, admin))

    @dp.message(Command("balance"))
    async def cmd_balance(message: Message) -> None:
        lang, balance = await asyncio.to_thread(_sync, message)
        amount = f"{balance // 100:,}".replace(",", " ")
        kb_btn = _button(tr("wallet", lang), "#/wallet")
        kb = InlineKeyboardMarkup(inline_keyboard=[[kb_btn]]) if kb_btn else None
        await message.answer(tr("balance", lang, amount=amount), reply_markup=kb)

    @dp.message(Command("orders"))
    async def cmd_orders(message: Message) -> None:
        lang, _ = await asyncio.to_thread(_sync, message)
        btn = _button(tr("orders", lang), "#/account/orders")
        await message.answer(tr("orders", lang),
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn]]) if btn else None)

    @dp.message(Command("admin"))
    async def cmd_admin(message: Message) -> None:
        lang, _ = await asyncio.to_thread(_sync, message)
        if not _is_admin(message.from_user.id):
            await message.answer(tr("denied", lang))
            return
        btn = _button(tr("admin", lang), "#/admin")
        await message.answer(tr("admin", lang),
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn]]) if btn else None)

    @dp.message(Command("help", "support"))
    async def cmd_help(message: Message) -> None:
        lang, _ = await asyncio.to_thread(_sync, message)
        await message.answer(tr("help", lang))

    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Open store / Do'kon"),
            BotCommand(command="balance", description="Balance / Balans"),
            BotCommand(command="orders", description="Orders / Buyurtmalar"),
            BotCommand(command="lang", description="Language / Til"),
        ])
        base = _webapp_url()
        if base.startswith("https://"):
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text=_site_name(), web_app=WebAppInfo(url=base))
            )
    except Exception as exc:
        log.warning("bot setup call failed: %s", type(exc).__name__)

    log.info("Telegram bot starting (polling)...")
    try:
        await dp.start_polling(bot, handle_signals=False)
    except asyncio.CancelledError:
        log.info("Telegram bot stopped")
        raise
    except Exception as exc:
        log.error("Telegram bot crashed: %s", type(exc).__name__)
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass


class BotManager:
    """Owns the bot task so the admin panel can restart it after a token change."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(run_bot(), name="telegram-bot")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    async def restart(self) -> None:
        await self.stop()
        self.start()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()


bot_manager = BotManager()
