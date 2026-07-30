import os
import logging
from telegram import Update, Poll
from telegram.ext import Application, CommandHandler, ContextTypes

# Logging setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Token Koyeb se aayega
TOKEN = os.getenv("BOT_TOKEN")

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Swagat hai Quiz Bot mein!\n\n"
        "🎮 Quiz start karne ke liye **/quiz** command bhejien."
    )

# /quiz command
async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = "India ki Capital (Rajdhani) kya hai?"
    options = ["Mumbai", "Delhi", "Kolkata", "Chennai"]
    correct_option_id = 1  # Delhi

    await context.bot.send_poll(
        chat_id=update.effective_chat.id,
        question=question,
        options=options,
        type=Poll.QUIZ,
        correct_option_id=correct_option_id,
        is_anonymous=False
    )

def main():
    if not TOKEN:
        print("ERROR: BOT_TOKEN Environment Variable nahi mila!")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))

    print("Bot Start ho chuka hai...")
    app.run_polling()

if __name__ == '__main__':
    main()
  
