import html
import logging
import os
import re
from collections import Counter

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("wordcounter")

TOKEN = os.environ["BOT_TOKEN"]
MAX_FILE_BYTES = 1 * 1024 * 1024  # 1 MB text files
READ_WPM = 200   # average silent reading speed
SPEAK_WPM = 130  # average speaking speed

# Words (with inner apostrophes/hyphens). Each CJK character counts as one word.
WORD_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]|[^\W_]+(?:['’\-][^\W_]+)*")

STOP = set(
    "that this with from have they will would there their what about which when "
    "your been were them then than into just like more some very also only over "
    "such even most other because could should where while these those being "
    "does done here make made many much"
.split())

LIMITS = [
    ("SMS (1 message)", 160),
    ("X / Twitter post", 280),
    ("Instagram caption", 2200),
    ("Telegram message", 4096),
]

WELCOME = (
    "👋 <b>Welcome to Word Counter!</b>\n\n"
    "Send or paste any text and I'll count:\n"
    "• Words and characters\n"
    "• Sentences, paragraphs and lines\n"
    "• Reading and speaking time\n"
    "• Most-used words\n\n"
    "You can also send a <b>.txt file</b> (up to 1 MB).\n"
    "I don't store your text. 🔒\n\n"
    "Go ahead, send me some text ✍️"
)

HELP = (
    "<b>How to use</b>\n"
    "• Paste or forward any text to this chat\n"
    "• Or send a .txt file\n\n"
    "Long text? Telegram messages are limited to 4096 characters, "
    "so send longer text as a .txt file.\n\n"
    "/start - welcome message\n"
    "/help - this message"
)


def fmt_time(seconds: int) -> str:
    if seconds < 1:
        return "under 1 sec"
    if seconds < 60:
        return f"{seconds} sec"
    m, s = divmod(seconds, 60)
    return f"{m} min {s} sec" if s else f"{m} min"


def build_report(text: str) -> str:
    words = WORD_RE.findall(text)
    n_words = len(words)
    n_chars = len(text)
    n_chars_ns = len(re.sub(r"\s", "", text))

    stripped = text.strip()
    sentences = [
        s for s in re.split(r"(?<=[.!?…])\s+", stripped) if re.search(r"\w", s)
    ]
    paragraphs = [p for p in re.split(r"\n\s*\n", stripped) if p.strip()]
    lines = [l for l in text.splitlines() if l.strip()]

    read_s = round(n_words / READ_WPM * 60)
    speak_s = round(n_words / SPEAK_WPM * 60)

    freq = Counter(
        w.lower() for w in words if len(w) >= 4 and w.lower() not in STOP
    )
    top = [(w, c) for w, c in freq.most_common(5) if c >= 2]

    out = [
        "📊 <b>Text analysis</b>",
        "",
        f"📝 Words: <b>{n_words:,}</b>",
        f"🔤 Characters: <b>{n_chars:,}</b>",
        f"🔡 Characters (no spaces): <b>{n_chars_ns:,}</b>",
        f"💬 Sentences: <b>{len(sentences):,}</b>",
        f"📄 Paragraphs: <b>{len(paragraphs):,}</b>",
        f"↩️ Lines: <b>{len(lines):,}</b>",
        "",
        f"⏱ Reading time: <b>{fmt_time(read_s)}</b>",
        f"🎙 Speaking time: <b>{fmt_time(speak_s)}</b>",
    ]

    if top:
        out += ["", "🔝 <b>Most used words</b>"]
        out += [f"• {html.escape(w)} ({c})" for w, c in top]

    out += ["", "📏 <b>Character limits</b>"]
    for name, limit in LIMITS:
        if n_chars <= limit:
            out.append(f"✅ {name}: fits ({n_chars:,}/{limit:,})")
        else:
            out.append(f"❌ {name}: {n_chars - limit:,} over")

    return "\n".join(out)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.HTML)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)


async def count_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    await update.message.reply_text(build_report(text), parse_mode=ParseMode.HTML)


async def count_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    doc = msg.document
    if doc.file_size and doc.file_size > MAX_FILE_BYTES:
        await msg.reply_text("That file is too large. Please send a .txt file under 1 MB.")
        return
    try:
        tg_file = await doc.get_file()
        data = bytes(await tg_file.download_as_bytearray())
        text = data.decode("utf-8-sig", errors="replace")
    except Exception:
        log.exception("File read failed")
        await msg.reply_text("😕 I couldn't read that file. Please send a plain .txt file.")
        return
    if not text.strip():
        await msg.reply_text("That file looks empty.")
        return
    await msg.reply_text(build_report(text), parse_mode=ParseMode.HTML)


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error", exc_info=context.error)


async def post_init(app: Application):
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Start the bot"),
            BotCommand("help", "How to use"),
        ]
    )
    log.info("Bot @%s is running", app.bot.username)


def main():
    app = (
        Application.builder()
        .token(TOKEN)
        .concurrent_updates(True)
        .post_init(post_init)
        .build()
    )
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(
        MessageHandler(private & filters.TEXT & ~filters.COMMAND, count_text)
    )
    app.add_handler(
        MessageHandler(
            private & (filters.Document.TEXT | filters.Document.FileExtension("txt")),
            count_file,
        )
    )
    app.add_error_handler(on_error)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
