import os
import logging
import json
import google.generativeai as genai
from telegram import Update, Poll
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Gemini Setup
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Quiz Bot ready hai! AI se quiz banane ke liye **/quiz topic** likhein.\n\nExample: `/quiz Science` ya `/quiz Bollywood`")

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args) if context.args else "General Knowledge"
    await update.message.reply_text(f"🤖 **{topic}** par AI question bana raha hai, 5 second rukiye...")

    prompt = f"""
    Generate 1 multiple choice question on the topic: '{topic}'.
    Return ONLY a raw JSON object with NO markdown formatting, NO ```json blocks, like this:
    {{
        "question": "Question text here?",
        "options": ["Option1", "Option2", "Option3", "Option4"],
        "correct_option_id": 0
    }}
    Note: correct_option_id must be an integer from 0 to 3.
    """

    try:
        response = model.generate_content(prompt)
        data = json.loads(response.text.strip())

        await context.bot.send_poll(
            chat_id=update.effective_chat.id,
            question=data["question"],
            options=data["options"],
            type=Poll.QUIZ,
            correct_option_id=data["correct_option_id"],
            is_anonymous=False
        )
    except Exception as e:
        await update.message.reply_text("❌ Question banane mein problem aayi, dobara try karein!")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.run_polling()

if __name__ == '__main__':
    main()
    
