import os
import re
import zipfile
import shutil
import asyncio
import threading
import time
import urllib.request
from pathlib import Path
from time import time as current_time
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
)
from telegram.error import TelegramError, Forbidden, BadRequest

# ============= Flask Web Server =============
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Cyber Engine Online!"

@flask_app.route('/health')
def health():
    return "OK", 200

# ============= Self-Ping System =============
def self_ping_loop():
    time.sleep(10)
    port = os.environ.get("PORT", "10000")
    url = f"http://127.0.0.1:{port}/health"
    while True:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                pass
        except Exception:
            pass
        time.sleep(600)

# ============= RAR Support =============
try:
    import rarfile
    RAR_SUPPORT = True
except ImportError:
    RAR_SUPPORT = False

# ============= Luhn Validation =============
def luhn_check(cc_num: str) -> bool:
    if not cc_num.isdigit():
        return False
    digits = [int(d) for d in cc_num]
    check_digit = digits.pop()
    digits.reverse()
    for i in range(0, len(digits), 2):
        doubled = digits[i] * 2
        digits[i] = doubled - 9 if doubled > 9 else doubled
    total = sum(digits) + check_digit
    return total % 10 == 0

# ============= Card Type Detection =============
def get_card_type(cc: str) -> str:
    if cc.startswith('4'):
        return 'Visa'
    if cc.startswith(('51','52','53','54','55')) or \
       (len(cc) >= 4 and '2221' <= cc[:4] <= '2720'):
        return 'Mastercard'
    if cc.startswith(('34','37')):
        return 'Amex'
    if cc.startswith(('6011','65')) or \
       (len(cc) >= 3 and '644' <= cc[:3] <= '649') or \
       (len(cc) >= 6 and '622126' <= cc[:6] <= '622925'):
        return 'Discover'
    if cc.startswith(('36','38','300','301','302','303','304','305')):
        return 'Diners'
    if len(cc) >= 4 and '3528' <= cc[:4] <= '3589':
        return 'JCB'
    if cc.startswith('62'):
        return 'UnionPay'
    return 'Unknown'

# ============= Core Processing =============
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
        except Exception:
            pass
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
        except Exception:
            pass
    return content

def parse_line_to_cc_cvv(line):
    if not line or len(line) < 12:
        return None

    cc_match = re.search(r'\b([3-6]\d{12,18})\b', line)
    if not cc_match:
        return None
    cc = cc_match.group(1)
    if not luhn_check(cc):
        return None

    exp_match = re.search(r'\b(0[1-9]|1[0-2])[\s|/:\-,;]+(\d{4}|\d{2})\b', line)
    if not exp_match:
        return None
    mm = f"{int(exp_match.group(1)):02d}"
    yy = exp_match.group(2)[-2:]

    temp_line = line.replace(cc, '').replace(exp_match.group(0), '')
    cvv_match = re.search(r'\b(\d{3,4})\b', temp_line)
    if not cvv_match:
        return None
    cvv = cvv_match.group(1)
    ctype = get_card_type(cc)
    return (cc, mm, yy, cvv, ctype)

def extract_cards_from_text(content):
    unique_cards = set()
    for line in content.splitlines():
        parsed = parse_line_to_cc_cvv(line)
        if parsed:
            cc, mm, yy, cvv, ctype = parsed
            unique_cards.add(f"{cc}|{mm}|{yy}|{cvv}|{ctype}")

    block_pattern = re.compile(
        r'(?:CN|CARD|CC)?[:\s]*([3-6]\d{12,18})[\s\S]*?'
        r'(?:DATE|EXP)?[:\s]*(0[1-9]|1[0-2])[\s/|\-,;]+(\d{4}|\d{2})[\s\S]*?'
        r'(?:CVV|CVC)[:\s]*(\d{3,4})',
        re.IGNORECASE
    )
    for match in block_pattern.finditer(content):
        cc = match.group(1)
        if not luhn_check(cc):
            continue
        mm = f"{int(match.group(2)):02d}"
        yy = match.group(3)[-2:]
        cvv = match.group(4)
        if cvv:
            ctype = get_card_type(cc)
            unique_cards.add(f"{cc}|{mm}|{yy}|{cvv}|{ctype}")
    return unique_cards

def process_files(file_paths, output_path, loop, progress_queue):
    all_unique_cards = set()
    total = len(file_paths)

    for idx, path in enumerate(file_paths, 1):
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

        extracted = extract_cards_from_text(content)
        all_unique_cards.update(extracted)

        if loop and progress_queue:
            loop.call_soon_threadsafe(progress_queue.put_nowait, (idx, total, path.name))

    with open(output_path, 'w', encoding='utf-8') as f:
        for card in sorted(all_unique_cards):
            f.write(card + '\n')

    return len(all_unique_cards)

# ============= Telegram Bot Handlers =============
AWAITING_FILES = 1
SESSION_TIMEOUT = 1200  # 20 minutes

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a detailed welcome/help message."""
    keyboard = [[InlineKeyboardButton("🚀 Start Session", callback_data="start_merge")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    help_text = (
        "⚡ **CYBER SCANNER ENGINE v3.6** ⚡\n"
        "────────────────────────────\n"
        "🛡️ **Features at a glance:**\n"
        "• Luhn Algorithm – validates real card numbers\n"
        "• CVV Detection – only cards with CVV\n"
        "• Card Type Identifier – Visa, Mastercard, Amex, etc.\n"
        "• Duplicate Removal – unique CC|MM|YY|CVV\n"
        "• Archive Support – .zip & .rar files\n"
        "• Inline Buttons – one‑tap control\n"
        "• Auto Timeout (20 min) – auto cleanup\n"
        "• Live Progress Bar – ASCII block progress\n"
        "• Group Support – result sent privately\n\n"
        "📌 **Commands:**\n"
        "/merge – Start a new session\n"
        "/done – Process & get output\n"
        "/cancel – Abort session\n\n"
        "👇 Tap **Start Session** or type /merge"
    )
    try:
        await update.message.reply_text(help_text, parse_mode="Markdown", reply_markup=reply_markup)
    except (Forbidden, TelegramError):
        pass

async def merge_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    temp_dir = Path(f"temp/{user_id}_{chat_id}")
    temp_dir.mkdir(parents=True, exist_ok=True)
    context.user_data['temp_dir'] = str(temp_dir)
    context.user_data['files'] = []
    context.user_data['output_file'] = str(temp_dir / "merged_output.txt")
    context.user_data['initiator'] = user_id
    context.user_data['last_activity'] = current_time()

    # Start the lightweight timeout checker (single task, no repeated create/cancel)
    if 'timeout_task' not in context.user_data or context.user_data['timeout_task'].done():
        timeout_task = asyncio.create_task(timeout_checker(update, context))
        context.user_data['timeout_task'] = timeout_task

    keyboard = [
        [InlineKeyboardButton("✅ Done", callback_data="done_merge"),
         InlineKeyboardButton("❌ Cancel", callback_data="cancel_merge")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        status_msg = await update.effective_message.reply_text(
            "🧠 **CYBER ENGINE INITIALIZED**\n"
            "────────────────────────\n"
            "📥 `STATUS`: Waiting for files...\n"
            "📦 `TOTAL FILES`: `0`\n"
            "📄 `LAST LOADED`: `None`\n"
            "⏳ `TIMEOUT`: 20 min (resets on every file)\n"
            "────────────────────────\n"
            "⚡ Send `.txt`, `.zip`, or `.rar` files.\n"
            "Use buttons below to finish or cancel.",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        context.user_data['status_msg_id'] = status_msg.message_id
    except (Forbidden, TelegramError):
        pass

    return AWAITING_FILES

async def timeout_checker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check every 15 seconds if session should timeout."""
    try:
        while 'files' in context.user_data:
            await asyncio.sleep(15)
            if 'last_activity' not in context.user_data:
                break
            if current_time() - context.user_data['last_activity'] > SESSION_TIMEOUT:
                # Timeout reached, clean up
                status_msg_id = context.user_data.get('status_msg_id')
                if status_msg_id:
                    try:
                        await context.bot.delete_message(
                            chat_id=update.effective_chat.id,
                            message_id=status_msg_id
                        )
                    except:
                        pass
                if 'temp_dir' in context.user_data:
                    shutil.rmtree(Path(context.user_data['temp_dir']), ignore_errors=True)
                context.user_data.clear()
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="⏰ **Session timed out due to inactivity. Files cleared.**",
                    parse_mode="Markdown"
                )
                break
    except Exception as e:
        print(f"Timeout checker error: {e}")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'files' not in context.user_data or update.effective_user.id != context.user_data.get('initiator'):
        return AWAITING_FILES

    document = update.message.document
    file_name = document.file_name
    file_ext = os.path.splitext(file_name)[1].lower()

    if file_ext not in ['.txt', '.zip', '.rar']:
        return AWAITING_FILES

    # Update activity timestamp (timer reset without creating new task)
    context.user_data['last_activity'] = current_time()

    try:
        file = await document.get_file()
        temp_dir = Path(context.user_data['temp_dir'])
        file_path = temp_dir / file_name
        await file.download_to_drive(file_path)
        context.user_data['files'].append(str(file_path))

        count = len(context.user_data['files'])
        status_msg_id = context.user_data.get('status_msg_id')

        updated_text = (
            "⚙️ **ANALYZING & BUFFERING DATA**...\n"
            "────────────────────────\n"
            "📥 `STATUS`: Ingesting Logs...\n"
            f"📦 `TOTAL FILES`: `{count}`\n"
            f"📄 `LAST LOADED`: `{file_name}`\n"
            "────────────────────────\n"
            "⚡ Keep sending files or use buttons below."
        )

        keyboard = [
            [InlineKeyboardButton("✅ Done", callback_data="done_merge"),
             InlineKeyboardButton("❌ Cancel", callback_data="cancel_merge")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if status_msg_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=status_msg_id,
                    text=updated_text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
            except (Forbidden, BadRequest, TelegramError):
                pass

    except Exception as e:
        print(f"Download error: {e}")

    return AWAITING_FILES

async def progress_updater(queue, chat_id, message_id, context):
    last_percent = -1
    while True:
        data = await queue.get()
        if data is None:
            queue.task_done()
            break
        idx, total, fname = data
        percent = int((idx / total) * 100) if total > 0 else 100
        # ASCII block progress bar (works on all devices)
        filled_blocks = "█" * (percent // 10)
        empty_blocks = "░" * (10 - (percent // 10))
        bar = filled_blocks + empty_blocks
        text = (
            "🔍 **CYBER PARSER EXECUTING**\n"
            "────────────────────────\n"
            f"⚙️ `STAGE`: Processing file {idx}/{total}...\n"
            f"📄 `CURRENT`: `{fname}`\n"
            f"📊 `PROGRESS`: {bar} {percent}%\n"
            "────────────────────────\n"
            "⏳ Please wait..."
        )
        if percent != last_percent:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode="Markdown"
                )
            except Exception:
                pass
            last_percent = percent
        queue.task_done()

async def done_merge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Cancel timeout task (stop the checker loop)
    if 'timeout_task' in context.user_data:
        context.user_data['timeout_task'].cancel()

    if update.message:
        try:
            await update.message.delete()
        except:
            pass

    if 'files' not in context.user_data or not context.user_data['files']:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ No data loaded in buffer.",
                parse_mode="Markdown"
            )
        except (Forbidden, TelegramError):
            pass
        return ConversationHandler.END

    files = context.user_data['files']
    output_path = context.user_data['output_file']
    temp_dir = Path(context.user_data['temp_dir'])
    status_msg_id = context.user_data.get('status_msg_id')
    chat_id = update.effective_chat.id
    initiator_id = context.user_data['initiator']

    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    updater_task = asyncio.ensure_future(
        progress_updater(queue, chat_id, status_msg_id, context)
    )

    try:
        count = await loop.run_in_executor(
            None, process_files, files, output_path, loop, queue
        )
        await queue.put(None)
        await updater_task

        if status_msg_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=status_msg_id)
            except (Forbidden, BadRequest, TelegramError):
                pass

        if count == 0:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ **PARSE FAILED**: No valid cards with CVV found.",
                parse_mode="Markdown"
            )
        else:
            final_caption = (
                "🎯 **EXTRACTION COMPLETE**\n"
                "────────────────────────\n"
                f"📊 `TOTAL CARDS (WITH CVV & LUHN)`: `{count}`\n"
                f"📁 `PROCESSED FILES`: `{len(files)}`\n"
                "🛡️ `FORMAT`: `CC|MM|YY|CVV|Type`\n"
                "────────────────────────\n"
                "🔥 Session Closed Successfully."
            )

            if update.effective_chat.type in ['group', 'supergroup']:
                try:
                    await context.bot.send_document(
                        chat_id=initiator_id,
                        document=open(output_path, 'rb'),
                        filename="Merged_Cards_With_CVV.txt",
                        caption=final_caption,
                        parse_mode="Markdown"
                    )
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="✅ **Processing complete.** I've sent you the result in private.",
                        parse_mode="Markdown"
                    )
                except Forbidden:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="⚠️ I cannot send you a private message. Please start a chat with me first: https://t.me/CC_Marger_bot",
                        parse_mode="Markdown"
                    )
            else:
                with open(output_path, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=f,
                        filename="Merged_Cards_With_CVV.txt",
                        caption=final_caption,
                        parse_mode="Markdown"
                    )

    except Exception as e:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ `SYSTEM ERROR`: {e}",
                parse_mode="Markdown"
            )
        except (Forbidden, TelegramError):
            pass
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        context.user_data.clear()

    return ConversationHandler.END

async def cancel_merge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'timeout_task' in context.user_data:
        context.user_data['timeout_task'].cancel()

    if update.message:
        try:
            await update.message.delete()
        except:
            pass

    status_msg_id = context.user_data.get('status_msg_id')
    if status_msg_id:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=status_msg_id)
        except (Forbidden, BadRequest, TelegramError):
            pass

    if 'temp_dir' in context.user_data:
        shutil.rmtree(Path(context.user_data['temp_dir']), ignore_errors=True)

    context.user_data.clear()
    try:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🚫 **SESSION ABORTED & BUFFER PURGED**",
            parse_mode="Markdown"
        )
    except (Forbidden, TelegramError):
        pass

    return ConversationHandler.END

# ============= Callback Query Handler =============
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "start_merge":
        await merge_start(update, context)
        return AWAITING_FILES
    elif query.data == "done_merge":
        await done_merge(update, context)
        return ConversationHandler.END
    elif query.data == "cancel_merge":
        await cancel_merge(update, context)
        return ConversationHandler.END

# ============= Async Bot Launcher =============
def run_bot_async():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    TOKEN = os.getenv("BOT_TOKEN")
    if not TOKEN:
        print("❌ BOT_TOKEN missing!")
        return

    app = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("merge", merge_start),
            CallbackQueryHandler(button_handler, pattern="^start_merge$")
        ],
        states={
            AWAITING_FILES: [
                MessageHandler(filters.Document.ALL, handle_document),
                CommandHandler("done", done_merge),
                CommandHandler("cancel", cancel_merge),
                CallbackQueryHandler(button_handler, pattern="^(done_merge|cancel_merge)$")
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
        print("🤖 Polling Loop Active...")
        await app.updater.start_polling(drop_pending_updates=True)
        while True:
            await asyncio.sleep(3600)

    try:
        loop.run_until_complete(main())
    except Exception as e:
        print(f"❌ Core Error: {e}")

# ============= Application Main =============
if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot_async, daemon=True)
    bot_thread.start()

    ping_thread = threading.Thread(target=self_ping_loop, daemon=True)
    ping_thread.start()

    port = int(os.environ.get("PORT", 10000))
    print(f"🚀 Web Server Active on Port {port}...")
    flask_app.run(host="0.0.0.0", port=port)
