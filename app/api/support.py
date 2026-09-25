"""Support content API (FAQ / help) — fully translatable."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.i18n import normalize_lang
from app.models import AdminSetting

router = APIRouter(prefix="/api/support", tags=["support"])

FAQ: list[dict] = [
    {
        "id": "how-to-buy",
        "q": {
            "uz": "Qanday buyurtma berish mumkin?",
            "en": "How do I place an order?",
            "ru": "Как сделать заказ?",
        },
        "a": {
            "uz": "O'yinni tanlang, mahsulot va paketni tanlang, o'yinchi ID ma'lumotlarini kiriting, savatga qo'shing va to'lovni tasdiqlang. To'lov tasdiqlangach, buyurtma avtomatik bajariladi.",
            "en": "Choose a game, pick a product and package, enter your player ID information, add to cart and confirm payment. Once payment is verified your order is fulfilled automatically.",
            "ru": "Выберите игру, продукт и пакет, введите ID игрока, добавьте в корзину и подтвердите оплату. После подтверждения оплаты заказ выполняется автоматически.",
        },
    },
    {
        "id": "player-id",
        "q": {
            "uz": "O'yinchi ID nima va qayerdan topiladi?",
            "en": "What is a Player ID and where do I find it?",
            "ru": "Что такое ID игрока и где его найти?",
        },
        "a": {
            "uz": "Player ID — o'yin ichidagi unikal raqamingiz. Odatda o'yin sozlamalaridagi profil bo'limida ko'rsatiladi. Ba'zi o'yinlar uchun Zone ID ham so'raladi — faqat o'yin talab qilganda.",
            "en": "A Player ID is your unique in-game number, usually shown in your profile inside the game settings. Some games also require a Zone ID — we only ask for it when the game requires it.",
            "ru": "Player ID — ваш уникальный номер в игре, обычно указан в профиле в настройках игры. Для некоторых игр нужен Zone ID — мы запрашиваем его только когда это необходимо.",
        },
    },
    {
        "id": "payment-help",
        "q": {
            "uz": "To'lov qabul qilinmadi. Nima qilish kerak?",
            "en": "My payment was not accepted. What should I do?",
            "ru": "Оплата не прошла. Что делать?",
        },
        "a": {
            "uz": "Buyurtma sahifasida to'lov holatini tekshiring. Agar muammo davom etsa, buyurtma raqami bilan qo'llab-quvvatlash xizmatiga yozing. Hech qachon bir xil to'lovni ikki marta amalga oshirmang.",
            "en": "Check the payment status on your order page. If the problem persists, contact support with your order number. Never pay twice for the same order.",
            "ru": "Проверьте статус оплаты на странице заказа. Если проблема сохраняется, напишите в поддержку, указав номер заказа. Не оплачивайте заказ дважды.",
        },
    },
    {
        "id": "order-status",
        "q": {
            "uz": "Buyurtma holati qanday yangilanadi?",
            "en": "How is the order status updated?",
            "ru": "Как обновляется статус заказа?",
        },
        "a": {
            "uz": "To'lov tasdiqlanishi bilan buyurtma yetkazib beruvchiga yuboriladi. Sizning buyurtma sahifangiz holatni avtomatik yangilaydi: Kutilmoqda → Bajarilmoqda → Bajarildi.",
            "en": "Once payment is verified the order is sent to the supplier. Your order page refreshes the status automatically: Pending → Processing → Completed.",
            "ru": "После подтверждения оплаты заказ отправляется поставщику. Страница заказа обновляется автоматически: Ожидание → В обработке → Выполнен.",
        },
    },
    {
        "id": "refund",
        "q": {
            "uz": "Buyurtma bajarilmasa nima bo'ladi?",
            "en": "What happens if my order fails?",
            "ru": "Что будет, если заказ не выполнится?",
        },
        "a": {
            "uz": "Agar yetkazib beruvchi buyurtmani bajara olmasa, buyurtma 'Bajarilmadi' holatiga o'tadi va sizga xabar yuboriladi. Qaytarish siyosati to'lov provayderi va biznes qoidalariga bog'liq — qo'llab-quvvatlash bilan bog'laning.",
            "en": "If the supplier cannot fulfill the order it is marked as Failed and you are notified. Refunds depend on the payment provider and business rules — contact support.",
            "ru": "Если поставщик не сможет выполнить заказ, он получит статус «Ошибка», и вы будете уведомлены. Возврат средств зависит от платёжного провайдера — обратитесь в поддержку.",
        },
    },
    {
        "id": "security",
        "q": {
            "uz": "Ma'lumotlarim xavfsizmi?",
            "en": "Is my data safe?",
            "ru": "Мои данные в безопасности?",
        },
        "a": {
            "uz": "Parollar kriptografik xesh bilan saqlanadi, to'lov faqat server tomonda tekshirilgan webhook orqali tasdiqlanadi va o'yinchi ma'lumotlari xavfsiz maskalanadi.",
            "en": "Passwords are cryptographically hashed, payments are only confirmed by verified server-side webhooks and player information is securely masked.",
            "ru": "Пароли хранятся в виде криптографических хэшей, оплата подтверждается только проверенными серверными вебхуками, данные игрока маскируются.",
        },
    },
]


@router.get("/faq")
def faq(lang: str = Query(default="uz")):
    lang = normalize_lang(lang)
    return {
        "items": [
            {"id": item["id"], "question": item["q"][lang], "answer": item["a"][lang]}
            for item in FAQ
        ]
    }


@router.get("/info")
def support_info(db: Session = Depends(get_db), lang: str = Query(default="uz")):
    lang = normalize_lang(lang)
    row = db.execute(
        select(AdminSetting).where(AdminSetting.key == "general")
    ).scalar_one_or_none()
    values = row.value if row and isinstance(row.value, dict) else {}
    return {
        "support_link": values.get("support_link", ""),
        "announcement": values.get(f"announcement_{lang}", "") or "",
    }
