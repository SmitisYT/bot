import telebot
import random
import string
import time
import requests
import re
import traceback
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from flask import Flask, request
import threading
import logging
import json

# Try mysql-connector-python, fallback to MySQLdb
try:
    import mysql.connector
    import mysql.connector.pooling
    MYSQL_LIB = "mysql.connector"
except ImportError:
    import MySQLdb
    import MySQLdb.cursors
    MYSQL_LIB = "MySQLdb"

# Bot configuration
TOKEN = "7717022740:AAHiaTyRrtJYFSkDYYosP04utC3RJXWI6Fs"
WEBHOOK_URL = "https://botchathook.onrender.com/bot"  # Replace with your Render app URL
KEEP_ALIVE_URL = "https://botchathook.onrender.com"  # Replace with your Render app URL
VERIFY_CODE_URL = "https://botchathook.onrender.com/verify_code"  # New endpoint for code verification
MYSQL_CONFIG = {
    'host': '141.8.193.104',
    'user': 'a0903281_botsmit',
    'password': 'cVq786qq',
    'database': 'a0903281_botsmit',
    'port': 3306,
    'pool_name': 'bot_pool' if MYSQL_LIB == "mysql.connector" else None,
    'pool_size': 5 if MYSQL_LIB == "mysql.connector" else None
}
ADMIN_IDS = [313759708, 882651970, 875909419, 1516256568]
admin_mode = {}
active_tickets = {}
admin_active_ticket = {}

# Initialize bot and Flask app
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)
telebot.logger.setLevel(logging.INFO)

# Database connection pool
db_pool = None

def get_global_ip():
    try:
        response = requests.get('https://api.ipify.org?format=json', timeout=5)
        response.raise_for_status()
        return response.json()['ip']
    except requests.RequestException as e:
        return f"Failed to get global IP: {e}"

def keep_alive_pinger():
    while True:
        try:
            response = requests.get(KEEP_ALIVE_URL, timeout=5)
            print(f"Keep-alive ping to {KEEP_ALIVE_URL}: status_code={response.status_code}, response={response.text}")
        except requests.RequestException as e:
            print(f"Keep-alive ping failed to {KEEP_ALIVE_URL}: {e}")
        time.sleep(600)  # Ping every 10 minutes

def get_mysql_connection():
    global db_pool
    if MYSQL_LIB == "mysql.connector":
        if db_pool is None:
            try:
                db_pool = mysql.connector.pooling.MySQLConnectionPool(**MYSQL_CONFIG)
                print("MySQL connection pool initialized")
            except mysql.connector.Error as err:
                print(f"Error initializing MySQL connection pool: {err}")
                raise
        for attempt in range(3):
            try:
                conn = db_pool.get_connection()
                print(f"Retrieved connection from pool: attempt {attempt + 1}")
                cursor = conn.cursor()
                cursor.execute("SELECT DATABASE()")
                db_name = cursor.fetchone()[0]
                print(f"Connected to database: {db_name}")
                cursor.close()
                return conn
            except mysql.connector.Error as err:
                print(f"Failed to get connection from pool, attempt {attempt + 1}: {err}")
                if attempt < 2:
                    time.sleep(2)
                else:
                    raise
        raise mysql.connector.Error("Failed to get connection after 3 attempts")
    else:  # MySQLdb
        try:
            conn = MySQLdb.connect(
                host=MYSQL_CONFIG['host'],
                user=MYSQL_CONFIG['user'],
                passwd=MYSQL_CONFIG['password'],
                db=MYSQL_CONFIG['database'],
                port=MYSQL_CONFIG['port']
            )
            print("MySQLdb connection established")
            cursor = conn.cursor()
            cursor.execute("SELECT DATABASE()")
            db_name = cursor.fetchone()[0]
            print(f"Connected to database: {db_name}")
            cursor.close()
            return conn
        except MySQLdb.Error as err:
            print(f"Failed to connect with MySQLdb: {err}")
            raise

def init_mysql_db():
    try:
        conn = get_mysql_connection()
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS telegram_users
                         (telegram_id BIGINT PRIMARY KEY, minecraft_username VARCHAR(255), telegram_username VARCHAR(255))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS pending_codes
                         (code VARCHAR(6) PRIMARY KEY, telegram_id BIGINT, username VARCHAR(255), created_at DATETIME)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS tickets
                         (ticket_id VARCHAR(10) PRIMARY KEY, telegram_id BIGINT, title VARCHAR(255), status VARCHAR(20))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS ticket_messages
                         (id BIGINT AUTO_INCREMENT PRIMARY KEY, ticket_id VARCHAR(10), telegram_id BIGINT, 
                         message_text TEXT, photo_id VARCHAR(255), timestamp DATETIME)''')
        cursor.execute("DESCRIBE telegram_users")
        columns = [row[0] for row in cursor.fetchall()]
        print(f"telegram_users columns: {columns}")
        if 'telegram_username' not in columns:
            print("telegram_username column missing, attempting to add")
            cursor.execute("ALTER TABLE telegram_users ADD COLUMN telegram_username VARCHAR(255)")
            conn.commit()
            print("Added telegram_username column")
        # Add created_at column to pending_codes if not exists
        cursor.execute("DESCRIBE pending_codes")
        columns = [row[0] for row in cursor.fetchall()]
        print(f"pending_codes columns: {columns}")
        if 'created_at' not in columns:
            print("created_at column missing in pending_codes, attempting to add")
            cursor.execute("ALTER TABLE pending_codes ADD COLUMN created_at DATETIME")
            conn.commit()
            print("Added created_at column to pending_codes")
        conn.commit()
        cursor.close()
        conn.close()
        print("MySQL database initialized")
    except (mysql.connector.Error if MYSQL_LIB == "mysql.connector" else MySQLdb.Error) as err:
        print(f"Error initializing MySQL database: {err}\n{traceback.format_exc()}")
        raise

def generate_code():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=6))

def generate_ticket_id():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=10))

def create_main_menu(telegram_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("Поддержка"), KeyboardButton("Личный кабинет"))
    if telegram_id in ADMIN_IDS and admin_mode.get(telegram_id, False):
        print(f"Adding 'Админ панель' for telegram_id={telegram_id}, admin_mode={admin_mode.get(telegram_id)}")
        markup.add(KeyboardButton("Админ панель"))
    else:
        print(f"Not adding 'Админ панель' for telegram_id={telegram_id}, admin_mode={admin_mode.get(telegram_id)}, is_admin={telegram_id in ADMIN_IDS}")
    return markup

def create_support_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("Получить ссылку на РП"))
    markup.add(KeyboardButton("Не удалось загрузить ресурс пак"))
    markup.add(KeyboardButton("Обучение"))
    markup.add(KeyboardButton("Связаться со специалистом"))
    markup.add(KeyboardButton("Назад"))
    return markup

def create_training_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("Как зайти"))
    markup.add(KeyboardButton("Как выбрать класс"))
    markup.add(KeyboardButton("Как прокачаться"))
    markup.add(KeyboardButton("Как выбрать скин"))
    markup.add(KeyboardButton("Обзор дракона пустоты"))
    markup.add(KeyboardButton("Обзор громовержца"))
    markup.add(KeyboardButton("Обзор инфернала"))
    markup.add(KeyboardButton("Обзор йотуна"))
    markup.add(KeyboardButton("Обзор вампира"))
    markup.add(KeyboardButton("Назад"))
    return markup

def create_admin_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("Оказать поддержку"))
    markup.add(KeyboardButton("Назад"))
    return markup

def create_admin_support_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    try:
        conn = get_mysql_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ticket_id, title FROM tickets WHERE status = 'open'")
        tickets = cursor.fetchall()
        cursor.close()
        conn.close()
        print(f"Fetched {len(tickets)} open tickets: {[(t[0], t[1]) for t in tickets]}")
        if not tickets:
            return None, "Нет открытых тем на данный момент."
        for ticket_id, title in tickets:
            markup.add(KeyboardButton(f"{title} ({ticket_id})"))
        markup.add(KeyboardButton("Назад"))
        return markup, "Открытые темы:"
    except (mysql.connector.Error if MYSQL_LIB == "mysql.connector" else MySQLdb.Error) as err:
        print(f"Error fetching tickets: {err}")
        return None, f"Ошибка базы данных при получении тем: {err}"

def create_ticket_view_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("Закрыть тему"))
    markup.add(KeyboardButton("Выйти из темы"))
    return markup

def create_back_to_support_menu(telegram_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    try:
        conn = get_mysql_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ticket_id FROM tickets WHERE telegram_id = %s AND status = 'open'", (telegram_id,))
        ticket = cursor.fetchone()
        cursor.close()
        conn.close()
        if ticket:
            print(f"Adding 'Закрыть тему' to back_to_support_menu for telegram_id={telegram_id}, ticket_id={ticket[0]}")
            markup.add(KeyboardButton("Закрыть тему"))
        else:
            print(f"No active ticket in create_back_to_support_menu for telegram_id={telegram_id}")
    except (mysql.connector.Error if MYSQL_LIB == "mysql.connector" else MySQLdb.Error) as err:
        print(f"Error checking active ticket in create_back_to_support_menu: {err}")
    markup.add(KeyboardButton("Назад"))
    return markup

def create_close_ticket_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    respons = "Закрыть тему"
    markup.add(KeyboardButton(respons))
    markup.add(KeyboardButton("Назад"))
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    if message.chat.type != 'private':
        print(f"Skipping /start: chat_type={message.chat.type} is not private")
        return
    telegram_id = message.from_user.id
    print(f"Start command: telegram_id={telegram_id}, admin_mode={admin_mode.get(telegram_id)}")
    try:
        bot.reply_to(message, "Привет, Я - Бот Менеджер, могу привязать твой аккаунт к майнкрафту или помочь по другим вопросам", reply_markup=create_main_menu(telegram_id))
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in send_welcome for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")

@bot.message_handler(commands=['admin'])
def toggle_admin_mode(message):
    if message.chat.type != 'private':
        print(f"Skipping /admin: chat_type={message.chat.type} is not private")
        return
    telegram_id = message.from_user.id
    print(f"Admin command: telegram_id={telegram_id}, current admin_mode={admin_mode.get(telegram_id)}")
    try:
        if telegram_id not in ADMIN_IDS:
            bot.reply_to(message, "У вас нет доступа к этой команде.", reply_markup=create_main_menu(telegram_id))
            return
        admin_mode[telegram_id] = not admin_mode.get(telegram_id, False)
        if admin_mode[telegram_id]:
            bot.reply_to(message, "Админ режим включен.", reply_markup=create_admin_menu())
            bot.send_message(telegram_id, "Главное меню обновлено:", reply_markup=create_main_menu(telegram_id))
        else:
            bot.reply_to(message, "Админ режим выключен.", reply_markup=create_main_menu(telegram_id))
            if telegram_id in admin_active_ticket:
                del admin_active_ticket[telegram_id]
        print(f"Admin mode updated: telegram_id={telegram_id}, admin_mode={admin_mode.get(telegram_id)}")
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in toggle_admin_mode for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")

@bot.message_handler(func=lambda message: message.text.strip().lower() == "закрыть тему" and message.chat.type == 'private' and message.from_user.id not in ADMIN_IDS)
def handle_user_close_ticket(message):
    telegram_id = message.from_user.id
    print(f"User close ticket handler: telegram_id={telegram_id}, active_tickets={active_tickets.get(telegram_id)}")
    try:
        conn = get_mysql_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ticket_id FROM tickets WHERE telegram_id = %s AND status = 'open'", (telegram_id,))
        ticket = cursor.fetchone()
        if not ticket:
            print(f"No active ticket found in database for telegram_id={telegram_id}")
            bot.reply_to(message, "У вас нет активной темы.", reply_markup=create_support_menu())
            cursor.close()
            conn.close()
            return
        ticket_id = ticket[0]
        cursor.execute("UPDATE tickets SET status = 'closed' WHERE ticket_id = %s AND telegram_id = %s", (ticket_id, telegram_id))
        if cursor.rowcount > 0:
            conn.commit()
            bot.reply_to(message, f"Ваша тема (ID: {ticket_id}) закрыта.", reply_markup=create_support_menu())
            print(f"Ticket closed: ticket_id={ticket_id}, telegram_id={telegram_id}")
            if telegram_id in active_tickets:
                del active_tickets[telegram_id]
            for admin_id, active_ticket_id in list(admin_active_ticket.items()):
                if active_ticket_id == ticket_id:
                    try:
                        bot.send_message(admin_id, f"Тема (ID: {ticket_id}) закрыта пользователем.", reply_markup=create_admin_support_menu()[0] or create_admin_menu())
                        del admin_active_ticket[admin_id]
                    except telebot.apihelper.ApiTelegramException as e:
                        print(f"Failed to notify admin_id={admin_id} of ticket closure: {e}")
        else:
            print(f"Failed to close ticket: ticket_id={ticket_id}, telegram_id={telegram_id}")
            bot.reply_to(message, "Не удалось закрыть тему.", reply_markup=create_support_menu())
        cursor.close()
        conn.close()
    except (mysql.connector.Error if MYSQL_LIB == "mysql.connector" else MySQLdb.Error) as err:
        print(f"Database error in handle_user_close_ticket: {err}\n{traceback.format_exc()}")
        bot.reply_to(message, f"Ошибка базы данных: {err}", reply_markup=create_support_menu())
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in handle_user_close_ticket for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")

@bot.message_handler(func=lambda message: message.text.strip().lower() == "закрыть тему" and message.chat.type == 'private' and message.from_user.id in ADMIN_IDS and message.from_user.id in admin_active_ticket)
def handle_admin_close_ticket(message):
    telegram_id = message.from_user.id
    ticket_id = admin_active_ticket.get(telegram_id)
    print(f"Admin close ticket: telegram_id={telegram_id}, ticket_id={ticket_id}")
    try:
        if not ticket_id:
            print(f"No active ticket for admin: telegram_id={telegram_id}")
            bot.reply_to(message, "Нет активной темы.", reply_markup=create_admin_support_menu()[0] or create_admin_menu())
            return
        conn = get_mysql_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id FROM tickets WHERE ticket_id = %s AND status = 'open'", (ticket_id,))
        user_id = cursor.fetchone()
        if user_id:
            user_id = user_id[0]
            cursor.execute("UPDATE tickets SET status = 'closed' WHERE ticket_id = %s", (ticket_id,))
            conn.commit()
            bot.reply_to(message, f"Тема (ID: {ticket_id}) закрыта.", reply_markup=create_admin_support_menu()[0] or create_admin_menu())
            if user_id in active_tickets:
                del active_tickets[user_id]
                try:
                    bot.send_message(user_id, f"Ваша тема (ID: {ticket_id}) закрыта администратором.", reply_markup=create_support_menu())
                    print(f"Notified user_id={user_id} of ticket closure: ticket_id={ticket_id}")
                except telebot.apihelper.ApiTelegramException as e:
                    print(f"Failed to notify user_id={user_id} of ticket closure: {e}")
            if telegram_id in admin_active_ticket:
                del admin_active_ticket[telegram_id]
        else:
            print(f"Ticket not found or closed: ticket_id={ticket_id}")
            bot.reply_to(message, "Тема не найдена или уже закрыта.", reply_markup=create_admin_support_menu()[0] or create_admin_menu())
        cursor.close()
        conn.close()
    except (mysql.connector.Error if MYSQL_LIB == "mysql.connector" else MySQLdb.Error) as err:
        print(f"Database error in handle_admin_close_ticket: {err}\n{traceback.format_exc()}")
        bot.reply_to(message, f"Ошибка базы данных: {err}", reply_markup=create_admin_support_menu()[0] or create_admin_menu())
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in handle_admin_close_ticket for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")

@bot.message_handler(func=lambda message: message.text == "Выйти из темы" and message.chat.type == 'private' and message.from_user.id in ADMIN_IDS and message.from_user.id in admin_active_ticket)
def handle_admin_exit_ticket(message):
    telegram_id = message.from_user.id
    ticket_id = admin_active_ticket.get(telegram_id)
    print(f"Admin exit ticket: telegram_id={telegram_id}, ticket_id={ticket_id}")
    try:
        if telegram_id in admin_active_ticket:
            del admin_active_ticket[telegram_id]
        bot.reply_to(message, "Вы вышли из темы.", reply_markup=create_admin_support_menu()[0] or create_admin_menu())
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in handle_admin_exit_ticket for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")

@bot.message_handler(func=lambda message: message.text in ["Поддержка", "Личный кабинет", "Админ панель"] and message.chat.type == 'private')
def handle_main_menu(message):
    telegram_id = message.from_user.id
    print(f"Main menu handler: telegram_id={telegram_id}, message={message.text}, admin_mode={admin_mode.get(telegram_id)}")
    try:
        if message.text == "Поддержка":
            bot.reply_to(message, "Пожалуйста, выберите вашу проблему", reply_markup=create_support_menu())
        elif message.text == "Личный кабинет":
            conn = get_mysql_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT minecraft_username FROM telegram_users WHERE telegram_id = %s", (telegram_id,))
            user = cursor.fetchone()
            cursor.close()
            conn.close()
            if user:
                username = user[0]
                markup = ReplyKeyboardMarkup(resize_keyboard=True)
                markup.add(KeyboardButton("Сбросить пароль"), KeyboardButton("Отвязать аккаунт"))
                markup.add(KeyboardButton("Назад"))
                bot.reply_to(message, f"Ваш аккаунт привязан к нику: {username}", reply_markup=markup)
            else:
                markup = ReplyKeyboardMarkup(resize_keyboard=True)
                markup.add(KeyboardButton("Привязать аккаунт"))
                markup.add(KeyboardButton("Назад"))
                bot.reply_to(message, "Ваш аккаунт не привязан. Хотите привязать?", reply_markup=markup)
        elif message.text == "Админ панель" and telegram_id in ADMIN_IDS:
            print(f"Admin panel accessed: telegram_id={telegram_id}")
            if not admin_mode.get(telegram_id, False):
                admin_mode[telegram_id] = True
                print(f"Enabled admin_mode for telegram_id={telegram_id}")
            bot.reply_to(message, "Админ меню", reply_markup=create_admin_menu())
    except (mysql.connector.Error if MYSQL_LIB == "mysql.connector" else MySQLdb.Error) as err:
        print(f"Database error in handle_main_menu: {err}\n{traceback.format_exc()}")
        bot.reply_to(message, f"Ошибка базы данных: {err}", reply_markup=create_main_menu(telegram_id))
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in handle_main_menu for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")

@bot.message_handler(func=lambda message: message.text == "Оказать поддержку" and message.chat.type == 'private' and message.from_user.id in ADMIN_IDS)
def handle_admin_support(message):
    telegram_id = message.from_user.id
    print(f"Admin support handler triggered: telegram_id={telegram_id}, message={message.text}")
    try:
        markup, response_text = create_admin_support_menu()
        bot.reply_to(message, response_text, reply_markup=markup if markup else create_admin_menu())
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in handle_admin_support for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")

@bot.message_handler(func=lambda message: re.match(r'.*\s*\([A-Za-z0-9]{10}\)\s*$', message.text) and message.chat.type == 'private' and message.from_user.id in ADMIN_IDS and admin_mode.get(message.from_user.id, False))
def handle_ticket_selection(message):
    telegram_id = message.from_user.id
    print(f"Ticket selection handler triggered: telegram_id={telegram_id}, message='{message.text}', admin_mode={admin_mode.get(telegram_id)}, admin_active_ticket={admin_active_ticket}")
    try:
        match = re.match(r'.*\s*\(([A-Za-z0-9]{10})\)\s*$', message.text)
        ticket_id = None
        if match:
            ticket_id = match.group(1)
            print(f"Regex matched ticket_id: {ticket_id}")
        else:
            print(f"Regex failed for message: '{message.text}'")
            conn = get_mysql_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT ticket_id, title FROM tickets WHERE status = 'open'")
            tickets = cursor.fetchall()
            cursor.close()
            conn.close()
            normalized_text = message.text.strip()
            for tid, title in tickets:
                expected_text = f"{title} ({tid})".strip()
                if normalized_text == expected_text:
                    ticket_id = tid
                    print(f"Fallback matched ticket_id: {ticket_id}")
                    break
        if not ticket_id:
            print(f"No ticket_id matched for message: '{message.text}'")
            bot.reply_to(message, "Ошибка: неверный формат темы.", reply_markup=create_admin_support_menu()[0] or create_admin_menu())
            return
        conn = get_mysql_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT title, telegram_id FROM tickets WHERE ticket_id = %s AND status = 'open'", (ticket_id,))
        ticket = cursor.fetchone()
        if ticket:
            title, user_id = ticket
            cursor.execute("SELECT minecraft_username, telegram_username FROM telegram_users WHERE telegram_id = %s", (user_id,))
            user = cursor.fetchone()
            minecraft_username = user[0] if user and user[0] else "Not linked"
            telegram_username = user[1] if user and user[1] else "Unknown"
            print(f"Fetched usernames for user_id={user_id}: minecraft_username={minecraft_username}, telegram_username={telegram_username}")
            bot.reply_to(message, f"Тема: {title} (ID: {ticket_id}, Minecraft: {minecraft_username}, Telegram: {telegram_username})")
            cursor.execute("SELECT message_text, photo_id FROM ticket_messages WHERE ticket_id = %s ORDER BY timestamp", (ticket_id,))
            messages = cursor.fetchall()
            if not messages:
                bot.send_message(telegram_id, "Нет сообщений в этой теме.")
            for msg_text, photo_id in messages:
                if msg_text:
                    bot.send_message(telegram_id, msg_text)
                if photo_id:
                    bot.send_photo(telegram_id, photo_id)
            admin_active_ticket[telegram_id] = ticket_id
            print(f"Set admin_active_ticket: telegram_id={telegram_id}, ticket_id={ticket_id}, user_id={user_id}, minecraft_username={minecraft_username}, telegram_username={telegram_username}, admin_active_ticket={admin_active_ticket}")
            bot.send_message(telegram_id, "Напишите сообщение для пользователя:", reply_markup=create_ticket_view_menu())
        else:
            print(f"Ticket not found or closed: ticket_id={ticket_id}")
            bot.reply_to(message, "Тема не найдена или закрыта.", reply_markup=create_admin_support_menu()[0] or create_admin_menu())
        cursor.close()
        conn.close()
    except (mysql.connector.Error if MYSQL_LIB == "mysql.connector" else MySQLdb.Error) as err:
        print(f"Database error in handle_ticket_selection: {err}\n{traceback.format_exc()}")
        bot.reply_to(message, f"Ошибка базы данных: {err}", reply_markup=create_admin_support_menu()[0] or create_admin_menu())
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in handle_ticket_selection for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")

@bot.message_handler(func=lambda message: message.text == "Назад" and message.chat.type == 'private')
def handle_back(message):
    telegram_id = message.from_user.id
    print(f"Back handler: telegram_id={telegram_id}, admin_mode={admin_mode.get(telegram_id)}")
    try:
        if telegram_id in ADMIN_IDS and admin_mode.get(telegram_id, False):
            admin_mode[telegram_id] = False
            if telegram_id in admin_active_ticket:
                del admin_active_ticket[telegram_id]
            bot.reply_to(message, "Админ режим выключен.", reply_markup=create_main_menu(telegram_id))
        else:
            bot.reply_to(message, "Вернулись в главное меню.", reply_markup=create_main_menu(telegram_id))
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in handle_back for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")

@bot.message_handler(func=lambda message: message.text in ["Получить ссылку на РП", "Не удалось загрузить ресурс пак", "Обучение", "Связаться со специалистом"] and message.chat.type == 'private')
def handle_support_menu(message):
    telegram_id = message.from_user.id
    print(f"Support menu handler: telegram_id={telegram_id}, message={message.text}, admin_mode={admin_mode.get(telegram_id)}")
    try:
        if message.text == "Получить ссылку на РП":
            bot.reply_to(message, "Не нужно его распаковывать, просто переместите архив.zip в .minecraft/resourcepacks и включите его в игре\n\nhttps://xn--80aabizhtkd.xn--p1ai/pack.zip", reply_markup=create_back_to_support_menu(telegram_id))
        elif message.text == "Не удалось загрузить ресурс пак":
            bot.reply_to(message, "Для начала, с помощью программы Driver.Booster.Pro.12key.7z\n"
                                "https://drive.google.com/file/d/1b5OGlWiMHnZNw_veiqy7sNsKLnbimrTR/view?usp=sharing\n"
                                "обновите драйвера; для этого: скачиваете соответственно прикрепленный архив, по паролю \"kichkas.biz\" распаковываете его в любое удобное место, открываете саму программу с разрешением .exe и нажимаете запустить проверку.\n"
                                "После завершения обновления скачиваете прикрепленный архив AshampooUnInstaller.rar,\n"
                                "https://drive.google.com/file/d/1AOFd_BAtx4kO6ACwJWbDPT5YD7-Jwt4x/view?usp=sharing\n"
                                "открываете его и устанавливаете программу оттуда; с её помощью нужно найти и удалить tlauncher у устройства, так же установив галочку на очистку реестра, нужно так же ею удалить java.\n"
                                "Затем скачиваете java по ссылке: https://www.azul.com/core-post-download/?endpoint=zulu&uuid=61d4f2c3-b74a-419a-98a0-cf381411590c.\n"
                                "После всех этих шагов, необходимо скачать либо Legacy launcher по ссылке https://llaun.ch/ru, либо Xlauncher по ссылке https://xmcl.app/ru/.",
                                reply_markup=create_back_to_support_menu(telegram_id))
        elif message.text == "Обучение":
            bot.reply_to(message, "Выберите тему обучения:", reply_markup=create_training_menu())
        elif message.text == "Связаться со специалистом":
            conn = get_mysql_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT ticket_id FROM tickets WHERE telegram_id = %s AND status = 'open'", (telegram_id,))
            ticket = cursor.fetchone()
            if ticket:
                bot.reply_to(message, "У вас уже есть открытая тема. Пожалуйста, дождитесь её завершения или закройте текущую тему.", reply_markup=create_close_ticket_menu())
            else:
                bot.reply_to(message, "Введите краткий заголовок вашей проблемы:")
                bot.register_next_step_handler(message, process_ticket_title)
            cursor.close()
            conn.close()
    except (mysql.connector.Error if MYSQL_LIB == "mysql.connector" else MySQLdb.Error) as err:
        print(f"Database error in handle_support_menu: {err}\n{traceback.format_exc()}")
        bot.reply_to(message, f"Ошибка базы данных: {err}", reply_markup=create_support_menu())
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in handle_support_menu for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")

@bot.message_handler(func=lambda message: message.text in ["Как зайти", "Как выбрать класс", "Как прокачаться", "Как выбрать скин", 
                                                         "Обзор дракона пустоты", "Обзор громовержца", "Обзор инфернала", 
                                                         "Обзор йотуна", "Обзор вампира"] and message.chat.type == 'private')
def handle_training_menu(message):
    telegram_id = message.from_user.id
    print(f"Training menu handler: telegram_id={telegram_id}, message={message.text}")
    try:
        if message.text == "Как зайти":
            bot.reply_to(message, "https://youtu.be/nLFxcW4ydIw?si=IRKHtJ6ZSSAFazig", reply_markup=create_back_to_support_menu(telegram_id))
        elif message.text == "Как выбрать класс":
            bot.reply_to(message, "https://youtu.be/nVFmHgCrU4o?si=j9BrJqCs1sJOxgr8", reply_markup=create_back_to_support_menu(telegram_id))
        elif message.text == "Как прокачаться":
            bot.reply_to(message, "https://youtu.be/2PmsxuHYVWo?si=RVWuE1XkHIWte9d3\nhttps://youtu.be/T-adhrwIm60?si=mGUua_GYAwUlG5oS", reply_markup=create_back_to_support_menu(telegram_id))
        elif message.text == "Как выбрать скин":
            bot.reply_to(message, "https://youtu.be/0MPDCHXr74E?si=duwIOHH0lzaozJX7", reply_markup=create_back_to_support_menu(telegram_id))
        elif message.text == "Обзор дракона пустоты":
            bot.reply_to(message, "https://www.youtube.com/shorts/WrHP3u4TAmE", reply_markup=create_back_to_support_menu(telegram_id))
        elif message.text == "Обзор громовержца":
            bot.reply_to(message, "https://www.youtube.com/shorts/9U7EA5_HGmI", reply_markup=create_back_to_support_menu(telegram_id))
        elif message.text == "Обзор инфернала":
            bot.reply_to(message, "https://www.youtube.com/shorts/n2VdPnuuf0I", reply_markup=create_back_to_support_menu(telegram_id))
        elif message.text == "Обзор йотуна":
            bot.reply_to(message, "https://www.youtube.com/shorts/WVDijyBcoZY", reply_markup=create_back_to_support_menu(telegram_id))
        elif message.text == "Обзор вампира":
            bot.reply_to(message, "https://www.youtube.com/shorts/7YC9r85-wFI", reply_markup=create_back_to_support_menu(telegram_id))
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in handle_training_menu for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")

@bot.message_handler(func=lambda message: message.text == "Привязать аккаунт" and message.chat.type == 'private')
def start_linking(message):
    telegram_id = message.from_user.id
    print(f"Start linking handler: telegram_id={telegram_id}")
    try:
        # Check if already linked
        conn = get_mysql_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT minecraft_username FROM telegram_users WHERE telegram_id = %s", (telegram_id,))
        existing_user = cursor.fetchone()
        cursor.close()
        conn.close()
        if existing_user:
            bot.reply_to(message, f"Ваш аккаунт уже привязан к нику: {existing_user[0]}. Хотите отвязать его?", reply_markup=create_main_menu(telegram_id))
            return
        bot.reply_to(message, "Введите ваш ник в Minecraft (только английские буквы и цифры, без пробелов):")
        bot.register_next_step_handler(message, process_username)
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in start_linking for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")

def process_username(message):
    if message.chat.type != 'private':
        print(f"Skipping process_username: chat_type={message.chat.type} is not private")
        return
    telegram_id = message.from_user.id
    username = message.text.strip()
    telegram_username = message.from_user.username if message.from_user.username else message.from_user.first_name if message.from_user.first_name else "Unknown"
    print(f"Process username: telegram_id={telegram_id}, username={username}, telegram_username={telegram_username}")
    try:
        if not username.isalnum():
            bot.reply_to(message, "Ник должен содержать только английские буквы и цифры. Попробуйте снова:")
            bot.register_next_step_handler(message, process_username)
            return
        code = generate_code()
        conn = get_mysql_connection()
        cursor = conn.cursor()
        # Store in pending_codes only
        cursor.execute("INSERT INTO pending_codes (code, telegram_id, username, created_at) VALUES (%s, %s, %s, NOW())",
                      (code, telegram_id, username))
        conn.commit()
        cursor.close()
        conn.close()
        bot.reply_to(message, f"Будьте на сервере и введите в чат следующую команду: /connect {code}\n"
                            "Ваш аккаунт будет привязан после ввода команды.", reply_markup=create_main_menu(telegram_id))
        print(f"Code generated: code={code}, telegram_id={telegram_id}, username={username}")
    except (mysql.connector.Error if MYSQL_LIB == "mysql.connector" else MySQLdb.Error) as err:
        print(f"Database error in process_username: {err}\n{traceback.format_exc()}")
        bot.reply_to(message, f"Ошибка при сохранении кода: {err}", reply_markup=create_main_menu(telegram_id))
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in process_username for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")

@app.route('/verify_code', methods=['POST'])
def verify_code():
    try:
        data = request.get_json()
        if not data or 'code' not in data or 'username' not in data:
            print(f"Invalid verify_code request: {data}")
            return json.dumps({"success": False, "error": "Missing code or username"}), 400
        code = data['code']
        username = data['username']
        print(f"Received verify_code request: code={code}, username={username}")
        conn = get_mysql_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id, username FROM pending_codes WHERE code = %s", (code,))
        pending = cursor.fetchone()
        if not pending:
            print(f"Code not found: code={code}")
            cursor.close()
            conn.close()
            return json.dumps({"success": False, "error": "Invalid code"}), 404
        telegram_id, stored_username = pending
        if stored_username.lower() != username.lower():
            print(f"Username mismatch: stored={stored_username}, provided={username}")
            cursor.close()
            conn.close()
            return json.dumps({"success": False, "error": "Username does not match"}), 403
        # Check if code is expired (e.g., 10 minutes)
        cursor.execute("SELECT created_at FROM pending_codes WHERE code = %s", (code,))
        created_at = cursor.fetchone()[0]
        if (time.time() - created_at.timestamp()) > 600:  # 10 minutes
            print(f"Code expired: code={code}, created_at={created_at}")
            cursor.execute("DELETE FROM pending_codes WHERE code = %s", (code,))
            conn.commit()
            cursor.close()
            conn.close()
            return json.dumps({"success": False, "error": "Code expired"}), 410
        # Verify username not already linked
        cursor.execute("SELECT telegram_id FROM telegram_users WHERE minecraft_username = %s", (username,))
        existing_user = cursor.fetchone()
        if existing_user:
            print(f"Username already linked: username={username}, existing_telegram_id={existing_user[0]}")
            cursor.execute("DELETE FROM pending_codes WHERE code = %s", (code,))
            conn.commit()
            cursor.close()
            conn.close()
            return json.dumps({"success": False, "error": "Username already linked"}), 409
        # Link account
        telegram_username = None
        try:
            user = bot.get_chat(telegram_id)
            telegram_username = user.username if user.username else user.first_name if user.first_name else "Unknown"
        except telebot.apihelper.ApiTelegramException as e:
            print(f"Failed to get telegram_username for telegram_id={telegram_id}: {e}")
            telegram_username = "Unknown"
        cursor.execute("INSERT INTO telegram_users (telegram_id, minecraft_username, telegram_username) "
                     "VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE minecraft_username = %s, telegram_username = %s",
                     (telegram_id, username, telegram_username, username, telegram_username))
        cursor.execute("DELETE FROM pending_codes WHERE code = %s", (code,))
        conn.commit()
        cursor.close()
        conn.close()
        try:
            bot.send_message(telegram_id, f"Ваш аккаунт Minecraft ({username}) успешно привязан!")
            print(f"Account linked: telegram_id={telegram_id}, username={username}, telegram_username={telegram_username}")
        except telebot.apihelper.ApiTelegramException as e:
            print(f"Failed to notify telegram_id={telegram_id} of successful linking: {e}")
        return json.dumps({"success": True, "telegram_id": telegram_id}), 200
    except (mysql.connector.Error if MYSQL_LIB == "mysql.connector" else MySQLdb.Error) as err:
        print(f"Database error in verify_code: {err}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": f"Database error: {err}"}), 500
    except Exception as e:
        print(f"Unexpected error in verify_code: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": f"Unexpected error: {e}"}), 500

@bot.message_handler(func=lambda message: message.text == "Сбросить пароль" and message.chat.type == 'private')
def reset_password(message):
    telegram_id = message.from_user.id
    print(f"Reset password handler: telegram_id={telegram_id}")
    try:
        conn = get_mysql_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT minecraft_username FROM telegram_users WHERE telegram_id = %s", (telegram_id,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        if user:
            username = user[0]
            reset_url = f"http://xn--80aabizhtkd.xn--p1ai/resetpin.php?hash=3d8b57704510329075046abbc702dc48&login={username}"
            try:
                response = requests.get(reset_url)
                if response.status_code == 200:
                    bot.reply_to(message, "Пароль успешно сброшен!", reply_markup=create_main_menu(telegram_id))
                else:
                    bot.reply_to(message, f"Ошибка при сбросе пароля: HTTP {response.status_code}", reply_markup=create_main_menu(telegram_id))
            except requests.RequestException as e:
                bot.reply_to(message, f"Ошибка при отправке запроса: {e}", reply_markup=create_main_menu(telegram_id))
        else:
            bot.reply_to(message, "Аккаунт не привязан.", reply_markup=create_main_menu(telegram_id))
    except (mysql.connector.Error if MYSQL_LIB == "mysql.connector" else MySQLdb.Error) as err:
        print(f"Database error in reset_password: {err}\n{traceback.format_exc()}")
        bot.reply_to(message, f"Ошибка базы данных: {err}", reply_markup=create_main_menu(telegram_id))
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in reset_password for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")

@bot.message_handler(func=lambda message: message.text == "Отвязать аккаунт" and message.chat.type == 'private')
def unlink_account(message):
    telegram_id = message.from_user.id
    print(f"Unlink account handler: telegram_id={telegram_id}")
    try:
        conn = get_mysql_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM telegram_users WHERE telegram_id = %s", (telegram_id,))
        if cursor.rowcount > 0:
            conn.commit()
            bot.reply_to(message, "Аккаунт успешно отвязан!", reply_markup=create_main_menu(telegram_id))
        else:
            bot.reply_to(message, "Аккаунт не привязан.", reply_markup=create_main_menu(telegram_id))
        cursor.close()
        conn.close()
    except (mysql.connector.Error if MYSQL_LIB == "mysql.connector" else MySQLdb.Error) as err:
        print(f"Database error in unlink_account: {err}\n{traceback.format_exc()}")
        bot.reply_to(message, f"Ошибка базы данных: {err}", reply_markup=create_main_menu(telegram_id))
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in unlink_account for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")

@bot.message_handler(content_types=['text', 'photo'], func=lambda message: message.from_user.id in ADMIN_IDS and message.chat.type == 'private' and message.from_user.id in admin_active_ticket and message.text not in ["Закрыть тему", "Выйти из темы", "Оказать поддержку", "Назад", "Админ панель", "Поддержка", "Личный кабинет", "Получить ссылку на РП", "Не удалось загрузить ресурс пак", "Обучение", "Связаться со специалистом", "Как зайти", "Как выбрать класс", "Как прокачаться", "Как выбрать скин", "Обзор дракона пустоты", "Обзор громовержца", "Обзор инфернала", "Обзор йотуна", "Обзор вампира"])
def handle_admin_ticket_messages(message):
    telegram_id = message.from_user.id
    print(f"Evaluating admin ticket message handler: telegram_id={telegram_id}, is_admin={telegram_id in ADMIN_IDS}, in_admin_active_ticket={telegram_id in admin_active_ticket}, chat_type={message.chat.type}, admin_active_ticket={admin_active_ticket}, message_content_type={message.content_type}, message_text={message.text}, photo={message.photo}, message_json={message.json}")
    try:
        if telegram_id not in ADMIN_IDS:
            print(f"Handler skipped: telegram_id={telegram_id} not in ADMIN_IDS")
            return
        if message.chat.type != 'private':
            print(f"Handler skipped: chat_type={message.chat.type} is not private")
            return
        if telegram_id not in admin_active_ticket:
            print(f"Handler skipped: telegram_id={telegram_id} not in admin_active_ticket")
            bot.reply_to(message, "Ошибка: нет активной темы. Выберите тему из меню поддержки.", reply_markup=create_admin_support_menu()[0] or create_admin_menu())
            return
        ticket_id = admin_active_ticket[telegram_id]
        print(f"Admin ticket message handler triggered: telegram_id={telegram_id}, ticket_id={ticket_id}, content_type={message.content_type}, message_text={message.text}, photo={message.photo}, admin_active_ticket={admin_active_ticket}, message_json={message.json}")
        conn = get_mysql_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id FROM tickets WHERE ticket_id = %s AND status = 'open'", (ticket_id,))
        user_id = cursor.fetchone()
        if user_id:
            user_id = user_id[0]
            if not isinstance(user_id, int) or user_id <= 0:
                print(f"Invalid user_id={user_id} for ticket_id={ticket_id}")
                bot.reply_to(message, "Ошибка: неверный ID пользователя для этой темы.", reply_markup=create_admin_support_menu()[0] or create_admin_menu())
                cursor.close()
                conn.close()
                return
            if user_id == telegram_id:
                print(f"Admin is ticket owner: telegram_id={telegram_id}, user_id={user_id}, ticket_id={ticket_id}")
                bot.reply_to(message, "Вы не можете отправлять сообщения в свою собственную тему. Выберите другую тему или закройте эту.", reply_markup=create_ticket_view_menu())
                cursor.close()
                conn.close()
                return
            print(f"Sending to user_id={user_id}, ticket_id={ticket_id}")
            try:
                bot_info = bot.get_me()
                bot_id = bot_info.id
                print(f"Bot info: id={bot_id}, username={bot_info.username}")
                try:
                    chat_member = bot.get_chat_member(user_id, bot_id)
                    print(f"Bot chat member status: user_id={user_id}, bot_id={bot_id}, status={chat_member.status}, can_send_messages={chat_member.can_send_messages if hasattr(chat_member, 'can_send_messages') else 'unknown'}")
                    if chat_member.status in ['left', 'kicked']:
                        print(f"Bot cannot send messages: user_id={user_id}, bot_status={chat_member.status}")
                        bot.reply_to(message, f"Ошибка: бот не может отправить сообщение пользователю (ID: {user_id}, статус: {chat_member.status}). Попросите пользователя отправить /start и разрешить сообщения.", reply_markup=create_ticket_view_menu())
                        cursor.close()
                        conn.close()
                        return
                except telebot.apihelper.ApiTelegramException as e:
                    print(f"Failed to get bot chat member status for user_id={user_id}, bot_id={bot_id}: {e}\n{traceback.format_exc()}")
                    bot.reply_to(message, f"Ошибка: не удалось проверить статус бота для пользователя (ID: {user_id}): {e}. Попросите пользователя отправить /start.", reply_markup=create_ticket_view_menu())
                    cursor.close()
                    conn.close()
                    return
                try:
                    chat = bot.get_chat(user_id)
                    print(f"User chat status: user_id={user_id}, chat_type={chat.type}, chat_active={chat.can_send_messages if hasattr(chat, 'can_send_messages') else 'unknown'}")
                except telebot.apihelper.ApiTelegramException as e:
                    print(f"Failed to get chat for user_id={user_id}: {e}\n{traceback.format_exc()}")
                    bot.reply_to(message, f"Ошибка: пользователь (ID: {user_id}) не начал чат с ботом. Попросите пользователя отправить /start.", reply_markup=create_ticket_view_menu())
                    cursor.close()
                    conn.close()
                    return
                try:
                    user_chat_member = bot.get_chat_member(user_id, user_id)
                    print(f"User chat member status: user_id={user_id}, status={user_chat_member.status}, can_send_messages={user_chat_member.can_send_messages if hasattr(user_chat_member, 'can_send_messages') else 'unknown'}")
                    if user_chat_member.status in ['left', 'kicked']:
                        print(f"User cannot receive messages: user_id={user_id}, status={user_chat_member.status}")
                        bot.reply_to(message, f"Ошибка: пользователь (ID: {user_id}) не может получать сообщения (статус: {user_chat_member.status}). Попросите пользователя отправить /start.", reply_markup=create_ticket_view_menu())
                        cursor.close()
                        conn.close()
                        return
                except telebot.apihelper.ApiTelegramException as e:
                    print(f"Failed to get user chat member status for user_id={user_id}: {e}\n{traceback.format_exc()}")
                    bot.reply_to(message, f"Ошибка: не удалось проверить статус пользователя (ID: {user_id}): {e}. Попросите пользователя отправить /start.", reply_markup=create_ticket_view_menu())
                    cursor.close()
                    conn.close()
                    return
            except telebot.apihelper.ApiTelegramException as e:
                print(f"Failed to get bot info: {e}\n{traceback.format_exc()}")
                bot.reply_to(message, f"Ошибка: не удалось проверить статус бота: {e}.", reply_markup=create_ticket_view_menu())
                cursor.close()
                conn.close()
                return
            try:
                sent_message = bot.send_message(user_id, "Тестовое сообщение от бота для проверки связи.")
                print(f"Sent test message to user_id={user_id}, message_id={sent_message.message_id}")
            except telebot.apihelper.ApiTelegramException as e:
                print(f"Test message failed to user_id={user_id}: {e}\n{traceback.format_exc()}")
                bot.reply_to(message, f"Ошибка: не удалось связаться с пользователем (ID: {user_id}): {e}. Попросите пользователя отправить /start.", reply_markup=create_ticket_view_menu())
                cursor.close()
                conn.close()
                return
            except Exception as e:
                print(f"Unexpected error in test message to user_id={user_id}: {e}\n{traceback.format_exc()}")
                bot.reply_to(message, f"Ошибка: не удалось отправить тестовое сообщение пользователю (ID: {user_id}): {e}.", reply_markup=create_ticket_view_menu())
                cursor.close()
                conn.close()
                return
            photo_id = message.photo[-1].file_id if message.content_type == 'photo' else None
            message_text = message.text if message.content_type == 'text' else None
            print(f"Preparing to send: message_text={message_text}, photo_id={photo_id}")
            if not message_text and not photo_id:
                print(f"No content to send: message_text={message_text}, photo_id={photo_id}")
                bot.reply_to(message, "Ошибка: сообщение или фото пустое.", reply_markup=create_ticket_view_menu())
                cursor.close()
                conn.close()
                return
            cursor.execute("INSERT INTO ticket_messages (ticket_id, telegram_id, message_text, photo_id, timestamp) "
                         "VALUES (%s, %s, %s, %s, NOW())",
                         (ticket_id, telegram_id, message_text, photo_id))
            conn.commit()
            sent = False
            if message_text:
                for attempt in range(5):
                    try:
                        sent_message = bot.send_message(user_id, f"Сообщение от администратора: {message_text}")
                        print(f"Sent text message to user_id={user_id}: {message_text} on attempt {attempt + 1}, message_id={sent_message.message_id}")
                        sent = True
                        bot.reply_to(message, f"Сообщение отправлено пользователю (ID: {user_id}).", reply_markup=create_ticket_view_menu())
                        break
                    except telebot.apihelper.ApiTelegramException as e:
                        print(f"Attempt {attempt + 1} failed to send text message to user_id={user_id}: {e}\n{traceback.format_exc()}")
                        if attempt < 4:
                            time.sleep(3)
                        else:
                            bot.reply_to(message, f"Не удалось отправить сообщение пользователю (ID: {user_id}): {e}. Попросите пользователя проверить настройки приватности или отправить /start.", reply_markup=create_ticket_view_menu())
                    except Exception as e:
                        print(f"Unexpected error in sending text message to user_id={user_id} on attempt {attempt + 1}: {e}\n{traceback.format_exc()}")
                        if attempt < 4:
                            time.sleep(3)
                        else:
                            bot.reply_to(message, f"Не удалось отправить сообщение пользователю (ID: {user_id}): {e}.", reply_markup=create_ticket_view_menu())
            if photo_id:
                for attempt in range(5):
                    try:
                        sent_photo = bot.send_photo(user_id, photo_id)
                        print(f"Sent photo to user_id={user_id}: {photo_id} on attempt {attempt + 1}, message_id={sent_photo.message_id}")
                        sent = True
                        bot.reply_to(message, f"Фото отправлено пользователю (ID: {user_id}).", reply_markup=create_ticket_view_menu())
                        break
                    except telebot.apihelper.ApiTelegramException as e:
                        print(f"Attempt {attempt + 1} failed to send photo to user_id={user_id}: {e}\n{traceback.format_exc()}")
                        if attempt < 4:
                            time.sleep(3)
                        else:
                            bot.reply_to(message, f"Не удалось отправить фото пользователю (ID: {user_id}): {e}. Попросите пользователя проверить настройки приватности или отправить /start.", reply_markup=create_ticket_view_menu())
                    except Exception as e:
                        print(f"Unexpected error in sending photo to user_id={user_id} on attempt {attempt + 1}: {e}\n{traceback.format_exc()}")
                        if attempt < 4:
                            time.sleep(3)
                        else:
                            bot.reply_to(message, f"Не удалось отправить фото пользователю (ID: {user_id}): {e}.", reply_markup=create_ticket_view_menu())
            if not sent:
                print(f"No content sent to user_id={user_id} for ticket_id={ticket_id}")
                bot.reply_to(message, f"Ошибка: не удалось отправить сообщение или фото пользователю (ID: {user_id}). Возможно, пользователь ограничил сообщения или проблемы с Telegram API.", reply_markup=create_ticket_view_menu())
            print(f"Handler completed: telegram_id={telegram_id}, ticket_id={ticket_id}, sent={sent}, admin_active_ticket={admin_active_ticket}")
        else:
            print(f"No user_id found for ticket_id={ticket_id} or ticket is closed")
            bot.reply_to(message, "Тема не найдена или закрыта.", reply_markup=create_admin_support_menu()[0] or create_admin_menu())
        cursor.close()
        conn.close()
    except (mysql.connector.Error if MYSQL_LIB == "mysql.connector" else MySQLdb.Error) as err:
        print(f"Database error in handle_admin_ticket_messages: {err}\n{traceback.format_exc()}")
        bot.reply_to(message, f"Ошибка базы данных: {err}", reply_markup=create_admin_support_menu()[0] or create_admin_menu())
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in handle_admin_ticket_messages for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")
        bot.reply_to(message, f"Ошибка Telegram API: {e}", reply_markup=create_admin_support_menu()[0] or create_admin_menu())
    except Exception as e:
        print(f"Unexpected error in handle_admin_ticket_messages: {e}\n{traceback.format_exc()}")
        bot.reply_to(message, f"Ошибка: {e}", reply_markup=create_admin_support_menu()[0] or create_admin_menu())

@bot.message_handler(content_types=['text', 'photo'], func=lambda message: message.from_user.id in ADMIN_IDS)
def debug_admin_messages(message):
    telegram_id = message.from_user.id
    print(f"Debug admin message: telegram_id={telegram_id}, chat_type={message.chat.type}, content_type={message.content_type}, message_text={message.text}, photo={message.photo}, message_json={message.json}")

@bot.message_handler(content_types=['text', 'photo'])
def handle_ticket_messages(message):
    if message.chat.type != 'private':
        print(f"Skipping handle_ticket_messages: chat_type={message.chat.type} is not private")
        return
    telegram_id = message.from_user.id
    if telegram_id in ADMIN_IDS:
        print(f"Skipping handle_ticket_messages for admin: telegram_id={telegram_id}, content_type={message.content_type}, message_text={message.text}, photo={message.photo}")
        return
    print(f"Evaluating handle_ticket_messages: telegram_id={telegram_id}, content_type={message.content_type}, message_text={message.text}, photo={message.photo}")
    try:
        conn = get_mysql_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ticket_id FROM tickets WHERE telegram_id = %s AND status = 'open'", (telegram_id,))
        ticket = cursor.fetchone()
        cursor.close()
        conn.close()
        if ticket:
            ticket_id = ticket[0]
            if telegram_id not in active_tickets:
                print(f"Syncing active_tickets: telegram_id={telegram_id}, ticket_id={ticket_id}")
                active_tickets[telegram_id] = ticket_id
        else:
            print(f"No active ticket for user: telegram_id={telegram_id}")
            bot.reply_to(message, "У вас нет активной темы. Создайте новую через 'Связаться со специалистом'.", reply_markup=create_support_menu())
            return
        ticket_id = active_tickets[telegram_id]
        print(f"Handling ticket message: telegram_id={telegram_id}, ticket_id={ticket_id}, content_type={message.content_type}")
        conn = get_mysql_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM tickets WHERE ticket_id = %s", (ticket_id,))
        ticket = cursor.fetchone()
        if ticket and ticket[0] == 'open':
            photo_id = message.photo[-1].file_id if message.content_type == 'photo' else None
            message_text = message.text if message.content_type == 'text' else None
            cursor.execute("INSERT INTO ticket_messages (ticket_id, telegram_id, message_text, photo_id, timestamp) "
                         "VALUES (%s, %s, %s, %s, NOW())",
                         (ticket_id, telegram_id, message_text, photo_id))
            conn.commit()
            for admin_id, active_ticket_id in admin_active_ticket.items():
                if active_ticket_id == ticket_id:
                    if message_text:
                        try:
                            bot.send_message(admin_id, f"Сообщение от пользователя (ID: {ticket_id}): {message_text}")
                            print(f"Sent user message to admin_id={admin_id}: {message_text}")
                        except telebot.apihelper.ApiTelegramException as e:
                            print(f"Failed to send user message to admin_id={admin_id}: {e}")
                    if photo_id:
                        try:
                            bot.send_photo(admin_id, photo_id)
                            print(f"Sent user photo to admin_id={admin_id}: {photo_id}")
                        except telebot.apihelper.ApiTelegramException as e:
                            print(f"Failed to send user photo to admin_id={admin_id}: {e}")
            bot.reply_to(message, "Ваше сообщение отправлено в поддержку.", reply_markup=create_back_to_support_menu(telegram_id))
        else:
            print(f"Ticket not open: ticket_id={ticket_id}, status={ticket[0] if ticket else 'not found'}")
            bot.reply_to(message, "Тема не найдена или закрыта. Создайте новую через 'Связаться со специалистом'.", reply_markup=create_support_menu())
        cursor.close()
        conn.close()
    except (mysql.connector.Error if MYSQL_LIB == "mysql.connector" else MySQLdb.Error) as err:
        print(f"Database error in handle_ticket_messages: {err}\n{traceback.format_exc()}")
        bot.reply_to(message, f"Ошибка базы данных: {err}", reply_markup=create_support_menu())
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in handle_ticket_messages for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")
        bot.reply_to(message, f"Ошибка Telegram API: {e}", reply_markup=create_support_menu())

def process_ticket_title(message):
    if message.chat.type != 'private':
        print(f"Skipping process_ticket_title: chat_type={message.chat.type} is not private")
        return
    telegram_id = message.from_user.id
    title = message.text.strip()
    telegram_username = message.from_user.username if message.from_user.username else message.from_user.first_name if message.from_user.first_name else "Unknown"
    print(f"Processing ticket title: telegram_id={telegram_id}, title={title}, telegram_username={telegram_username}")
    try:
        if not title:
            bot.reply_to(message, "Заголовок не может быть пустым. Попробуйте снова:", reply_markup=create_support_menu())
            bot.register_next_step_handler(message, process_ticket_title)
            return
        ticket_id = generate_ticket_id()
        conn = get_mysql_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO tickets (ticket_id, telegram_id, title, status) VALUES (%s, %s, %s, %s)",
                      (ticket_id, telegram_id, title, 'open'))
        cursor.execute("DESCRIBE telegram_users")
        columns = [row[0] for row in cursor.fetchall()]
        print(f"telegram_users columns in process_ticket_title: {columns}")
        if 'telegram_username' not in columns:
            print("telegram_username column missing in process_ticket_title, attempting to add")
            cursor.execute("ALTER TABLE telegram_users ADD COLUMN telegram_username VARCHAR(255)")
            conn.commit()
            print("Added telegram_username column in process_ticket_title")
        cursor.execute("INSERT INTO telegram_users (telegram_id, telegram_username) VALUES (%s, %s) "
                     "ON DUPLICATE KEY UPDATE telegram_username = %s",
                     (telegram_id, telegram_username, telegram_username))
        conn.commit()
        cursor.close()
        conn.close()
        active_tickets[telegram_id] = ticket_id
        print(f"Ticket created: telegram_id={telegram_id}, ticket_id={ticket_id}, title={title}, telegram_username={telegram_username}, active_tickets={active_tickets}")
        bot.reply_to(message, f"Тема создана: {title} (ID: {ticket_id})\nПожалуйста, опишите вашу проблему подробнее.", reply_markup=create_back_to_support_menu(telegram_id))
        for admin_id in ADMIN_IDS:
            try:
                bot.send_message(admin_id, f"Новая тема создана: {title} (ID: {ticket_id}, Minecraft: {minecraft_username}, Telegram: {telegram_username})")
                print(f"Notified admin_id={admin_id} of new ticket: ticket_id={ticket_id}, title={title}")
            except telebot.apihelper.ApiTelegramException as e:
                print(f"Failed to notify admin_id={admin_id} of new ticket: {e}")
    except (mysql.connector.Error if MYSQL_LIB == "mysql.connector" else MySQLdb.Error) as err:
        print(f"Database error in process_ticket_title: {err}\n{traceback.format_exc()}")
        bot.reply_to(message, f"Ошибка базы данных: {err}", reply_markup=create_support_menu())
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Error in process_ticket_title for telegram_id={telegram_id}: {e}\n{traceback.format_exc()}")

def get_open_tickets():
    try:
        conn = get_mysql_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ticket_id, title FROM tickets WHERE status = 'open'")
        tickets = cursor.fetchall()
        cursor.close()
        conn.close()
        print(f"get_open_tickets: Fetched {len(tickets)} tickets: {[(t[0], t[1]) for t in tickets]}")
        return tickets
    except (mysql.connector.Error if MYSQL_LIB == "mysql.connector" else MySQLdb.Error) as err:
        print(f"Error fetching tickets in get_open_tickets: {err}\n{traceback.format_exc()}")
        return []

# Flask webhook endpoint
@app.route('/bot', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        return '', 403

def set_webhook():
    try:
        bot.remove_webhook()
        time.sleep(0.1)
        success = bot.set_webhook(url=WEBHOOK_URL)
        if success:
            print(f"Webhook set successfully: {WEBHOOK_URL}")
        else:
            print("Failed to set webhook")
    except Exception as e:
        print(f"Error setting webhook: {e}\n{traceback.format_exc()}")

def start_flask():
    print(f"Starting Flask server")
    app.run(host='0.0.0.0', port=8080, debug=False)  # Port 8080 for Render

if __name__ == "__main__":
    print(f"Starting bot from global IP: {get_global_ip()}")
    init_mysql_db()
    threading.Thread(target=set_webhook).start()
    threading.Thread(target=keep_alive_pinger, daemon=True).start()
    start_flask()
