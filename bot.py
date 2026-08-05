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
    return "Cyber Engine Online!"

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
    if not line or len(line) < 12:
        return None

    # ১৩ থেকে ১৯ ডিজিটের কার্ড নম্বর খোঁজা
    cc_match = re.search(r'\b([3-6]\d{12,18})\b', line)
    if not cc_match:
        return None
    cc = cc_match.group(1)

    # এক্সপায়ারি ডেট খোঁজা
    exp_match = re.search(r'\b(0[1-9]|1[0-2])[\s|/:\-,;]+(\d{4}|\d{2})\b', line)
    if not exp_match:
        return None
        
    mm = f"{int(exp_match.group(1)):02d}"
    yy = exp_match.group(2)[-2:]

    # CVV খোঁজা (৩ বা ৪ ডিজিট)
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
    msg = (
        "⚡ **CYBER SCANNER ENGINE v2.4** ⚡\n"
        "────────────────────────\n"
        "SYSTEM: `ONLINE` 🟢\n\n"
        "👉 Start Session: /merge\n"
        "👉 Abort Session: /cancel"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def merge_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    temp_dir = Path(f"temp/{user_id}")
    temp_dir.mkdir(parents=True, exist_ok=True)
    context.user_data['temp_dir'] = str(temp_dir)
    context.user_data['files'] = []
    context.user_data['output_file'] = str(temp_dir / "merged_output.txt")
    
    # Live Status Dashboard
    status_msg = await update.message.reply_text(
        "🧠 **CYBER ENGINE INITIALIZED**\n"
        "────────────────────────\n"
        "📥 `STATUS`: Waiting for files...\n"
        "📦 `TOTAL FILES`: `0`\n"
        "📄 `LAST LOADED`: `None`\n"
        "────────────────────────\n"
        "⚡ Send `.txt`, `.zip`, or `.rar` files.\n"
        "🚀 Send `/done` when finished.",
        parse_mode="Markdown"
    )
    context.user_data['status_msg_id'] = status_msg.message_id
    return AWAITING_FILES

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'files' not in context.user_data:
        await update.message.reply_text("⚠️ System inactive. Use `/merge` first.", parse_mode="Markdown")
        return AWAITING_FILES

    # চ্যাট পরিষ্কার রাখতে ইউজারের পাঠানো ফাইল মেসেজ সাথে সাথেই ডিলিট করা
    try:
        await update.message.delete()
    except Exception:
        pass

    document = update.message.document
    file_name = document.file_name
    file_ext = os.path.splitext(file_name)[1].lower()

    if file_ext not in ['.txt', '.zip', '.rar']:
        return AWAITING_FILES

    try:
        file = await document.get_file()
        temp_dir = Path(context.user_data['temp_dir'])
        file_path = temp_dir / file_name
        await file.download_to_drive(file_path)
        context.user_data['files'].append(str(file_path))
        
        # লাইভ ড্যাশবোর্ড টেক্সট আপডেট
        count = len(context.user_data['files'])
        status_msg_id = context.user_data.get('status_msg_id')
        
        updated_text = (
            "⚙️ **ANALYZING & BUFFERING DATA**...\n"
            "────────────────────────\n"
            "📥 `STATUS`: Ingesting Logs...\n"
            f"📦 `TOTAL FILES`: `{count}`\n"
            f"📄 `LAST LOADED`: `{file_name}`\n"
            "────────────────────────\n"
            "⚡ Keep sending files or send `/done` to execute."
        )
        
        if status_msg_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=status_msg_id,
                    text=updated_text,
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    except Exception as e:
        print(f"Download error: {e}")

    return AWAITING_FILES

async def done_merge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ইউজারের /done মেসেজটি ডিলিট করা
    try:
        await update.message.delete()
    except Exception:
        pass

    if 'files' not in context.user_data or not context.user_data['files']:
        await update.message.reply_text("⚠️ No data loaded in buffer.", parse_mode="Markdown")
        return ConversationHandler.END

    files = context.user_data['files']
    output_path = context.user_data['output_file']
    temp_dir = Path(context.user_data['temp_dir'])
    status_msg_id = context.user_data.get('status_msg_id')

    # ড্যাশবোর্ড এনালাইজিং স্টেটে নেওয়া
    processing_text = (
        "🔍 **CYBER PARSER EXECUTING**\n"
        "────────────────────────\n"
        "⚙️ `STAGE`: Extracting & Filtering Duplicates...\n"
        f"📦 `FILES IN QUEUE`: `{len(files)}`\n"
        "⏳ `STATUS`: Running RegEx Engine...\n"
        "────────────────────────\n"
        "⏳ Please wait..."
    )
    if status_msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_msg_id,
                text=processing_text,
                parse_mode="Markdown"
            )
        except Exception:
            pass

    try:
        loop = asyncio.get_running_loop()
        count = await loop.run_in_executor(None, process_files, files, output_path)

        # ড্যাশবোর্ড মেসেজটি ডিলিট করে ক্লিন ফাইনাল রিপ্লাই দেওয়া
        if status_msg_id:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=status_msg_id)
            except Exception:
                pass

        if count == 0:
            await update.message.reply_text("⚠️ **PARSE FAILED**: No valid target data found in buffer.", parse_mode="Markdown")
        else:
            final_caption = (
                "🎯 **EXTRACTION COMPLETE**\n"
                "────────────────────────\n"
                f"📊 `TOTAL UNIQUE CARDS`: `{count}`\n"
                f"📁 `PROCESSED FILES`: `{len(files)}`\n"
                "🛡️ `FORMAT`: `CC|MM|YY|CVV`\n"
                "────────────────────────\n"
                "🔥 Session Closed Successfully."
            )
            with open(output_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename="Merged_Cards_Output.txt",
                    caption=final_caption,
                    parse_mode="Markdown"
                )
    except Exception as e:
        await update.message.reply_text(f"❌ `SYSTEM ERROR`: {e}", parse_mode="Markdown")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        context.user_data.clear()

    return ConversationHandler.END

async def cancel_merge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.delete()
    except Exception:
        pass

    status_msg_id = context.user_data.get('status_msg_id')
    if status_msg_id:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=status_msg_id)
        except Exception:
            pass

    if 'temp_dir' in context.user_data:
        shutil.rmtree(Path(context.user_data['temp_dir']), ignore_errors=True)
    
    context.user_data.clear()
    await update.message.reply_text("🚫 **SESSION ABORTED & BUFFER PURGED**", parse_mode="Markdown")
    return ConversationHandler.END

# ============= Bot Starter =============
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
        print("🤖 Initializing Cyber Bot Engine...")
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
