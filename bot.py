import telebot
import os
import pdfplumber
import re
from datetime import datetime, timedelta
import threading
from flask import Flask

# ================== CONFIG ==================
BOTTOKEN = "7972205321:AAFkpCpePT8ynRqdOr6qYOlcKmFA-ikEjwE"
ADMIN_ID = 5436942211  # 🔴 PUT YOUR TELEGRAM ID HERE

if not BOTTOKEN:
    print("❌ BOT_TOKEN missing")
    exit()

bot = telebot.TeleBot(BOTTOKEN)

# ================== WEB SERVER ==================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running"

def run_web():
    app.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False)

# ================== STORAGE ==================
FOLDER = "driverlogs"
if not os.path.exists(FOLDER):
    os.makedirs(FOLDER)

user_data = {}
users = set()

# ================== HELPERS ==================

def extract_driver(text):
    match = re.search(r"Driver Name\s+([A-Za-z]+\s+[A-Za-z]+)", text)
    return match.group(1) if match else "Unknown"

def clean_location(loc):
    loc = re.sub(r"\d+(\.\d+)?", "", loc)
    loc = re.sub(r"\s+", " ", loc)

    match = re.search(r"of\s+(.+?,\s*[A-Z]{2})", loc)
    if match:
        return match.group(1)

    return loc.strip()

def format_time(dt):
    return dt.strftime("%m/%d %I:%M %p")

# ================== PARSER ==================

def parse_logs(text):
    pattern = r"(\d{2}/\d{2}, \d{2}:\d{2}:\d{2}).*?(Off Duty|Sleeper|Driving|On Duty).*?(.+)"
    matches = re.findall(pattern, text)

    logs = []
    for time_str, status, location in matches:
        try:
            dt = datetime.strptime(time_str, "%m/%d, %H:%M:%S")
            logs.append({
                "time": dt,
                "status": status,
                "location": location.strip()
            })
        except:
            continue

    logs.sort(key=lambda x: x["time"])
    return logs

def find_blocks(logs):
    blocks = []
    current = None

    for log in logs:
        if log["status"] in ["Off Duty", "Sleeper"]:
            if current is None:
                current = {"start": log["time"], "location": log["location"]}
        else:
            if current:
                current["end"] = log["time"]
                current["duration"] = current["end"] - current["start"]
                blocks.append(current)
                current = None
    return blocks

def get_latest_shift(blocks):
    valid = [b for b in blocks if b["duration"] >= timedelta(hours=10)]
    return max(valid, key=lambda x: x["end"]) if valid else None

def get_latest_cycle(blocks):
    valid = [b for b in blocks if b["duration"] >= timedelta(hours=34)]
    return max(valid, key=lambda x: x["end"]) if valid else None

def get_first_work(logs):
    for log in logs:
        if log["status"] in ["Driving", "On Duty"]:
            return log
    return None

# ================== PICKUP ==================

def get_pickup(text):
    lines = text.split("\n")
    pickup_lines = []

    for line in lines:
        if "pickup" in line.lower() or "load" in line.lower():
            pickup_lines.append(line)

    if not pickup_lines:
        return None

    last = pickup_lines[-1]

    time_match = re.search(r"(\d{2}/\d{2}, \d{2}:\d{2}:\d{2})", last)
    location_match = re.search(r"of\s+(.+?,\s*[A-Z]{2})", last)

    result = {}

    if time_match:
        dt = datetime.strptime(time_match.group(1), "%m/%d, %H:%M:%S")
        result["time"] = format_time(dt)
        result["day"] = dt.strftime("%m/%d")
    else:
        result["time"] = "Not Found"
        result["day"] = "Not Found"

    result["location"] = clean_location(location_match.group(1)) if location_match else "Not Found"

    return result

# ================== START ==================

@bot.message_handler(commands=['start'])
def start(msg):
    users.add(msg.chat.id)
    bot.reply_to(msg, "🚛 Send PDF")

# ================== ADMIN PANEL ==================

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Not allowed")
        return

    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("👥 Users", "📊 Stats", "📢 Broadcast")

    bot.send_message(message.chat.id, "Admin Panel", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ["👥 Users", "📊 Stats", "📢 Broadcast"])
def admin_actions(message):
    if message.from_user.id != ADMIN_ID:
        return

    if message.text == "👥 Users":
        bot.send_message(message.chat.id, f"Total users: {len(users)}")

    elif message.text == "📊 Stats":
        bot.send_message(message.chat.id, "Bot is running perfectly 🚀")

    elif message.text == "📢 Broadcast":
        bot.send_message(message.chat.id, "Send message to broadcast:")
        bot.register_next_step_handler(message, broadcast_message)

def broadcast_message(message):
    if message.from_user.id != ADMIN_ID:
        return

    for user in users:
        try:
            bot.send_message(user, message.text)
        except:
            pass

# ================== HANDLE PDF ==================

@bot.message_handler(content_types=['document'])
def handle_pdf(message):
    if message.document.mime_type != 'application/pdf':
        bot.reply_to(message, "❌ Send PDF only")
        return

    users.add(message.chat.id)

    bot.reply_to(message, "⏳ Processing...")

    file_info = bot.get_file(message.document.file_id)
    file = bot.download_file(file_info.file_path)

    path = os.path.join(FOLDER, message.document.file_name)
    with open(path, 'wb') as f:
        f.write(file)

    text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"

    logs = parse_logs(text)
    blocks = find_blocks(logs)

    shift = get_latest_shift(blocks)
    cycle = get_latest_cycle(blocks)
    first_work = get_first_work(logs)
    pickup = get_pickup(text)
    driver = extract_driver(text)

    user_data[message.chat.id] = {
        "driver": driver,
        "shift": shift,
        "cycle": cycle,
        "first": first_work,
        "pickup": pickup
    }

    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton("📦 Pickup", callback_data="pickup"),
        telebot.types.InlineKeyboardButton("🔄 Cycle", callback_data="cycle"),
        telebot.types.InlineKeyboardButton("🛌 Shift", callback_data="shift")
    )

    bot.send_message(message.chat.id, f"Driver: {driver}\n\nChoose option:", reply_markup=markup)

# ================== BUTTON HANDLER ==================

@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    bot.answer_callback_query(call.id)

    data = user_data.get(call.message.chat.id)
    if not data:
        bot.send_message(call.message.chat.id, "No data")
        return

    if call.data == "pickup":
        p = data["pickup"]
        if p:
            text = f"📦 Pickup Info:\nTime: {p['time']}\nDay: {p['day']}\nLocation: {p['location']}"
        else:
            text = "📦 Pickup Info:\nNot Found"

    elif call.data == "cycle":
        c = data["cycle"]
        if c:
            text = f"🔄 Cycle Info:\nFrom: {format_time(c['start'])}\nTill: {format_time(c['end'])}\nLocation: {clean_location(c['location'])}"
        else:
            text = "🔄 Cycle Info:\nNot Found"

    elif call.data == "shift":
        s = data["shift"]
        if s:
            text = f"🛌 Shift Info:\nFrom: {format_time(s['start'])}\nTill: {format_time(s['end'])}\nLocation: {clean_location(s['location'])}"
        else:
            text = "🛌 Shift Info:\nNot Found"

    bot.send_message(call.message.chat.id, text)

# ================== RUN ==================

if __name__ == "__main__":
    threading.Thread(target=run_web).start()
    print("🚀 Bot Running...")
    bot.infinity_polling(skip_pending=True)
