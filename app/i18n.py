"""Backend i18n: uz / en / ru. Frontend has its own matching dictionaries."""
from __future__ import annotations

LANGUAGES = ("uz", "en", "ru")

STRINGS: dict[str, dict[str, str]] = {
    "order_created": {
        "uz": "Buyurtma yaratildi",
        "en": "Order created",
        "ru": "Заказ создан",
    },
    "payment_received": {
        "uz": "To'lov qabul qilindi",
        "en": "Payment received",
        "ru": "Оплата получена",
    },
    "fulfillment_processing": {
        "uz": "Buyurtma bajarilmoqda",
        "en": "Fulfillment processing",
        "ru": "Выполнение заказа",
    },
    "order_completed": {
        "uz": "Buyurtma bajarildi",
        "en": "Order completed",
        "ru": "Заказ выполнен",
    },
    "order_failed": {
        "uz": "Buyurtma bajarilmadi",
        "en": "Order failed",
        "ru": "Ошибка заказа",
    },
    "order_refunded": {
        "uz": "To'lov qaytarildi",
        "en": "Order refunded",
        "ru": "Возврат средств",
    },
    "password_reset": {
        "uz": "Parolni tiklash havolasi (2 soat amal qiladi):",
        "en": "Password reset link (valid for 2 hours):",
        "ru": "Ссылка для сброса пароля (действует 2 часа):",
    },
    "admin_message": {"uz": "{title}", "en": "{title}", "ru": "{title}"},
    "custom": {"uz": "{text}", "en": "{text}", "ru": "{text}"},
    "topup_paid": {
        "uz": "Balans to'ldirildi",
        "en": "Balance topped up",
        "ru": "Баланс пополнен",
    },
    "amount": {"uz": "Summa", "en": "Amount", "ru": "Сумма"},
    "order_number": {"uz": "Buyurtma", "en": "Order", "ru": "Заказ"},
    "status": {"uz": "Holat", "en": "Status", "ru": "Статус"},
    "total": {"uz": "Jami", "en": "Total", "ru": "Итого"},
    "supplier_unavailable": {
        "uz": "Yetkazib beruvchi vaqtincha ishlamayapti.",
        "en": "Supplier temporarily unavailable.",
        "ru": "Поставщик временно недоступен.",
    },
    "payment_not_configured": {
        "uz": "To'lov tizimi sozlanmagan.",
        "en": "Payment is not configured.",
        "ru": "Оплата не настроена.",
    },
    "catalog_sync_required": {
        "uz": "Katalogni sinxronizatsiya qilish kerak.",
        "en": "Catalog synchronization required.",
        "ru": "Требуется синхронизация каталога.",
    },
    "not_available": {"uz": "MAVJUD EMAS", "en": "NOT AVAILABLE", "ru": "НЕДОСТУПНО"},
    "not_configured": {"uz": "SOZLANMAGAN", "en": "NOT CONFIGURED", "ru": "НЕ НАСТРОЕНО"},
}


def t(key: str, lang: str = "uz", **params: object) -> str:
    entry = STRINGS.get(key, {})
    text = entry.get(lang) or entry.get("en") or key
    if params:
        try:
            return text.format(**params)
        except Exception:
            return text
    return text


def normalize_lang(lang: str | None) -> str:
    if lang and lang.lower()[:2] in LANGUAGES:
        return lang.lower()[:2]
    return "uz"
