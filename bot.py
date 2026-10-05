import asyncio
import io
import logging
import os

from PIL import Image, ImageOps
from rembg import new_session, remove
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputSticker,
    Update,
)
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("stickerbot")

TOKEN = os.environ["BOT_TOKEN"]
MODEL = os.getenv("REMBG_MODEL", "u2netp")  # u2netp = light, u2net = better quality
MAX_PARALLEL = int(os.getenv("MAX_PARALLEL", "2"))
MAX_INPUT_BYTES = 15 * 1024 * 1024

SESSION = new_session(MODEL)
SEM = asyncio.Semaphore(MAX_PARALLEL)

KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton("➕ Add to my sticker pack", callback_data="addpack")]]
)

WELCOME = (
    "👋 Welcome to Sticker Maker!\n\n"
    "Send me any photo and I'll remove the background and turn it into a "
    "Telegram sticker in seconds.\n\n"
    "• Best results: a clear subject (person, pet, object)\n"
    "• Tap “Add to my sticker pack” to build your own pack\n\n"
    "Send a photo to start 📸"
)


def make_sticker(data: bytes) -> bytes:
    """Remove background and return a 512px WEBP under 512 KB."""
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGBA")
    img.thumbnail((1024, 1024), Image.LANCZOS)  # speeds up processing

    cut = remove(img, session=SESSION)
    bbox = cut.getbbox()
    if bbox:
        cut = cut.crop(bbox)

    w, h = cut.size
    scale = 512 / max(w, h)
    cut = cut.resize(
        (max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS
    )

    for quality in (95, 85, 75, 60, 45):
        buf = io.BytesIO()
        cut.save(buf, format="WEBP", quality=quality, method=4)
        if buf.tell() <= 500 * 1024:
            break
    return buf.getvalue()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send a photo (or an image file) and I'll turn it into a sticker.\n\n"
        "/mypack - get the link to your sticker pack\n"
        "/start - show the welcome message"
    )


async def my_pack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = f"u{update.effective_user.id}_by_{context.bot.username}"
    try:
        await context.bot.get_sticker_set(name)
        await update.message.reply_text(f"📦 Your pack: https://t.me/addstickers/{name}")
    except TelegramError:
        await update.message.reply_text(
            "You don't have a pack yet. Make a sticker and tap “Add to my sticker pack”."
        )


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.photo:
        file_obj = msg.photo[-1]
    else:
        file_obj = msg.document
        if not file_obj.mime_type or not file_obj.mime_type.startswith("image/"):
            await msg.reply_text("Please send an image (photo or image file) 📸")
            return

    if file_obj.file_size and file_obj.file_size > MAX_INPUT_BYTES:
        await msg.reply_text("That image is too large. Please send one under 15 MB.")
        return

    await msg.chat.send_action(ChatAction.CHOOSE_STICKER)
    status = await msg.reply_text("✂️ Working on it...")
    try:
        tg_file = await file_obj.get_file()
        data = bytes(await tg_file.download_as_bytearray())
        async with SEM:
            webp = await asyncio.to_thread(make_sticker, data)
        context.user_data["last"] = webp
        await msg.reply_sticker(sticker=webp, reply_markup=KEYBOARD)
    except Exception:
        log.exception("Failed to make sticker")
        await msg.reply_text("😕 Sorry, I couldn't process that image. Try another one.")
    finally:
        try:
            await status.delete()
        except TelegramError:
            pass


async def add_to_pack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    webp = context.user_data.get("last")
    if not webp:
        await q.answer("Send a photo first 📸", show_alert=True)
        return
    await q.answer("Adding...")

    user = q.from_user
    name = f"u{user.id}_by_{context.bot.username}"
    sticker = InputSticker(sticker=webp, emoji_list=["😀"], format="static")

    try:
        try:
            await context.bot.get_sticker_set(name)
            exists = True
        except TelegramError:
            exists = False

        if exists:
            await context.bot.add_sticker_to_set(user.id, name, sticker)
        else:
            title = f"{user.first_name}'s stickers"[:64]
            await context.bot.create_new_sticker_set(user.id, name, title, [sticker])

        await q.message.reply_text(f"✅ Added!\n📦 Your pack: https://t.me/addstickers/{name}")
    except TelegramError as e:
        log.warning("Sticker pack error: %s", e)
        await q.message.reply_text(
            "😕 Couldn't add it to your pack (the pack may be full, 120 max). "
            "You can still forward the sticker."
        )


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error", exc_info=context.error)


async def post_init(app: Application):
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Start the bot"),
            BotCommand("mypack", "Get your sticker pack link"),
            BotCommand("help", "How to use"),
        ]
    )
    log.info("Bot @%s is running (model=%s)", app.bot.username, MODEL)


def main():
    app = (
        Application.builder()
        .token(TOKEN)
        .concurrent_updates(True)  # lets many users be served at once
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("mypack", my_pack))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_image))
    app.add_handler(CallbackQueryHandler(add_to_pack, pattern="^addpack$"))
    app.add_error_handler(on_error)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
