import logging
import asyncio
import os
import json
import uuid
import aiohttp
import sqlite3
import threading
import re
from datetime import datetime, timedelta
from flask import Flask
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import create_engine, text
import time

# ==================================================
# FLASK
# ==================================================
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "🤖 Бот работает!"

@flask_app.route('/health')
def health():
    return "OK", 200

# ==================================================
# SUPABASE
# ==================================================
SUPABASE_URL = "postgresql://postgres.hbjcrkcvaiuktkdrpema:gPldQXhIjtSeXTN4@aws-0-eu-north-1.pooler.supabase.com:6543/postgres"

engine = create_engine(SUPABASE_URL, echo=False, pool_pre_ping=True)

def get_all_users():
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT user_id FROM users"))
            return [row[0] for row in result]
    except Exception as e:
        logging.error(f"Ошибка получения пользователей: {e}")
        return []

def get_user_count():
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM users"))
            return result.fetchone()[0] or 0
    except Exception as e:
        logging.error(f"Ошибка получения количества пользователей: {e}")
        return 0

def add_user(user_id: int, first_name: str, username: str = None):
    try:
        with engine.connect() as conn:
            conn.execute(
                text("INSERT INTO users (user_id, first_name, username) VALUES (:id, :name, :uname) ON CONFLICT (user_id) DO NOTHING"),
                {"id": user_id, "name": first_name, "uname": username}
            )
            conn.commit()
        return True
    except Exception as e:
        logging.error(f"Ошибка добавления пользователя: {e}")
        return False

def add_user_discount(user_id: int, discount_code: str, discount_percent: int):
    try:
        with engine.connect() as conn:
            conn.execute(
                text("INSERT INTO user_discounts (user_id, discount_code, discount_percent) VALUES (:id, :code, :percent) ON CONFLICT (user_id, discount_code) DO NOTHING"),
                {"id": user_id, "code": discount_code, "percent": discount_percent}
            )
            conn.commit()
        return True
    except Exception as e:
        logging.error(f"Ошибка сохранения скидки: {e}")
        return False

def get_user_discounts(user_id: int):
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT discount_code, discount_percent, used FROM user_discounts WHERE user_id = :id AND used = 0"), {"id": user_id})
            return result.fetchall()
    except Exception as e:
        logging.error(f"Ошибка получения скидок: {e}")
        return []

def mark_discount_used(user_id: int, discount_code: str):
    try:
        with engine.connect() as conn:
            conn.execute(text("UPDATE user_discounts SET used = 1 WHERE user_id = :id AND discount_code = :code"), {"id": user_id, "code": discount_code})
            conn.commit()
        return True
    except Exception as e:
        logging.error(f"Ошибка отметки скидки: {e}")
        return False

# ==================================================
# ПРОМОКОДЫ В ПАМЯТИ
# ==================================================
active_promocodes = {}

def create_promocode(code: str, discount: int, expires_minutes: int = None):
    code = code.upper()
    expires_at = None
    if expires_minutes:
        expires_at = time.time() + (expires_minutes * 60)
    active_promocodes[code] = {"discount": discount, "expires_at": expires_at}
    logging.info(f"✅ Создан промокод {code} (скидка {discount}%)")
    return True

def delete_promocode(code: str):
    code = code.upper()
    if code in active_promocodes:
        del active_promocodes[code]
        return True
    return False

def check_promocode(code: str):
    code = code.upper()
    if code not in active_promocodes:
        return None
    promo = active_promocodes[code]
    if promo["expires_at"] and time.time() > promo["expires_at"]:
        del active_promocodes[code]
        return None
    return promo["discount"]

def get_all_promocodes():
    expired = []
    for code, data in active_promocodes.items():
        if data["expires_at"] and time.time() > data["expires_at"]:
            expired.append(code)
    for code in expired:
        del active_promocodes[code]
    return list(active_promocodes.items())

def cleanup_expired_promocodes():
    while True:
        time.sleep(60)
        get_all_promocodes()

# ==================================================
# КОНФИГ
# ==================================================
ROLLYPAY_API_KEY = "z39_r_COJdiB7PWeddOYvzT2rx4cjIbS1m4JJcgBTi0"
ROLLYPAY_CALLBACK_URL = "https://t-bot-18jz.onrender.com/webhook"
BOT_TOKEN = "8405743009:AAFmmRNDGWGDnxQbIDPVtiAprSnh0aq9g0U"
PROJECT_NAME = "VIP"
SUPPORT_CONTACT_RU = "https://t.me/Nastia_sup"
ADMIN_IDS = [8370080332, 8559381302]

DOCS_RU = {"offer": "https://telegra.ph/POLZOVATELSKOE-SOGLASHENIE-07-01-29", "policy": "https://telegra.ph/Politika-konfidicialnosti-07-01"}
DOCS_EN = {"offer": "https://telegra.ph/POLZOVATELSKOE-SOGLASHENIE-07-01-29", "policy": "https://telegra.ph/Politika-konfidicialnosti-07-01"}

CHANNEL_IDS = {
    "week": "-1004267025056",
    "month": "-1004478645537",
    "year": "-1004325704012",
    "test": "-1003875225035",
}

DB_PATH = "users.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS paid_tariffs (user_id INTEGER, tariff_key TEXT, paid_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, tariff_key))')
    conn.commit()
    conn.close()

def add_paid_tariff(user_id: int, tariff_key: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO paid_tariffs (user_id, tariff_key) VALUES (?, ?)', (user_id, tariff_key))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logging.error(f"Ошибка добавления оплаты: {e}")
        return False

def get_paid_tariffs(user_id: int):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT tariff_key FROM paid_tariffs WHERE user_id = ?', (user_id,))
        result = [row[0] for row in cursor.fetchall()]
        conn.close()
        return result
    except Exception as e:
        logging.error(f"Ошибка получения оплаченных тарифов: {e}")
        return []

def is_tariff_paid(user_id: int, tariff_key: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM paid_tariffs WHERE user_id = ? AND tariff_key = ?', (user_id, tariff_key))
        result = cursor.fetchone() is not None
        conn.close()
        return result
    except Exception as e:
        logging.error(f"Ошибка проверки оплаты: {e}")
        return False

# ==================================================
# ТЕКСТЫ
# ==================================================
LANG = {
    "ru": {
        "start_welcome": "💬 Привет, {name}!\n\n📜 <a href=\"{offer}\">Пользовательское соглашение</a>\n🔒 <a href=\"{policy}\">Политика конфиденциальности</a>\n\n🚀 VIP-ДОСТУП КО ВСЕМ МАТЕРИАЛАМ\n\nЗдесь ты получаешь всё в одном месте:\n— Школьницы, вписки, закладчицы, альтушки\n— Мини Детск, жесть, износы и другие тарифы\n— Ежедневное обновление контента\n— Приватный чат для VIP пользователей\n— Скупаю контент в других ботах, и сливаю в VIP\n— Поддержка 24/7 — <a href=\"https://t.me/Nastia_sup\">@Nastia_sup</a>\n\nВместо 700 ₽ за один тариф — 449 ₽ в месяц за всё.",
        "prices_menu": "📋 <b>Прайс</b>\n\nВыберите тариф, чтобы узнать подробности и оформить покупку.",
        "subs_menu": "📋 <b>Ваши подписки</b>\n\n{list}",
        "no_subs": "⌛️ <b>У вас пока нет активных подписок.</b>\n\nВыберите тариф, чтобы оформить доступ.",
        "tariff_desc": "📋 {name}\n\n💰 Цена: {price_text} RUB\n\n{desc}\n\n🔐 Будет получен доступ на срок {duration} к:\n• Shkod VIP👑 (внешняя ссылка)",
        "tariff_desc_paid": "📋 {name}\n\n💰 Цена: {price_text} RUB\n\n{desc}\n\n🔐 Будет получен доступ на срок {duration} к:\n• Shkod VIP👑 (внешняя ссылка)\n\n✅ <b>ТАРИФ ОПЛАЧЕН</b>\n\n🔑 Для получения ссылки напишите в поддержку @Nastia_sup",
        "enter_promo": "🏷️ <b>Введите промокод</b>\n\nНапишите промокод в чат.",
        "promo_success": "✅ Промокод <b>{code}</b> активирован! Скидка {discount}% 🔥\n\n📋 {name}\n💰 Цена: <s>{old_rub} RUB</s> → {new_rub} RUB <b>(-{discount}%)</b>\n\nВыберите валюту для оплаты.",
        "promo_fail": "❌ Промокод не найден или истек.",
        "choose_pay": "📋 {name}\nСрок доступа: {duration}\n💰 Цена: {price_text}\n\n🔒 Будет получен доступ к:\n• {project} (внешняя ссылка)\n\nВыберите валюту для оплаты",
        "pay_rub": "📋 {name}\nСрок доступа: {duration}\n{price_line}💳 Способ оплаты: RollyPay\n\n💰 Итоговая стоимость: {final} RUB\n\n🔒 Будет получен доступ к:\n• {project} (внешняя ссылка)\n\n✅ Счет на оплату сформирован!",
        "pay_stars": "📋 {name}\nСрок доступа: {duration}\n{price_line}💳 Способ оплаты: ЗА ЗВЕЗДЫ ⭐\n\n💰 Итоговая стоимость: {final} STARS\n\nℹ️ <b>Информация по оплате</b>\nПодарить звезды или подарки на этот аккаунт - <a href=\"{support}\">@Nastia_sup</a>\n\nкурс:\n1 ⭐ - 1 рубль",
        "btn_prices": "🛒 Прайс",
        "btn_subs": "🛍️ Подписки",
        "btn_promo": "🏷️ Ввести промокод",
        "btn_pay": "💳 Способы оплаты",
        "btn_back": "👈 НАЗАД",
        "btn_pay_rub": "{price} RUB",
        "btn_pay_rub_disc": "{price} RUB 🏷️(-{disc}%)",
        "btn_pay_stars": "{price} STARS",
        "btn_pay_stars_disc": "{price} STARS 🏷️(-{disc}%)",
        "btn_goto_pay": "✅ ПЕРЕЙТИ К ОПЛАТЕ",
        "btn_new_link": "🔗 Получить новую ссылку",
        "btn_cancel": "🚫 ОТМЕНА",
        "btn_stars_go": "⭐ Stars со скидкой до 42%",
        "payment_success": "✅ <b>Оплата прошла!</b>\n\n🔗 <b>Ваша ссылка доступа (действует 30 секунд):</b>\n{link}\n\n⚠️ <b>Внимание!</b> Ссылка действительна только 30 секунд!\n\nСпасибо за покупку! ❤️",
        "payment_success_test": "✅ <b>Доступ открыт!</b>\n\n🔗 <b>Ваша ссылка доступа (действует 30 секунд):</b>\n{link}\n\n⚠️ <b>Внимание!</b> Ссылка действительна только 30 секунд!\n\nСпасибо за использование бота! ❤️",
        "subs_list_item": "• {name} (оплачен ✅)",
        "admin_panel": "⚙️ <b>Админ-панель</b>\n\n👥 Всего пользователей: {users}\n🎫 Активных промокодов: {promos}\n\nВыберите действие:",
        "promo_created": "✅ Промокод <b>{code}</b> создан!\n\n📊 Скидка: {discount}%\n⏰ Действует: {time}\n\nПромокод уже работает!",
        "promo_deleted": "✅ Промокод <b>{code}</b> удален!",
        "promo_list": "📋 <b>Список активных промокодов:</b>\n\n{promo_list}\n\nВсего: {count}",
        "no_promos": "📭 Нет активных промокодов",
        "enter_code": "📝 <b>Введите название промокода</b>\n\n(только буквы и цифры, без пробелов)\n\n🔄 Чтобы отменить, отправьте /cancel",
        "enter_discount": "📝 <b>Введите размер скидки</b>\n\n(число от 1 до 100)\n\n🔄 Чтобы отменить, отправьте /cancel",
        "enter_expires": "⏰ <b>Введите время действия промокода</b>\n\nУкажите в минутах (например: 5, 30, 60)\n\nЕсли не нужно ограничение, отправьте 0\n\n🔄 Чтобы отменить, отправьте /cancel",
        "btn_create_promo": "➕ Создать промокод",
        "btn_delete_promo": "🗑️ Удалить промокод",
        "btn_list_promos": "📋 Список промокодов",
    }
}

# ==================================================
# ТАРИФЫ
# ==================================================
TARIFFS = {
    "week": {
        "name_ru": "🚀 VIP на неделю — 199 ₽",
        "name_en": "🚀 VIP for a week — 199 ₽",
        "price_rub": 199,
        "price_stars": 180,
        "duration_ru": "7 дней",
        "duration_en": "7 days",
        "desc_ru": "Ты получаешь доступ ко всем материалам на 7 дней:\n— Школьницы\n— Вписки\n— Студентки\n— Закладчицы\n— Альтушки\n— Мини Детск.\n— Жесть\n— И другие, и всё, что я добавляю ежедневно"
    },
    "month": {
        "name_ru": "👑 VIP на месяц — 449 ₽",
        "name_en": "👑 VIP for a month — 449 ₽",
        "price_rub": 449,
        "price_stars": 400,
        "duration_ru": "30 дней",
        "duration_en": "30 days",
        "desc_ru": "Ты получаешь доступ ко ВСЕМ материалам на 30 дней:\n— Школьницы\n— Вписки\n— Студентки\n— Закладчицы\n— Альтушки\n— Мини Детск.\n— Жесть\n— И другие, и всё, что я добавляю ежедневно\n\nПочему это выгодно:\n— Вместо 700 ₽ за один тариф — 449 ₽ за всё.\n— Контент обновляется каждый день."
    },
    "year": {
        "name_ru": "🔥 VIP на год — 1299 ₽",
        "name_en": "🔥 VIP for a year — 1299 ₽",
        "price_rub": 1299,
        "price_stars": 1170,
        "duration_ru": "365 дней",
        "duration_en": "365 days",
        "desc_ru": "Ты получаешь доступ ко ВСЕМ материалам на 365 дней:\n— Школьницы\n— Вписки\n— Студентки\n— Закладчицы\n— Альтушки\n— Мини Детск.\n— Жесть\n— И другие, и всё, что я добавляю ежедневно\n\nПочему это выгодно:\n— Всего 1 299 ₽ за целый год — это 108 ₽ в месяц!\n— Это в 3 раза дешевле, чем покупать месяц за 449 ₽."
    }
}

TEST_TARIFF = {
    "name_ru": "🧪 ТЕСТОВЫЙ тариф (Бесплатно)",
    "name_en": "🧪 TEST tariff (Free)",
    "price_rub": 0,
    "price_stars": 0,
    "duration_ru": "Тестовый",
    "duration_en": "Test",
    "desc_ru": "🧪 Это тестовый тариф. Он полностью БЕСПЛАТНЫЙ!\n\nПросто выберите его и получите ссылку для тестирования."
}

# ==================================================
# ИНИЦИАЛИЗАЦИЯ БОТА
# ==================================================
storage = MemoryStorage()
session = AiohttpSession()
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML), session=session)
dp = Dispatcher(storage=storage)

# --- FSM STATES ---
class PromoStates(StatesGroup):
    waiting_for_promo = State()
    waiting_for_code = State()
    waiting_for_discount = State()
    waiting_for_expires = State()

class MailingStates(StatesGroup):
    waiting_for_content = State()

# ==================================================
# ФУНКЦИИ ДЛЯ БОТА
# ==================================================
async def create_rollypay_payment(amount: int, user_id: int, tariff_key: str, tariff_name: str) -> str:
    discounts = get_user_discounts(user_id)
    final_price = amount
    discount_code = None
    
    if discounts:
        max_discount = max(d[1] for d in discounts)
        if max_discount > 0:
            final_price = int(amount * (1 - max_discount / 100))
            for code, percent, used in discounts:
                if percent == max_discount and used == 0:
                    mark_discount_used(user_id, code)
                    discount_code = code
                    break
    
    url = "https://rollypay.io/api/v1/payments"
    headers = {
        "X-API-Key": ROLLYPAY_API_KEY,
        "Content-Type": "application/json",
        "X-Nonce": str(uuid.uuid4())
    }
    payload = {
        "amount": str(final_price),
        "payment_currency": "RUB",
        "order_id": f"order_{user_id}_{tariff_key}_{int(datetime.now().timestamp())}",
        "description": f"Оплата доступа #{user_id}_{tariff_key}" + (f" (скидка {discount_code})" if discount_code else ""),
        "callback_url": ROLLYPAY_CALLBACK_URL,
        "success_url": "https://t.me/blogprivatbot",
        "fail_url": "https://t.me/blogprivatbot",
        "merchant_fee": "true"
    }
    
    async with aiohttp.ClientSession() as client:
        async with client.post(url, headers=headers, json=payload) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("pay_url")
            else:
                return None

async def create_one_time_link(chat_id: str) -> str:
    try:
        expire_date = datetime.now() + timedelta(seconds=30)
        invite_link = await bot.create_chat_invite_link(
            chat_id=chat_id,
            member_limit=1,
            expire_date=expire_date,
            creates_join_request=False
        )
        return invite_link.invite_link
    except Exception as e:
        logging.error(f"Ошибка создания ссылки: {e}")
        return None

async def save_payment_and_send_link(message: Message, tariff_key: str, lang: str, user_id: int):
    if tariff_key not in CHANNEL_IDS:
        await message.answer("❌ Ошибка: канал для этого тарифа не настроен.")
        return
    
    chat_id = CHANNEL_IDS[tariff_key]
    link = await create_one_time_link(chat_id)
    
    if not link:
        await message.answer("❌ Ошибка создания ссылки.")
        return
    
    add_paid_tariff(user_id, tariff_key)
    
    if tariff_key == "test":
        text = LANG[lang]["payment_success_test"].format(link=link)
    else:
        text = LANG[lang]["payment_success"].format(link=link)
    
    await message.answer(text, disable_web_page_preview=False)

# ==================================================
# КЛАВИАТУРЫ
# ==================================================
def get_main_keyboard(lang):
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=LANG[lang]["btn_prices"]), KeyboardButton(text=LANG[lang]["btn_subs"])]
    ], resize_keyboard=True)

def get_tariff_keyboard(lang):
    buttons = []
    for key, data in TARIFFS.items():
        name = data['name_ru'] if lang == 'ru' else data['name_en']
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"tariff_{key}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_test_tariff_keyboard(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 ОПЛАТИТЬ", callback_data="pay_test")],
        [InlineKeyboardButton(text="👈 НАЗАД", callback_data="back_to_prices")]
    ])

def get_tariff_details_keyboard(tariff_key, lang, user_id):
    buttons = []
    buttons.append([InlineKeyboardButton(text=LANG[lang]["btn_promo"], callback_data=f"enter_promo_{tariff_key}")])
    
    is_paid = is_tariff_paid(user_id, tariff_key)
    
    if not is_paid:
        buttons.append([InlineKeyboardButton(text=LANG[lang]["btn_pay"], callback_data=f"choose_pay_{tariff_key}")])
    
    buttons.append([InlineKeyboardButton(text=LANG[lang]["btn_back"], callback_data="back_to_prices")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_payment_method_keyboard(tariff_key, discount_percent=0, lang="ru"):
    tariff = TARIFFS[tariff_key]
    
    if discount_percent > 0:
        rub_price = int(tariff['price_rub'] * (1 - discount_percent / 100))
        stars_price = int(tariff['price_stars'] * (1 - discount_percent / 100))
        btn_rub = LANG[lang]["btn_pay_rub_disc"].format(price=rub_price, disc=discount_percent)
        btn_stars = LANG[lang]["btn_pay_stars_disc"].format(price=stars_price, disc=discount_percent)
    else:
        rub_price = tariff['price_rub']
        stars_price = tariff['price_stars']
        btn_rub = LANG[lang]["btn_pay_rub"].format(price=rub_price)
        btn_stars = LANG[lang]["btn_pay_stars"].format(price=stars_price)

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn_rub, callback_data=f"pay_rub_{tariff_key}")],
        [InlineKeyboardButton(text=btn_stars, callback_data=f"pay_stars_{tariff_key}")],
        [InlineKeyboardButton(text=LANG[lang]["btn_back"], callback_data="back_to_prices")]
    ])

def get_payment_action_keyboard(payment_url, tariff_key, lang="ru"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=LANG[lang]["btn_goto_pay"], url=payment_url)],
        [InlineKeyboardButton(text=LANG[lang]["btn_new_link"], callback_data=f"refresh_link_{tariff_key}")],
        [InlineKeyboardButton(text=LANG[lang]["btn_back"], callback_data="back_to_prices")]
    ])

def get_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать промокод", callback_data="admin_create_promo")],
        [InlineKeyboardButton(text="🗑️ Удалить промокод", callback_data="admin_delete_promo")],
        [InlineKeyboardButton(text="📋 Список промокодов", callback_data="admin_list_promos")],
        [InlineKeyboardButton(text="📨 Рассылка", callback_data="admin_mailing")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")]
    ])

# ==================================================
# ХЭНДЛЕРЫ
# ==================================================

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    first_name = message.from_user.first_name or "Пользователь"
    username = message.from_user.username
    
    add_user(user_id, first_name, username)
    
    lang = "ru"
    
    welcome_text = LANG[lang]["start_welcome"].format(
        name=first_name,
        offer=DOCS_RU["offer"],
        policy=DOCS_RU["policy"]
    )
    await message.answer(welcome_text, disable_web_page_preview=True, reply_markup=get_main_keyboard(lang))
    
    menu_text = LANG[lang]["prices_menu"]
    await message.answer(menu_text, reply_markup=get_tariff_keyboard(lang))

@dp.message(F.text == "🛒 Прайс")
async def show_prices(message: Message):
    lang = "ru"
    await message.answer(LANG[lang]["prices_menu"], reply_markup=get_tariff_keyboard(lang))

@dp.message(F.text == "🛍️ Подписки")
async def show_subscriptions_button(message: Message):
    lang = "ru"
    user_id = message.from_user.id
    
    paid_list = get_paid_tariffs(user_id)
    
    if paid_list:
        subs_list = []
        for tariff_key in paid_list:
            if tariff_key == "test":
                name = TEST_TARIFF['name_ru'] if lang == "ru" else TEST_TARIFF['name_en']
                subs_list.append(LANG[lang]["subs_list_item"].format(name=name))
            elif tariff_key in TARIFFS:
                name = TARIFFS[tariff_key]['name_ru'] if lang == "ru" else TARIFFS[tariff_key]['name_en']
                subs_list.append(LANG[lang]["subs_list_item"].format(name=name))
        
        if subs_list:
            text = LANG[lang]["subs_menu"].format(list="\n".join(subs_list))
            await message.answer(text)
            return
    
    await message.answer(LANG[lang]["no_subs"])

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Только для админов!")
        return
    
    user_count = get_user_count()
    promo_count = len(get_all_promocodes())
    
    text = LANG["ru"]["admin_panel"].format(users=user_count, promos=promo_count)
    await message.answer(text, reply_markup=get_admin_keyboard())

# --- УПРАВЛЕНИЕ ПРОМОКОДАМИ ---

@dp.callback_query(F.data == "admin_create_promo")
async def admin_create_promo_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Только для админов!", show_alert=True)
        return
    
    await callback.message.delete()
    await callback.message.answer(
        LANG["ru"]["enter_code"],
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚫 Отмена", callback_data="admin_cancel")]
        ])
    )
    await state.set_state(PromoStates.waiting_for_code)
    await callback.answer()

@dp.message(PromoStates.waiting_for_code)
async def process_promo_code_name(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Только для админов!")
        return
    
    code = message.text.strip().upper()
    
    if not re.match(r'^[A-Z0-9]+$', code):
        await message.answer("❌ Промокод может содержать только буквы и цифры!")
        return
    
    if check_promocode(code) is not None:
        await message.answer("❌ Такой промокод уже существует!")
        return
    
    await state.update_data(promo_code=code)
    await message.answer(LANG["ru"]["enter_discount"])
    await state.set_state(PromoStates.waiting_for_discount)

@dp.message(PromoStates.waiting_for_discount)
async def process_promo_discount(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Только для админов!")
        return
    
    try:
        discount = int(message.text.strip())
        if discount < 1 or discount > 100:
            await message.answer("❌ Скидка должна быть от 1 до 100%!")
            return
    except ValueError:
        await message.answer("❌ Введите число от 1 до 100!")
        return
    
    await state.update_data(promo_discount=discount)
    await message.answer(LANG["ru"]["enter_expires"])
    await state.set_state(PromoStates.waiting_for_expires)

@dp.message(PromoStates.waiting_for_expires)
async def process_promo_expires(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Только для админов!")
        return
    
    try:
        minutes = int(message.text.strip())
        if minutes < 0:
            await message.answer("❌ Введите положительное число или 0!")
            return
        expires_minutes = minutes if minutes > 0 else None
    except ValueError:
        expires_minutes = None
    
    data = await state.get_data()
    code = data.get("promo_code")
    discount = data.get("promo_discount")
    
    success = create_promocode(code, discount, expires_minutes)
    
    if success:
        time_text = f"{expires_minutes} минут" if expires_minutes else "Бессрочно"
        text = LANG["ru"]["promo_created"].format(code=code, discount=discount, time=time_text)
        await message.answer(text)
        
        user_count = get_user_count()
        promo_count = len(get_all_promocodes())
        admin_text = LANG["ru"]["admin_panel"].format(users=user_count, promos=promo_count)
        await message.answer(admin_text, reply_markup=get_admin_keyboard())
    else:
        await message.answer("❌ Ошибка создания промокода!")
    
    await state.clear()

@dp.callback_query(F.data == "admin_delete_promo")
async def admin_delete_promo_start(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Только для админов!", show_alert=True)
        return
    
    promos = get_all_promocodes()
    if not promos:
        await callback.answer("📭 Нет активных промокодов!", show_alert=True)
        return
    
    buttons = []
    for code, data in promos:
        if data['expires_at']:
            remaining = int((data['expires_at'] - time.time()) / 60)
            expires_text = f" (осталось {remaining} мин)"
        else:
            expires_text = " (бессрочно)"
        buttons.append([InlineKeyboardButton(
            text=f"{code} - {data['discount']}%{expires_text}",
            callback_data=f"delete_promo_{code}"
        )])
    
    buttons.append([InlineKeyboardButton(text="🚫 Отмена", callback_data="admin_cancel")])
    
    await callback.message.delete()
    await callback.message.answer(
        "🗑️ <b>Выберите промокод для удаления:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_promo_"))
async def process_delete_promo(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Только для админов!", show_alert=True)
        return
    
    code = callback.data.replace("delete_promo_", "")
    success = delete_promocode(code)
    
    if success:
        await callback.answer(f"✅ Промокод {code} удален!", show_alert=True)
        await callback.message.edit_text(LANG["ru"]["promo_deleted"].format(code=code))
        
        user_count = get_user_count()
        promo_count = len(get_all_promocodes())
        admin_text = LANG["ru"]["admin_panel"].format(users=user_count, promos=promo_count)
        await callback.message.answer(admin_text, reply_markup=get_admin_keyboard())
    else:
        await callback.answer("❌ Ошибка удаления!", show_alert=True)

@dp.callback_query(F.data == "admin_list_promos")
async def admin_list_promos(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Только для админов!", show_alert=True)
        return
    
    promos = get_all_promocodes()
    
    if not promos:
        await callback.message.edit_text(LANG["ru"]["no_promos"])
        return
    
    promo_list = []
    for code, data in promos:
        if data['expires_at']:
            remaining = int((data['expires_at'] - time.time()) / 60)
            expires_text = f"⏰ Осталось {remaining} мин"
        else:
            expires_text = "♾️ Бессрочно"
        promo_list.append(f"• <b>{code}</b> — {data['discount']}% ({expires_text})")
    
    text = LANG["ru"]["promo_list"].format(
        promo_list="\n".join(promo_list),
        count=len(promos)
    )
    
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👈 Назад", callback_data="admin_back")]
    ]))
    await callback.answer()

@dp.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Только для админов!", show_alert=True)
        return
    
    user_count = get_user_count()
    promo_count = len(get_all_promocodes())
    text = LANG["ru"]["admin_panel"].format(users=user_count, promos=promo_count)
    await callback.message.edit_text(text, reply_markup=get_admin_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "admin_cancel")
async def admin_cancel(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Только для админов!", show_alert=True)
        return
    
    await state.clear()
    user_count = get_user_count()
    promo_count = len(get_all_promocodes())
    text = LANG["ru"]["admin_panel"].format(users=user_count, promos=promo_count)
    await callback.message.delete()
    await callback.message.answer(text, reply_markup=get_admin_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Только для админов!", show_alert=True)
        return
    
    user_count = get_user_count()
    promo_count = len(get_all_promocodes())
    
    await callback.message.edit_text(
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: {user_count}\n"
        f"🎫 Активных промокодов: {promo_count}",
        reply_markup=get_admin_keyboard()
    )
    await callback.answer()

# --- РАССЫЛКА ---

@dp.callback_query(F.data == "admin_mailing")
async def admin_mailing_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Только для админов!", show_alert=True)
        return
    
    await callback.message.delete()
    await callback.message.answer(
        "📨 <b>Рассылка</b>\n\n"
        "Отправь мне сообщение (текст, фото, видео, GIF, документ), "
        "и я разошлю его ВСЕМ пользователям бота.\n\n"
        "🔄 Чтобы отменить, отправь /cancel"
    )
    await state.set_state(MailingStates.waiting_for_content)

@dp.message(MailingStates.waiting_for_content)
async def process_mailing_content(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Только для админов!")
        return
    
    await message.answer("⏳ Начинаю рассылку...")
    
    users = get_all_users()
    
    if not users:
        await message.answer("❌ Нет пользователей для рассылки!")
        await state.clear()
        return
    
    success = 0
    failed = 0
    
    for user_id in users:
        try:
            if message.text:
                await bot.send_message(user_id, message.text, parse_mode="HTML")
            elif message.photo:
                await bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption)
            elif message.video:
                await bot.send_video(user_id, message.video.file_id, caption=message.caption)
            elif message.animation:
                await bot.send_animation(user_id, message.animation.file_id, caption=message.caption)
            elif message.document:
                await bot.send_document(user_id, message.document.file_id, caption=message.caption)
            else:
                await message.answer("❌ Неподдерживаемый тип сообщения!")
                await state.clear()
                return
            
            success += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            failed += 1
    
    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📤 Отправлено: {success}\n"
        f"❌ Не доставлено: {failed}\n"
        f"👥 Всего пользователей: {len(users)}"
    )
    await state.clear()

# --- ТАРИФЫ ---

@dp.message(Command("test67"))
async def cmd_test67(message: Message):
    lang = "ru"
    user_id = message.from_user.id
    
    is_paid = is_tariff_paid(user_id, "test")
    
    if is_paid:
        text = f"""📋 <b>{TEST_TARIFF['name_ru']}</b>

💰 Цена: БЕСПЛАТНО 🎉
Срок доступа: {TEST_TARIFF['duration_ru']}

{TEST_TARIFF['desc_ru']}

✅ <b>ТАРИФ ОПЛАЧЕН</b>

🔑 Для получения ссылки напишите в поддержку @Nastia_sup"""
        await message.answer(text)
        return
    
    text = f"""📋 <b>{TEST_TARIFF['name_ru']}</b>

💰 Цена: БЕСПЛАТНО 🎉
Срок доступа: {TEST_TARIFF['duration_ru']}

{TEST_TARIFF['desc_ru']}"""
    
    await message.answer(text, reply_markup=get_test_tariff_keyboard(lang))

@dp.callback_query(F.data == "pay_test")
async def pay_test_tariff(callback: CallbackQuery):
    lang = "ru"
    user_id = callback.from_user.id
    
    if is_tariff_paid(user_id, "test"):
        await callback.answer("❌ Вы уже активировали тестовый тариф!", show_alert=True)
        return
    
    await callback.message.delete()
    await save_payment_and_send_link(callback.message, "test", lang, user_id)
    await callback.answer("✅ Доступ открыт!")

@dp.callback_query(F.data == "back_to_prices")
async def back_to_prices(callback: CallbackQuery):
    lang = "ru"
    await callback.answer()
    await callback.message.edit_text(LANG[lang]["prices_menu"], reply_markup=get_tariff_keyboard(lang))

@dp.callback_query(F.data.startswith("tariff_"))
async def show_tariff_details(callback: CallbackQuery, state: FSMContext):
    tariff_key = callback.data.replace("tariff_", "")
    
    if tariff_key not in TARIFFS:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    tariff = TARIFFS[tariff_key]
    lang = "ru"
    data = await state.get_data()
    discount = data.get("discount", 0)
    user_id = callback.from_user.id
    
    name = tariff['name_ru']
    duration = tariff['duration_ru']
    desc = tariff['desc_ru']
    
    if tariff['price_rub'] == 0:
        price_text = "БЕСПЛАТНО 🎉"
    elif discount > 0:
        new_price = int(tariff['price_rub'] * (1 - discount / 100))
        price_text = f"<s>{tariff['price_rub']} 🇷🇺RUB</s> → {new_price} 🇷🇺RUB <b>(-{discount}%)</b>"
    else:
        price_text = f"{tariff['price_rub']} 🇷🇺RUB"
    
    is_paid = is_tariff_paid(user_id, tariff_key)
    
    if is_paid:
        text = LANG[lang]["tariff_desc_paid"].format(
            name=name,
            price_text=price_text,
            duration=duration,
            desc=desc
        )
    else:
        text = LANG[lang]["tariff_desc"].format(
            name=name,
            price_text=price_text,
            duration=duration,
            desc=desc
        )
    
    await callback.message.edit_text(text, reply_markup=get_tariff_details_keyboard(tariff_key, lang, user_id))

@dp.callback_query(F.data.startswith("enter_promo_"))
async def enter_promo(callback: CallbackQuery, state: FSMContext):
    tariff_key = callback.data.replace("enter_promo_", "")
    
    if tariff_key not in TARIFFS:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    lang = "ru"
    await state.update_data(current_tariff=tariff_key)
    await callback.message.edit_text(
        LANG[lang]["enter_promo"],
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=LANG[lang]["btn_cancel"], callback_data=f"cancel_promo_{tariff_key}")]])
    )
    await state.set_state(PromoStates.waiting_for_promo)

@dp.message(PromoStates.waiting_for_promo)
async def process_promo(message: Message, state: FSMContext):
    promo_code = message.text.strip().upper()
    data = await state.get_data()
    tariff_key = data.get("current_tariff")
    lang = "ru"
    
    if not tariff_key or tariff_key not in TARIFFS:
        await state.clear()
        await message.answer("❌ Ошибка. Попробуйте выбрать тариф заново.")
        return

    discount = check_promocode(promo_code)
    
    if discount is not None:
        add_user_discount(message.from_user.id, promo_code, discount)
        
        tariff = TARIFFS[tariff_key]
        name = tariff['name_ru']
        new_rub = int(tariff['price_rub'] * (1 - discount / 100))
        
        text = LANG[lang]["promo_success"].format(
            code=promo_code, 
            discount=discount, 
            name=name, 
            old_rub=tariff['price_rub'], 
            new_rub=new_rub
        )
        await message.answer(text, reply_markup=get_payment_method_keyboard(tariff_key, discount, lang))
        await state.clear()
    else:
        await message.answer(LANG[lang]["promo_fail"])

@dp.callback_query(F.data.startswith("cancel_promo_"))
async def cancel_promo(callback: CallbackQuery, state: FSMContext):
    tariff_key = callback.data.replace("cancel_promo_", "")
    
    if tariff_key not in TARIFFS:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    lang = "ru"
    await state.clear()
    await callback.message.delete()
    tariff = TARIFFS[tariff_key]
    data = await state.get_data()
    discount = data.get("discount", 0)
    user_id = callback.from_user.id
    
    name = tariff['name_ru']
    duration = tariff['duration_ru']
    desc = tariff['desc_ru']

    if tariff['price_rub'] == 0:
        price_text = "БЕСПЛАТНО 🎉"
    elif discount > 0:
        new_price = int(tariff['price_rub'] * (1 - discount / 100))
        price_text = f"<s>{tariff['price_rub']} RUB</s> -> {new_price} RUB <b>(-{discount}%)</b>"
    else:
        price_text = f"{tariff['price_rub']} RUB"

    is_paid = is_tariff_paid(user_id, tariff_key)
    
    if is_paid:
        text = LANG[lang]["tariff_desc_paid"].format(
            name=name,
            price_text=price_text,
            duration=duration,
            desc=desc
        )
    else:
        text = LANG[lang]["tariff_desc"].format(
            name=name,
            price_text=price_text,
            duration=duration,
            desc=desc
        )
    
    await callback.message.answer(text, reply_markup=get_tariff_details_keyboard(tariff_key, lang, user_id))

@dp.callback_query(F.data.startswith("choose_pay_"))
async def choose_payment(callback: CallbackQuery, state: FSMContext):
    tariff_key = callback.data.replace("choose_pay_", "")
    
    if tariff_key not in TARIFFS:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    tariff = TARIFFS[tariff_key]
    
    if tariff['price_rub'] == 0:
        lang = "ru"
        user_id = callback.from_user.id
        await callback.message.delete()
        await save_payment_and_send_link(callback.message, tariff_key, lang, user_id)
        await callback.answer("✅ Доступ открыт!")
        return
    
    lang = "ru"
    data = await state.get_data()
    discount = data.get("discount", 0)
    
    name = tariff['name_ru']
    duration = tariff['duration_ru']
    
    if discount > 0:
        show_rub = int(tariff['price_rub'] * (1 - discount / 100))
        price_text = f"<s>{tariff['price_rub']} RUB</s> → {show_rub} RUB (-{discount}%)"
    else:
        show_rub = tariff['price_rub']
        price_text = f"{show_rub} RUB"
    
    text = LANG[lang]["choose_pay"].format(name=name, duration=duration, price_text=price_text, project=PROJECT_NAME)
    await callback.message.edit_text(text, reply_markup=get_payment_method_keyboard(tariff_key, discount, lang))

@dp.callback_query(F.data.startswith("pay_rub_"))
async def process_rub_payment(callback: CallbackQuery, state: FSMContext):
    tariff_key = callback.data.replace("pay_rub_", "")
    
    if tariff_key not in TARIFFS:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    tariff = TARIFFS[tariff_key]
    
    if tariff['price_rub'] == 0:
        lang = "ru"
        user_id = callback.from_user.id
        await callback.message.delete()
        await save_payment_and_send_link(callback.message, tariff_key, lang, user_id)
        await callback.answer("✅ Доступ открыт!")
        return
    
    lang = "ru"
    data = await state.get_data()
    discount = data.get("discount", 0)
    
    final_price = int(tariff['price_rub'] * (1 - discount / 100))
    user_id = callback.from_user.id
    
    await state.update_data(pending_tariff=tariff_key)
    
    payment_url = await create_rollypay_payment(final_price, user_id, tariff_key, tariff['name_ru'])
    
    if payment_url:
        name = tariff['name_ru']
        duration = tariff['duration_ru']
        
        if discount > 0:
            price_line = f"💰 Цена: <s>{tariff['price_rub']} RUB</s> → {final_price} RUB (-{discount}%)\n"
        else:
            price_line = f"💰 Цена: {final_price} RUB\n"
        
        text = LANG[lang]["pay_rub"].format(name=name, duration=duration, price_line=price_line, final=final_price, project=PROJECT_NAME)
        await callback.message.edit_text(text, reply_markup=get_payment_action_keyboard(payment_url, tariff_key, lang))
    else:
        await callback.answer("❌ Ошибка создания платежа. Попробуйте позже.", show_alert=True)

@dp.callback_query(F.data.startswith("pay_stars_"))
async def process_stars_payment(callback: CallbackQuery, state: FSMContext):
    tariff_key = callback.data.replace("pay_stars_", "")
    
    if tariff_key not in TARIFFS:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    tariff = TARIFFS[tariff_key]
    
    if tariff['price_rub'] == 0:
        lang = "ru"
        user_id = callback.from_user.id
        await callback.message.delete()
        await save_payment_and_send_link(callback.message, tariff_key, lang, user_id)
        await callback.answer("✅ Доступ открыт!")
        return
    
    lang = "ru"
    data = await state.get_data()
    discount = data.get("discount", 0)
    name = tariff['name_ru']
    duration = tariff['duration_ru']
    
    final_price = int(tariff['price_stars'] * (1 - discount / 100))
    demo_stars_url = f"https://t.me/TweetlyStarsBot?start=demo_stars_{tariff_key}"
    
    if discount > 0:
        price_line = f"💰 Цена: <s>{tariff['price_stars']} STARS</s> → {final_price} STARS (-{discount}%)\n"
    else:
        price_line = f"💰 Цена: {final_price} STARS\n"
    
    support = SUPPORT_CONTACT_RU
    text = LANG[lang]["pay_stars"].format(name=name, duration=duration, price_line=price_line, final=final_price, project=PROJECT_NAME, support=support)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=LANG[lang]["btn_stars_go"], url=demo_stars_url)],
        [InlineKeyboardButton(text=LANG[lang]["btn_back"], callback_data=f"choose_pay_{tariff_key}")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("refresh_link_"))
async def refresh_link(callback: CallbackQuery):
    tariff_key = callback.data.replace("refresh_link_", "")
    
    if tariff_key not in TARIFFS:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    tariff = TARIFFS[tariff_key]
    user_id = callback.from_user.id
    final_price = tariff['price_rub']

    payment_url = await create_rollypay_payment(final_price, user_id, tariff_key, tariff['name_ru'])

    if payment_url:
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Перейти к оплате", url=payment_url)],
                [InlineKeyboardButton(text="🔗 Получить новую ссылку", callback_data=f"refresh_link_{tariff_key}")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_prices")]
            ])
        )
        await callback.answer("✅ Новая ссылка сгенерирована!", show_alert=True)
    else:
        await callback.answer("❌ Ошибка создания новой ссылки.", show_alert=True)

@dp.message(Command("reset"))
async def cmd_reset(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды!")
        return
    await message.answer("🔄 Выполняю сброс...")
    await message.answer("✅ Бот сброшен!")

@dp.message(Command("language"))
async def cmd_language(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang_ru")],
        [InlineKeyboardButton(text="🇬🇧 English", callback_data="set_lang_en")]
    ])
    await message.answer("🌍 Выберите язык:", reply_markup=kb)

@dp.callback_query(F.data.startswith("set_lang_"))
async def process_lang_change(callback: CallbackQuery, state: FSMContext):
    lang = callback.data.replace("set_lang_", "")
    await state.update_data(lang=lang)
    await callback.answer()
    await callback.message.delete()
    await callback.message.answer(f"✅ Язык установлен на {'Русский' if lang == 'ru' else 'English'}! Нажмите /start")

@dp.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("✅ Отменено.")

# ==================================================
# ЗАПУСК
# ==================================================
async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    
    cleanup_thread = threading.Thread(target=cleanup_expired_promocodes, daemon=True)
    cleanup_thread.start()
    
    print("=" * 60)
    print("🚀 БОТ ЗАПУЩЕН!")
    print("📦 База данных: Supabase + SQLite")
    print("🎫 Промокоды в ОЗУ (автоочистка каждую минуту)")
    print("=" * 60)
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("✅ Flask запущен в фоновом потоке!")
    asyncio.run(main())
