import os
import re
import zipfile
import shutil
import asyncio
import threading
from pathlib import Path
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# ============= Flask App =============
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot is running perfectly!"

@flask_app.route('/health')
def health():
    return "OK", 200

# ============= RAR Support =============
try:
    import rarfile
    RAR_SUPPORT = True
except ImportError:
    RAR_SUPPORT = False
    print("⚠️ rarfile module not found. RAR files will be skipped.")

# ============= Core Parsing & Processing Functions =============
def extract_text_from_archive(archive_path):
    content = ""
    ext = os.path.splitext(archive_path)[1].lower()
    if ext == '.zip':
        try:
            with zipfile.ZipFile(archive_path, 'r') as zf:
                for info in zf.infolist():
                    if not info.is_dir() and info.filename.lower().endswith('.txt'):
                        try:
                            with zf.open(info) as f:
                                content += f.read().decode('utf-8', errors='ignore') + "\n"
                        except Exception:
                            continue
        except Exception as e:
            print(f"Zip extraction error: {e}")
    elif ext == '.rar' and RAR_SUPPORT:
        try:
            with rarfile.RarFile(archive_path) as rf:
                for info in rf.infolist():
                    if not info.is_dir() and info.filename.lower().endswith('.txt'):
                        try:
                            with rf.open(info) as f:
                                content += f.read().decode('utf-8', errors='ignore') + "\n"
                        except Exception:
                            continue
        except Exception as e:
            print(f"RAR extraction error: {e}")
    return content

def parse_line_to_cc_cvv(line):
    """
    লাইন থেকে CC, MM, YY এবং CVV খুঁজে বের করার নমনীয় লজিক।
    CVV থাকলে তা এক্সট্র্যাক্ট করবে, না থাকলে CC|MM|YY| ফরম্যাটে নিয়ে আসবে।
    """
    if not line or len(line) < 12:
        return None

    # ১. ১৩ থেকে ১৯ ডিজিটের কার্ড নম্বর খোঁজা
    cc_match = re.search(r'\b([3-6]\d{12,18})\b', line)
    if not cc_match:
        return None
    cc = cc_match.group(1)

    # ২. এক্সপায়ারি ডেট (MM/YY, MM/YYYY, MM|YY, MM-YY ইত্যাদি) খোঁজা
    exp_match = re.search(r'\b(0[1-9]|1[0-2])[\s|/:\-,;]+(\d{4}|\d{2})\b', line)
    if not exp_match:
        return None
        
    mm = f"{int(exp_match.group(1)):02d}"
    yy = exp_match.group(2)[-2:] # সবসময় শেষ ২ ডিজিট (যেমন: 2032 -> 32)

    # ৩. CVV খোঁজা (কার্ড নম্বর ও এক্সপায়ারি ডেট বাদ দিয়ে বাকি অংশের ভেতর ৩ বা ৪ ডিজিট খোঁজা)
    temp_line = line.replace(cc, '').replace(exp_match.group(0), '')
    cvv_match = re.search(r'\b(\d{3,4})\b', temp_line)
    
    cvv = cvv_match.group(1) if cvv_match else ""

    return (cc, mm, yy, cvv)

def process_files(file_paths, output_path):
    unique_cards = set()
    for path in file_paths:
        path = Path(path)
        if not path.exists():
            continue
        ext = path.suffix.lower()
        content = ""
        if ext == '.txt':
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except Exception:
                continue
        elif ext in ['.zip', '.rar']:
            content = extract_text_from_archive(str(path))
        else:
            continue

        for line in content.splitlines():
            parsed = parse_line_to_cc_cvv(line)
            if parsed:
                cc, mm, yy, cvv = parsed
                unique_cards.add(f"{cc}|{mm}|{yy}|{cvv}")

    with open(output_path, 'w', encoding='utf-8') as f:
        for card in sorted(unique_cards):
            f.write(card + '\n')

    return len(unique_cards)

# ============= Telegram Bot Handlers =============
AWAITING_FILES = 1

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 স্বাগতম! /merge দিয়ে শুরু করুন।")

async def merge_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    temp_dir = Path(f"temp/{user_id}")
    temp_dir.mkdir(parents=True, exist_ok=True)
    context.user_data['temp_dir'] = str(temp_dir)
    context.user_data['files'] = []
    context.user_data['output_file'] = str(temp_dir / "merged_output.txt")
    await update.message.reply_text(
        "✅ মার্জ সেশন শুরু হয়েছে। এখন আপনার টেক্সট/জিপ/রার ফাইলগুলো পাঠান। সব শেষে `/done` দিন।"
    )
    return AWAITING_FILES

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'files' not in context.user_data:
        await update.message.reply_text("⚠️ আগে `/merge` দিন।")
        return AWAITING_FILES
    document = update.message.document
    file_name = document.file_name
    file_ext = os.path.splitext(file_name)[1].lower()
    if file_ext not in ['.txt', '.zip', '.rar']:
        await update.message.reply_text("❌ শুধু .txt, .zip বা .rar গ্রহণযোগ্য।")
        return AWAITING_FILES
    try:
        file = await document.get_file()
        temp_dir = Path(context.user_data['temp_dir'])
        file_path = temp_dir / file_name
        await file.download_to_drive(file_path)
        context.user_data['files'].append(str(file_path))
        await update.message.reply_text(f"✅ '{file_name}' জমা হয়েছে (মোট {len(context.user_data['files'])} টি)।")
    except Exception as e:
        await update.message.reply_text(f"❌ ডাউনলোডে সমস্যা: {e}")
    return AWAITING_FILES

async def done_merge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'files' not in context.user_data or not context.user_data['files']:
        await update.message.reply_text("⚠️ কোনো ফাইল জমা নেই।")
        return ConversationHandler.END
    files = context.user_data['files']
    output_path = context.user_data['output_file']
    temp_dir = Path(context.user_data['temp_dir'])
    await update.message.reply_text(f"⏳ {len(files)} টি ফাইল প্রসেস করা হচ্ছে...")
    try:
        loop = asyncio.get_running_loop()
        count = await loop.run_in_executor(None, process_files, files, output_path)
        if count == 0:
            await update.message.reply_text("😞 কোনো বৈধ কার্ড ডেটা পাওয়া যায়নি।")
        else:
            with open(output_path, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    filename="merged_output.txt",
                    caption=f"✅ মার্জ সম্পন্ন! মোট {count} টি ইউনিক লাইন পাওয়া গেছে।"
                )
    except Exception as e:
        await update.message.reply_text(f"❌ ত্রুটি: {e}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        context.user_data.clear()
    return ConversationHandler.END

async def cancel_merge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'temp_dir' in context.user_data:
        shutil.rmtree(Path(context.user_data['temp_dir']), ignore_errors=True)
    context.user_data.clear()
    await update.message.reply_text("🚫 বাতিল করা হয়েছে।")
    return ConversationHandler.END

# ============= Bot Starter (Proper Async Lifecycle) =============
def run_bot_async():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    TOKEN = os.getenv("BOT_TOKEN")
    if not TOKEN:
        print("❌ BOT_TOKEN environment variable not set!")
        return

    app = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("merge", merge_start)],
        states={
            AWAITING_FILES: [
                MessageHandler(filters.Document.ALL, handle_document),
                CommandHandler("done", done_merge),
                CommandHandler("cancel", cancel_merge),
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_merge)],
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)

    async def main():
        print("🤖 Initializing Bot...")
        await app.initialize()
        await app.start()
        print("🤖 Starting Polling...")
        await app.updater.start_polling(drop_pending_updates=True)
        while True:
            await asyncio.sleep(3600)

    try:
        loop.run_until_complete(main())
    except Exception as e:
        print(f"❌ Error running bot: {e}")

# ============= Main =============
if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot_async, daemon=True)
    bot_thread.start()

    port = int(os.environ.get("PORT", 10000))
    print(f"🚀 Flask Web Server listening on port {port}...")
    flask_app.run(host="0.0.0.0", port=port)
