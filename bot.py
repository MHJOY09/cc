import os
import re
import zipfile
import shutil
from pathlib import Path
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# ============= Flask App =============
app = Flask(__name__)

# ============= Bot Token =============
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set!")

bot = Bot(token=TOKEN)

# ============= RAR Support =============
try:
    import rarfile
    RAR_SUPPORT = True
except ImportError:
    RAR_SUPPORT = False
    print("⚠️ rarfile not found. RAR files will be skipped.")

# ============= Core Functions =============
def extract_text_from_archive(archive_path):
    content = ""
    ext = os.path.splitext(archive_path)[1].lower()
    if ext == '.zip':
        with zipfile.ZipFile(archive_path, 'r') as zf:
            for info in zf.infolist():
                if not info.is_dir() and info.filename.lower().endswith('.txt'):
                    try:
                        with zf.open(info) as f:
                            content += f.read().decode('utf-8', errors='ignore') + "\n"
                    except Exception:
                        continue
    elif ext == '.rar' and RAR_SUPPORT:
        with rarfile.RarFile(archive_path) as rf:
            for info in rf.infolist():
                if not info.is_dir() and info.filename.lower().endswith('.txt'):
                    try:
                        with rf.open(info) as f:
                            content += f.read().decode('utf-8', errors='ignore') + "\n"
                    except Exception:
                        continue
    return content

def clean_cc(cc):
    return re.sub(r'[^0-9]', '', cc)

def validate_parts(cc, mm, yy, cvv):
    cc_clean = clean_cc(cc)
    if not (13 <= len(cc_clean) <= 19):
        return False
    if not mm.isdigit() or not (1 <= int(mm) <= 12):
        return False
    if not yy.isdigit() or not (0 <= int(yy) <= 99):
        if len(yy) == 4 and yy.isdigit():
            pass
        else:
            return False
    if not cvv.isdigit() or not (3 <= len(cvv) <= 4):
        return False
    return True

def parse_line_to_cc_cvv(line):
    line = line.strip()
    if not line:
        return None
    parts = line.split('|')
    if len(parts) == 4:
        cc, mm, yy, cvv = parts
        if cc.replace(' ', '').isdigit() and mm.isdigit() and yy.isdigit() and cvv.isdigit():
            if validate_parts(cc, mm, yy, cvv):
                return (clean_cc(cc), mm, yy, cvv)
    digit_groups = re.findall(r'\d+', line)
    if len(digit_groups) >= 4:
        cc_candidate = digit_groups[0]
        mm_candidate = digit_groups[1]
        yy_candidate = digit_groups[2]
        cvv_candidate = digit_groups[3]
        if validate_parts(cc_candidate, mm_candidate, yy_candidate, cvv_candidate):
            return (clean_cc(cc_candidate), mm_candidate, yy_candidate, cvv_candidate)
    return None

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
                if len(yy) == 4:
                    yy = yy[-2:]
                mm = f"{int(mm):02d}"
                unique_cards.add(f"{cc}|{mm}|{yy}|{cvv}")
    with open(output_path, 'w', encoding='utf-8') as f:
        for card in sorted(unique_cards):
            f.write(card + '\n')
    return len(unique_cards)

# ============= Telegram Handlers =============
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
        await update.message.reply_text(f"❌ শুধু .txt, .zip বা .rar গ্রহণযোগ্য।")
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
        import asyncio
        loop = asyncio.get_running_loop()
        count = await loop.run_in_executor(None, process_files, files, output_path)
        if count == 0:
            await update.message.reply_text("😞 কোনো বৈধ ডেটা পাওয়া যায়নি।")
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

# ============= Build Application =============
application = Application.builder().token(TOKEN).build()
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
application.add_handler(CommandHandler("start", start))
application.add_handler(conv_handler)

# ============= Webhook Endpoint =============
@app.route('/', methods=['GET'])
def home():
    return "Bot is running!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    """টেলিগ্রাম থেকে ইনকামিং আপডেট গ্রহণ"""
    try:
        update = Update.de_json(request.get_json(force=True), bot)
        application.process_update(update)
        return "OK", 200
    except Exception as e:
        print(f"Webhook error: {e}")
        return "Error", 500

# ============= Set Webhook =============
def set_webhook():
    """Render-এর URL দিয়ে ওয়েবহুক সেট করা"""
    app_url = os.getenv("RENDER_EXTERNAL_URL")
    if not app_url:
        print("⚠️ RENDER_EXTERNAL_URL not set. Webhook not configured.")
        return
    webhook_url = f"{app_url}/webhook"
    print(f"🔗 Setting webhook to: {webhook_url}")
    bot.set_webhook(webhook_url)

# ============= Main =============
if __name__ == "__main__":
    # ওয়েবহুক সেট করুন
    set_webhook()
    
    # Flask চালু করুন
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Flask server running on port {port}...")
    app.run(host="0.0.0.0", port=port)
