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
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# ==================================================
# FLASK ДЛЯ RENDER
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
SUPABASE_URL = "postgresql://postgres:5369fasF352@db.pyjpmckzoexfktjezjho.supabase.co:6543/postgres"

engine = create_engine(
    SUPABASE_URL,
    echo=False,
    pool_pre_ping=True
)

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
                text("""
                    INSERT INTO user_discounts (user_id, discount_code, discount_percent)
                    VALUES (:id, :code, :percent)
                    ON CONFLICT (user_id, discount_code) DO NOTHING
                """),
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
            result = conn.execute(
                text("SELECT discount_code, discount_percent, used FROM user_discounts WHERE user_id = :id AND used = 0"),
                {"id": user_id}
            )
            return result.fetchall()
    except Exception as e:
        logging.error(f"Ошибка получения скидок: {e}")
        return []

def mark_discount_used(user_id: int, discount_code: str):
    try:
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE user_discounts SET used = 1 WHERE user_id = :id AND discount_code = :code"),
                {"id": user_id, "code": discount_code}
            )
            conn.commit()
        return True
    except Exception as e:
        logging.error(f"Ошибка отметки скидки: {e}")
        return False

# ==================================================
# КОНФИГУРАЦИЯ
# ==================================================
ROLLYPAY_API_KEY = "z39_r_COJdiB7PWeddOYvzT2rx4cjIbS1m4JJcgBTi0"
ROLLYPAY_CALLBACK_URL = "https://t-bot-18jz.onrender.com/webhook"

BOT_TOKEN = "8405743009:AAFmmRNDGWGDnxQbIDPVtiAprSnh0aq9g0U"
PROJECT_NAME = "VIP"
SUPPORT_CONTACT_RU = "https://t.me/Nastia_sup"
SUPPORT_CONTACT_EN = "https://t.me/Nastia_sup"
ADMIN_IDS = [8370080332, 8559381302]

# ==================================================
# ID КАНАЛОВ (для каждого тарифа)
# ==================================================
CHANNEL_IDS = {
    "week": "-1004267025056",
    "month": "-1004478645537",
    "year": "-1004325704012",
    "test": "-1003875225035",
}

# ==================================================
# БАЗА ДАННЫХ (SQLite)
# ==================================================
DB_PATH = "users.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS paid_tariffs (
            user_id INTEGER,
            tariff_key TEXT,
            paid_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, tariff_key)
        )
    ''')
    conn.commit()
    conn.close()
    logging.info("✅ База данных инициализирована")

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
        "start_promo": "🎉 <b>Промокод {code} активирован! Скидка {discount}%!</b>",
        "start_welcome": "👋 Привет, {name}!\n\n<a href=\"{offer}\">Пользовательское соглашение</a>\n<a href=\"{policy}\">Политика конфиденциальности</a>",
        "prices_menu": "📋 <b>Прайс</b>\n\nВыберите тариф, чтобы узнать подробности и оформить покупку.",
        "subs_menu": "📋 <b>Ваши активные подписки</b>\n\n{list}",
        "no_subs": "⌛️ <b>У Вас нет действующих подписок.</b>\n\nВыберите тариф, чтобы оформить доступ.",
        "tariff_desc": "📋 <b>{name}</b>\n\n💰 Цена: {price_text}\nСрок доступа: {duration}\n\n{desc}",
        "tariff_desc_paid": "📋 <b>{name}</b>\n\n💰 Цена: {price_text}\nСрок доступа: {duration}\n\n{desc}\n\n✅ <b>ТАРИФ ОПЛАЧЕН</b>\n\n🔑 Для получения ссылки напишите в поддержку @Nastia_sup",
        "enter_promo": "🏷️ <b>Введите код промокода</b>\n\nНапишите промокод в чат.",
        "promo_success": "✅ Промокод <b>{code}</b> активирован! Скидка {discount}% 🔥\n\n📋 <b>{name}</b>\n💰 Цена: <s>{old_rub} RUB</s> → {new_rub} RUB <b>(-{discount}%)</b>\n\nВыберите валюту для оплаты.",
        "promo_fail": "❌ Промокод не найден. Попробуйте еще раз (или нажмите ◀️ Отмена).",
        "choose_pay": "📋 <b>{name}</b>\nСрок доступа: {duration}\n💰 Цена: {price_text}\n\n🔒 Будет получен доступ к:\n• {project} (внешняя ссылка)\n\nВыберите валюту для оплаты тарифа",
        "pay_rub": "📋 <b>{name}</b>\nСрок доступа: {duration}\n{price_line}💳 Способ оплаты: RollyPay\n\n💰 Итоговая стоимость: {final} RUB\n\n🔒 Будет получен доступ к:\n• {project} (внешняя ссылка)\n\n✅ Счет на оплату сформирован!",
        "pay_stars": "📋 <b>{name}</b>\nСрок доступа: {duration}\n{price_line}💳 Способ оплаты: ЗА ЗВЕЗДЫ ⭐\n\n💰 Итоговая стоимость: {final} STARS\n\nℹ️ <b>Информация по оплате</b>\nПодарить звезды или подарки на этот аккаунт - <a href=\"{support}\">@Nastia_sup</a>\n\nкурс:\n1 ⭐ - 1 рубль",
        "refresh_link": "♻️ <i>Ссылка обновлена!</i>",
        "btn_prices": "💵 Тарифы",
        "btn_subs": "⏳ Мои подписки",
        "btn_promo": "🏷️ Ввести промокод",
        "btn_pay": "💳 Способы оплаты",
        "btn_back": "👈 НАЗАД",
        "btn_pay_rub": "{price} RUB",
        "btn_pay_rub_disc": "{price} RUB 🏷️(-{disc}%)",
        "btn_pay_stars": "{price} STARS",
        "btn_pay_stars_disc": "{price} STARS 🏷️(-{disc}%)",
        "btn_goto_pay": "✅ ПЕРЕЙТИ К ОПЛАТЕ",
        "btn_new_link": "🔗 Получить новую ссылку",
        "btn_to_prices": "✅ КУПИТЬ ПОДПИСКУ",
        "btn_cancel": "🚫 ОТМЕНА",
        "btn_stars_go": "⭐ Stars со скидкой до 42%",
        "btn_lang": "🇷🇺 Язык",
        "payment_success": "✅ <b>Оплата прошла!</b>\n\n🔗 <b>Ваша ссылка доступа (действует 30 секунд):</b>\n{link}\n\n⚠️ <b>Внимание!</b> Ссылка действительна только 30 секунд!\n\nСпасибо за покупку! ❤️",
        "payment_success_test": "✅ <b>Доступ открыт!</b>\n\n🔗 <b>Ваша ссылка доступа (действует 30 секунд):</b>\n{link}\n\n⚠️ <b>Внимание!</b> Ссылка действительна только 30 секунд!\n\nСпасибо за использование бота! ❤️",
        "subs_list_item": "• {name} (оплачен ✅)",
        "main_menu_text": "🔥 VIP-ДОСТУП КО ВСЕМ МАТЕРИАЛАМ\n\nЗдесь ты получаешь всё в одном месте:\n— Школьницы, вписки, студентки, альтушки\n— Мини Детск, жесть, износы и другие тарифы\n— Ежедневное обновление контента\n— Сообщество, приватный чат для VIP пользователей\n\nВместо 700 ₽ за один тариф — 449 ₽ в месяц за всё.\n\nВыбери свой тариф ниже 👇"
    },
    "en": {
        "start_promo": "🎉 <b>Promo code {code} activated! {discount}% discount!</b>",
        "start_welcome": "👋 Hello, {name}!\n\n<a href=\"{offer}\">Terms of Service</a>\n<a href=\"{policy}\">Privacy Policy</a>",
        "prices_menu": "📋 <b>Prices</b>\n\nSelect a tariff to view details and make a purchase.",
        "subs_menu": "📋 <b>Your active subscriptions</b>\n\n{list}",
        "no_subs": "⌛️ <b>You don't have any active subscriptions.</b>\n\nSelect a tariff to get access.",
        "tariff_desc": "📋 <b>{name}</b>\n\n💰 Price: {price_text}\nAccess duration: {duration}\n\n{desc}",
        "tariff_desc_paid": "📋 <b>{name}</b>\n\n💰 Price: {price_text}\nAccess duration: {duration}\n\n{desc}\n\n✅ <b>TARIFF PAID</b>\n\n🔑 To get the link contact support @Nastia_sup",
        "enter_promo": "🏷️ <b>Enter promo code</b>\n\nType the promo code in the chat.",
        "promo_success": "✅ Promo code <b>{code}</b> activated! {discount}% discount 🔥\n\n📋 <b>{name}</b>\n💰 Price: <s>{old_rub} RUB</s> → {new_rub} RUB <b>(-{discount}%)</b>\n\nChoose a currency for payment.",
        "promo_fail": "❌ Promo code not found. Try again (or press ◀️ Cancel).",
        "choose_pay": "📋 <b>{name}</b>\nAccess duration: {duration}\n💰 Price: {price_text}\n\n🔒 You will get access to:\n• {project} (external link)\n\nChoose a currency for payment",
        "pay_rub": "📋 <b>{name}</b>\nAccess duration: {duration}\n{price_line}💳 Payment method: RollyPay\n\n💰 Total cost: {final} RUB\n\n🔒 You will get access to:\n• {project} (external link)\n\n✅ Invoice created!",
        "pay_stars": "📋 <b>{name}</b>\nAccess duration: {duration}\n{price_line}💳 Payment method: FOR STARS ⭐\n\n💰 Total cost: {final} STARS\n\nℹ️ <b>Payment info</b>\nSend stars or gifts to this account - <a href=\"{support}\">@Nastia_sup</a>\n\nRate:\n1 ⭐ - 1 ruble",
        "refresh_link": "♻️ <i>Link refreshed!</i>",
        "btn_prices": "💵 Prices",
        "btn_subs": "⏳ My subscriptions",
        "btn_promo": "🏷️ Enter promo code",
        "btn_pay": "💳 Payment methods",
        "btn_back": "👈 Back",
        "btn_pay_rub": "{price} RUB",
        "btn_pay_rub_disc": "{price} RUB 🏷️(-{disc}%)",
        "btn_pay_stars": "{price} STARS",
        "btn_pay_stars_disc": "{price} STARS 🏷️(-{disc}%)",
        "btn_goto_pay": "✅ GO TO PAYMENT",
        "btn_new_link": "🔗 Get new link",
        "btn_to_prices": "✅ BUY SUBSCRIPTION",
        "btn_cancel": "🚫 CANCEL",
        "btn_stars_go": "⭐ Stars up to 42% off",
        "btn_lang": "🇬🇧 Language",
        "payment_success": "✅ <b>Payment successful!</b>\n\n🔗 <b>Your access link (valid 30 seconds):</b>\n{link}\n\n⚠️ <b>Warning!</b> The link is valid only 30 seconds!\n\nThank you for your purchase! ❤️",
        "payment_success_test": "✅ <b>Access granted!</b>\n\n🔗 <b>Your access link (valid 30 seconds):</b>\n{link}\n\n⚠️ <b>Warning!</b> The link is valid only 30 seconds!\n\nThank you for using the bot! ❤️",
        "subs_list_item": "• {name} (paid ✅)",
        "main_menu_text": "🔥 VIP-ACCESS TO ALL MATERIALS\n\nHere you get everything in one place:\n— Schoolgirls, parties, students, alt girls\n— Mini Child, extreme, rapes and other tariffs\n— Daily content updates\n— Community, private chat for VIP users\n\nInstead of 700 ₽ for one tariff — 449 ₽ per month for everything.\n\nChoose your tariff below 👇"
    }
}

# ==================================================
# ТАРИФЫ (ТОЛЬКО 3!)
# ==================================================
TARIFFS = {
    "week": {
        "name_ru": "🔥 Неделя — 199 ₽",
        "name_en": "🔥 Week — 199 ₽",
        "price_rub": 199,
        "price_stars": 180,
        "duration_ru": "7 дней",
        "duration_en": "7 days",
        "category": "main",
        "desc_ru": "🔥 НЕДЕЛЯ ДОСТУПА — 199 ₽\n\nТы получаешь доступ ко всем материалам на 7 дней:\n— Школьницы\n— Вписки\n— Студентки\n— Альтушки\n— Мини Детск.\n— Жесть\n— И всё, что я добавляю ежедневно\n\nЭто выгодно, если:\n— Ты хочешь попробовать, что у меня есть.\n— Тебе нужен доступ на короткий срок.\n\nЦена: 199 ₽"
    },
    "month": {
        "name_ru": "🔥 Месяц — 449 ₽",
        "name_en": "🔥 Month — 449 ₽",
        "price_rub": 449,
        "price_stars": 400,
        "duration_ru": "30 дней",
        "duration_en": "30 days",
        "category": "main",
        "desc_ru": "🔥 МЕСЯЦ ДОСТУПА — 449 ₽\n\nТы получаешь доступ ко ВСЕМ материалам на 30 дней:\n— Школьницы\n— Вписки\n— Студентки\n— Альтушки\n— Мини Детск.\n— Жесть\n— И всё, что я добавляю ежедневно\n\nПочему это выгодно:\n— Вместо 700 ₽ за один тариф — 449 ₽ за всё.\n— Контент обновляется каждый день.\n— Ты экономишь больше 50% по сравнению с покупкой отдельных тарифов.\n\nЦена: 449 ₽"
    },
    "year": {
        "name_ru": "🔥 Год — 1 499 ₽",
        "name_en": "🔥 Year — 1 499 ₽",
        "price_rub": 1499,
        "price_stars": 1350,
        "duration_ru": "365 дней",
        "duration_en": "365 days",
        "category": "main",
        "desc_ru": "🔥 ГОД ДОСТУПА — 1 499 ₽\n\nТы получаешь доступ ко ВСЕМ материалам на 365 дней:\n— Школьницы\n— Вписки\n— Студентки\n— Альтушки\n— Мини Детск.\n— Жесть\n— И всё, что я добавляю ежедневно\n\nПочему это выгодно:\n— Всего 1 499 ₽ за целый год — это 125 ₽ в месяц!\n— Это в 3 раза дешевле, чем покупать месяц за 449 ₽.\n— Ты получаешь доступ ко всем моим материалам без ограничений.\n\nЦена: 1 499 ₽"
    }
}

# --- ТЕСТОВЫЙ ТАРИФ ---
TEST_TARIFF = {
    "name_ru": "🧪 ТЕСТОВЫЙ тариф (Бесплатно)",
    "name_en": "🧪 TEST tariff (Free)",
    "price_rub": 0,
    "price_stars": 0,
    "duration_ru": "Тестовый",
    "duration_en": "Test",
    "desc_ru": "🧪 Это тестовый тариф. Он полностью БЕСПЛАТНЫЙ!\n\nПросто выберите его и получите ссылку для тестирования."
}

# --- ПРОМОКОДЫ ---
PROMO_CODES = {
    "VIP10": 10,
    "SUPER25": 25,
    "HOMAKE40": 40,
    "BANK50": 50,
    "LOLIPOP80": 80,
    "newpopolnenie": 60
}

# --- ИНИЦИАЛИЗАЦИЯ ---
storage = MemoryStorage()
session = AiohttpSession()
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML), session=session)
dp = Dispatcher(storage=storage)

# --- FSM STATES ---
class PromoStates(StatesGroup):
    waiting_for_promo = State()

class MailingStates(StatesGroup):
    waiting_for_content = State()
    waiting_for_mail_type = State()

# --- ФУНКЦИИ ---
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
                error_text = await response.text()
                logging.error(f"Ошибка RollyPay: {response.status} - {error_text}")
                return None

async def get_lang(state: FSMContext):
    data = await state.get_data()
    return data.get("lang", "ru")

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

# --- КЛАВИАТУРЫ ---
def get_main_keyboard(lang):
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=LANG[lang]["btn_prices"]), KeyboardButton(text=LANG[lang]["btn_subs"])]
    ], resize_keyboard=True)

def get_tariff_keyboard(lang):
    """Все 3 тарифа в одном меню"""
    buttons = []
    for key, data in TARIFFS.items():
        name = data['name_ru'] if lang == 'ru' else data['name_en']
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"tariff_{key}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_test_tariff_keyboard(lang):
    buttons = [
        [InlineKeyboardButton(text="💳 ОПЛАТИТЬ", callback_data="pay_test")],
        [InlineKeyboardButton(text="👈 НАЗАД", callback_data="back_to_prices")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

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
        [InlineKeyboardButton(text="📨 Рассылка", callback_data="admin_mailing")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")]
    ])

# --- ХЭНДЛЕРЫ ---
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    first_name = message.from_user.first_name or "Пользователь"
    username = message.from_user.username
    
    add_user(user_id, first_name, username)
    
    lang = await get_lang(state)
    
    # НОВОЕ ПРИВЕТСТВИЕ
    welcome_text = LANG[lang]["main_menu_text"]
    
    await message.answer(welcome_text, reply_markup=get_tariff_keyboard(lang))

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Только для админов!")
        return
    
    user_count = get_user_count()
    
    text = f"""⚙️ <b>Админ-панель</b>

👥 Всего пользователей: {user_count}

Выберите действие:"""
    
    await message.answer(text, reply_markup=get_admin_keyboard())

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
        "⚠️ <b>Внимание:</b> Рассылка пойдёт всем пользователям, которые "
        "когда-либо взаимодействовали с ботом.\n\n"
        "🔄 Чтобы отменить, отправь /cancel"
    )
    await state.set_state(MailingStates.waiting_for_content)

@dp.message(Command("mail"))
async def cmd_mail(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Только для админов!")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏷️ Скидка 25%", callback_data="mail_promo25")],
        [InlineKeyboardButton(text="🏷️ Скидка 40%", callback_data="mail_promo40")],
        [InlineKeyboardButton(text="🏷️ Скидка 60%", callback_data="mail_promo60")],
        [InlineKeyboardButton(text="📨 Обычная рассылка", callback_data="mail_normal")]
    ])
    
    await message.answer(
        "📨 <b>Выбери тип рассылки:</b>\n\n"
        "• Скидка 25% — пользователь получит скидку 25%\n"
        "• Скидка 40% — пользователь получит скидку 40%\n"
        "• Скидка 60% — пользователь получит скидку 60%\n"
        "• Обычная — просто текст",
        reply_markup=keyboard
    )
    await state.set_state(MailingStates.waiting_for_mail_type)

@dp.callback_query(MailingStates.waiting_for_mail_type)
async def process_mail_type(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Только для админов!", show_alert=True)
        return
    
    mail_type = callback.data.replace("mail_", "")
    await state.update_data(mail_type=mail_type)
    
    await callback.message.delete()
    await callback.message.answer(
        "📝 <b>Отправь текст сообщения</b>\n\n"
        "Этот текст увидят все пользователи. Ты можешь отправить:\n"
        "• Текст\n"
        "• Фото\n"
        "• Видео\n"
        "• GIF\n\n"
        "🔄 Чтобы отменить, отправь /cancel"
    )
    await state.set_state(MailingStates.waiting_for_content)
    await callback.answer()

@dp.message(MailingStates.waiting_for_content)
async def process_mailing_content(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Только для админов!")
        return
    
    data = await state.get_data()
    mail_type = data.get("mail_type", "normal")
    
    await message.answer("⏳ Начинаю рассылку...")
    
    users = get_all_users()
    
    if not users:
        await message.answer("❌ Нет пользователей для рассылки!")
        await state.clear()
        return
    
    if mail_type == "promo25":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏷️ АКТИВИРОВАТЬ СКИДКУ", callback_data="mail_discount_25")]
        ])
        footer = "\n\n🔥 Нажми кнопку, чтобы активировать скидку 25% на любой тариф!"
    elif mail_type == "promo40":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏷️ АКТИВИРОВАТЬ СКИДКУ", callback_data="mail_discount_40")]
        ])
        footer = "\n\n🔥 Нажми кнопку, чтобы активировать скидку 40% на любой тариф!"
    elif mail_type == "promo60":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏷️ АКТИВИРОВАТЬ СКИДКУ", callback_data="mail_discount_60")]
        ])
        footer = "\n\n🔥 Нажми кнопку, чтобы активировать скидку 60% на любой тариф!"
    else:
        keyboard = None
        footer = ""
    
    success = 0
    failed = 0
    
    for user_id in users:
        try:
            if message.text:
                text = message.text + footer
                await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=keyboard)
            elif message.photo:
                await bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption + footer, reply_markup=keyboard)
            elif message.video:
                await bot.send_video(user_id, message.video.file_id, caption=message.caption + footer, reply_markup=keyboard)
            elif message.animation:
                await bot.send_animation(user_id, message.animation.file_id, caption=message.caption + footer, reply_markup=keyboard)
            elif message.document:
                await bot.send_document(user_id, message.document.file_id, caption=message.caption + footer, reply_markup=keyboard)
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
        f"👥 Всего пользователей: {len(users)}\n"
        f"📌 Тип: {mail_type}"
    )
    await state.clear()

@dp.message(Command("cancel"))
async def cancel_mailing(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("✅ Рассылка отменена.")

@dp.callback_query(F.data == "mail_discount_25")
async def mail_discount_25(callback: CallbackQuery):
    user_id = callback.from_user.id
    add_user_discount(user_id, "SUPER25", 25)
    
    await callback.message.edit_text(
        "🏷️ <b>Скидка 25% активирована!</b>\n\n"
        "Ты получил скидку 25% на любой тариф 🎉\n\n"
        "Скидка будет применена автоматически при покупке."
    )
    await callback.answer("✅ Скидка 25% активирована!", show_alert=True)

@dp.callback_query(F.data == "mail_discount_40")
async def mail_discount_40(callback: CallbackQuery):
    user_id = callback.from_user.id
    add_user_discount(user_id, "HOMAKE40", 40)
    
    await callback.message.edit_text(
        "🏷️ <b>Скидка 40% активирована!</b>\n\n"
        "Ты получил скидку 40% на любой тариф 🎉\n\n"
        "Скидка будет применена автоматически при покупке."
    )
    await callback.answer("✅ Скидка 40% активирована!", show_alert=True)

@dp.callback_query(F.data == "mail_discount_60")
async def mail_discount_60(callback: CallbackQuery):
    user_id = callback.from_user.id
    add_user_discount(user_id, "newpopolnenie", 60)
    
    await callback.message.edit_text(
        "🏷️ <b>Скидка 60% активирована!</b>\n\n"
        "Ты получил скидку 60% на любой тариф 🎉\n\n"
        "Скидка будет применена автоматически при покупке."
    )
    await callback.answer("✅ Скидка 60% активирована!", show_alert=True)

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Только для админов!", show_alert=True)
        return
    
    user_count = get_user_count()
    
    await callback.message.edit_text(
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: {user_count}",
        reply_markup=get_admin_keyboard()
    )
    await callback.answer()

@dp.message(Command("test67"))
async def cmd_test67(message: Message, state: FSMContext):
    lang = await get_lang(state)
    user_id = message.from_user.id
    
    is_paid = is_tariff_paid(user_id, "test")
    
    if is_paid:
        text = f"""📋 <b>{TEST_TARIFF['name_ru'] if lang == 'ru' else TEST_TARIFF['name_en']}</b>

💰 Цена: БЕСПЛАТНО 🎉
Срок доступа: {TEST_TARIFF['duration_ru'] if lang == 'ru' else TEST_TARIFF['duration_en']}

{TEST_TARIFF['desc_ru'] if lang == 'ru' else TEST_TARIFF['desc_en']}

✅ <b>ТАРИФ ОПЛАЧЕН</b>

🔑 Для получения ссылки напишите в поддержку @Nastia_sup"""
        await message.answer(text)
        return
    
    text = f"""📋 <b>{TEST_TARIFF['name_ru'] if lang == 'ru' else TEST_TARIFF['name_en']}</b>

💰 Цена: БЕСПЛАТНО 🎉
Срок доступа: {TEST_TARIFF['duration_ru'] if lang == 'ru' else TEST_TARIFF['duration_en']}

{TEST_TARIFF['desc_ru'] if lang == 'ru' else TEST_TARIFF['desc_en']}"""
    
    await message.answer(text, reply_markup=get_test_tariff_keyboard(lang))

@dp.callback_query(F.data == "pay_test")
async def pay_test_tariff(callback: CallbackQuery, state: FSMContext):
    lang = await get_lang(state)
    user_id = callback.from_user.id
    
    if is_tariff_paid(user_id, "test"):
        await callback.answer("❌ Вы уже активировали тестовый тариф!", show_alert=True)
        return
    
    await callback.message.delete()
    await save_payment_and_send_link(callback.message, "test", lang, user_id)
    await callback.answer("✅ Доступ открыт!")

@dp.message(Command("reset"))
async def cmd_reset(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды!")
        return
    await message.answer("🔄 Выполняю сброс...")
    await message.answer("✅ Бот сброшен!")

@dp.message(Command("language"))
async def cmd_language(message: Message, state: FSMContext):
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

@dp.message(F.text.in_([LANG["ru"]["btn_prices"], LANG["en"]["btn_prices"]]))
async def show_prices(message: Message, state: FSMContext):
    lang = await get_lang(state)
    await message.answer(LANG[lang]["main_menu_text"], reply_markup=get_tariff_keyboard(lang))

@dp.message(F.text.in_([LANG["ru"]["btn_subs"], LANG["en"]["btn_subs"]]))
async def show_subscriptions(message: Message, state: FSMContext):
    lang = await get_lang(state)
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

@dp.callback_query(F.data == "back_to_prices")
async def back_to_prices(callback: CallbackQuery, state: FSMContext):
    lang = await get_lang(state)
    await callback.answer()
    await callback.message.edit_text(LANG[lang]["main_menu_text"], reply_markup=get_tariff_keyboard(lang))

@dp.callback_query(F.data.startswith("tariff_"))
async def show_tariff_details(callback: CallbackQuery, state: FSMContext):
    tariff_key = callback.data.replace("tariff_", "")
    
    if tariff_key not in TARIFFS:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    tariff = TARIFFS[tariff_key]
    lang = await get_lang(state)
    data = await state.get_data()
    discount = data.get("discount", 0)
    user_id = callback.from_user.id
    
    name = tariff['name_ru'] if lang == "ru" else tariff['name_en']
    duration = tariff['duration_ru'] if lang == "ru" else tariff['duration_en']
    desc = tariff['desc_ru'] if lang == "ru" else tariff['desc_en']
    
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

# --- ОСТАЛЬНЫЕ ОБРАБОТЧИКИ ---
@dp.callback_query(F.data.startswith("enter_promo_"))
async def enter_promo(callback: CallbackQuery, state: FSMContext):
    tariff_key = callback.data.replace("enter_promo_", "")
    
    if tariff_key not in TARIFFS:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    lang = await get_lang(state)
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
    lang = await get_lang(state)
    
    if not tariff_key or tariff_key not in TARIFFS:
        await state.clear()
        await message.answer("❌ Ошибка. Попробуйте выбрать тариф заново.")
        return

    if promo_code in PROMO_CODES:
        discount = PROMO_CODES[promo_code]
        await state.update_data(discount=discount)
        
        tariff = TARIFFS[tariff_key]
        name = tariff['name_ru'] if lang == "ru" else tariff['name_en']
        new_rub = int(tariff['price_rub'] * (1 - discount / 100))
        
        text = LANG[lang]["promo_success"].format(code=promo_code, discount=discount, name=name, old_rub=tariff['price_rub'], new_rub=new_rub)
        await message.answer(text, reply_markup=get_payment_method_keyboard(tariff_key, discount, lang))
    else:
        await message.answer(LANG[lang]["promo_fail"])

@dp.callback_query(F.data.startswith("cancel_promo_"))
async def cancel_promo(callback: CallbackQuery, state: FSMContext):
    tariff_key = callback.data.replace("cancel_promo_", "")
    
    if tariff_key not in TARIFFS:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    lang = await get_lang(state)
    await state.clear()
    await callback.message.delete()
    tariff = TARIFFS[tariff_key]
    data = await state.get_data()
    discount = data.get("discount", 0)
    user_id = callback.from_user.id
    
    name = tariff['name_ru'] if lang == "ru" else tariff['name_en']
    duration = tariff['duration_ru'] if lang == "ru" else tariff['duration_en']
    desc = tariff['desc_ru'] if lang == "ru" else tariff['desc_en']

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
        lang = await get_lang(state)
        user_id = callback.from_user.id
        await callback.message.delete()
        await save_payment_and_send_link(callback.message, tariff_key, lang, user_id)
        await callback.answer("✅ Доступ открыт!")
        return
    
    lang = await get_lang(state)
    data = await state.get_data()
    discount = data.get("discount", 0)
    
    name = tariff['name_ru'] if lang == "ru" else tariff['name_en']
    duration = tariff['duration_ru'] if lang == "ru" else tariff['duration_en']
    
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
        lang = await get_lang(state)
        user_id = callback.from_user.id
        await callback.message.delete()
        await save_payment_and_send_link(callback.message, tariff_key, lang, user_id)
        await callback.answer("✅ Доступ открыт!")
        return
    
    lang = await get_lang(state)
    data = await state.get_data()
    discount = data.get("discount", 0)
    
    final_price = int(tariff['price_rub'] * (1 - discount / 100))
    user_id = callback.from_user.id
    
    await state.update_data(pending_tariff=tariff_key)
    
    payment_url = await create_rollypay_payment(final_price, user_id, tariff_key, tariff['name_ru'])
    
    if payment_url:
        name = tariff['name_ru'] if lang == "ru" else tariff['name_en']
        duration = tariff['duration_ru'] if lang == "ru" else tariff['duration_en']
        
        if discount > 0:
            price_line = f"💰 Цена: <s>{tariff['price_rub']} RUB</s> → {final_price} RUB (-{discount}%)\n"
        else:
            price_line = f"💰 Цена: {final_price} RUB\n"
        
        text = LANG[lang]["pay_rub"].format(name=name, duration=duration, price_line=price_line, final=final_price, project=PROJECT_NAME)
        await callback.message.edit_text(text, reply_markup=get_payment_action_keyboard(payment_url, tariff_key, lang))
    else:
        await callback.answer("❌ Ошибка создания платежа. Попробуйте позже или выберите другой способ оплаты.", show_alert=True)
        
@dp.callback_query(F.data.startswith("payment_success_"))
async def payment_success(callback: CallbackQuery, state: FSMContext):
    tariff_key = callback.data.replace("payment_success_", "")
    lang = await get_lang(state)
    user_id = callback.from_user.id
    
    await callback.message.delete()
    await save_payment_and_send_link(callback.message, tariff_key, lang, user_id)
    await callback.answer("✅ Оплата успешно завершена!")

@dp.callback_query(F.data.startswith("pay_stars_"))
async def process_stars_payment(callback: CallbackQuery, state: FSMContext):
    tariff_key = callback.data.replace("pay_stars_", "")
    
    if tariff_key not in TARIFFS:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    
    tariff = TARIFFS[tariff_key]
    
    if tariff['price_rub'] == 0:
        lang = await get_lang(state)
        user_id = callback.from_user.id
        await callback.message.delete()
        await save_payment_and_send_link(callback.message, tariff_key, lang, user_id)
        await callback.answer("✅ Доступ открыт!")
        return
    
    lang = await get_lang(state)
    data = await state.get_data()
    discount = data.get("discount", 0)
    name = tariff['name_ru'] if lang == "ru" else tariff['name_en']
    duration = tariff['duration_ru'] if lang == "ru" else tariff['duration_en']
    
    final_price = int(tariff['price_stars'] * (1 - discount / 100))
    demo_stars_url = f"https://t.me/TweetlyStarsBot?start=demo_stars_{tariff_key}"
    
    if discount > 0:
        price_line = f"💰 Цена: <s>{tariff['price_stars']} STARS</s> → {final_price} STARS (-{discount}%)\n"
    else:
        price_line = f"💰 Цена: {final_price} STARS\n"
    
    support = SUPPORT_CONTACT_RU if lang == "ru" else SUPPORT_CONTACT_EN
    text = LANG[lang]["pay_stars"].format(name=name, duration=duration, price_line=price_line, final=final_price, project=PROJECT_NAME, support=support)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=LANG[lang]["btn_stars_go"], url=demo_stars_url)],
        [InlineKeyboardButton(text=LANG[lang]["btn_back"], callback_data=f"choose_pay_{tariff_key}")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("refresh_link_"))
async def refresh_link(callback: CallbackQuery, state: FSMContext):
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
        await callback.answer("❌ Ошибка создания новой ссылки. Попробуйте позже.", show_alert=True)

# ==================================================
# ЗАПУСК
# ==================================================
async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("=" * 60)
    print("🚀 БОТ ЗАПУЩЕН!")
    print("📦 База данных: Supabase + SQLite")
    print("👥 Пользователи сохраняются в Supabase")
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
