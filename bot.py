import os
import json
import logging
import random
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from groq import Groq
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# --- DUMMY WEB SERVER FOR KOYEB PORT 8000 HEALTH CHECK ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        return

def run_health_check():
    server = HTTPServer(('0.0.0.0', 8000), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_check, daemon=True).start()

# --- GROQ & TELEGRAM BOT LOGIC ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

client = Groq(api_key=GROQ_API_KEY)

# Helper function to generate question content from Groq
def generate_quiz_data(topic):
    seed_id = random.randint(1000, 99999)
    prompt = f"""
    Generate 1 UNIQUE multiple choice quiz question about '{topic}' in HINDI language.
    Constraint ID: {seed_id} (Ensure a completely new question is created each time).

    Rules:
    - Write the question and options in Hindi script (हिंदी).
    - Provide 4 options.
    - Return ONLY a valid raw JSON object with NO markdown, NO backticks.

    JSON format strictly like this:
    {{
        "question": "प्रश्न यहाँ लिखें",
        "options": ["विकल्प A", "विकल्प B", "विकल्प C", "विकल्प D"],
        "answer_index": 0,
        "explanation": "संक्षिप्त स्पष्टीकरण"
    }}
    """
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)

# Common function to send a new question
async def send_new_question(chat_id, context, topic, message_to_edit=None):
    if message_to_edit:
        msg = await message_to_edit.edit_text(f"🤖 **{topic}** par agla question bana raha hoon...", parse_mode="Markdown")
    else:
        msg = await context.bot.send_message(chat_id=chat_id, text=f"🤖 **{topic}** par question bana raha hoon...", parse_mode="Markdown")
        
    try:
        data = generate_quiz_data(topic)
        keyboard = []
        for i, opt in enumerate(data["options"]):
            keyboard.append([InlineKeyboardButton(opt, callback_data=f"ans_{i}_{data['answer_index']}")])
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await msg.edit_text(f"❓ **प्रश्न:** {data['question']}", reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Question banane mein problem aayi: {str(e)[:50]}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Hello! AI Quiz Bot mein aapka swagat hai.\n\nQuiz start karne ke liye likhein:\n`/quiz <topic>`\nExample: `/quiz History` ya `/quiz Cricket`", parse_mode="Markdown")

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args) if context.args else "General Knowledge"
    context.user_data['last_topic'] = topic
    await send_new_question(update.effective_chat.id, context, topic)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    # Answer checking logic
    _, selected, correct = query.data.split("_")
    topic = context.user_data.get('last_topic', 'General Knowledge')
    
    if selected == correct:
        await query.answer(text="✅ सही जवाब! 🎉", show_alert=False)
        await query.edit_message_text(text=f"{query.message.text}\n\n✅ **सही जवाब!** 🎉\n\n⏳ *Agla question 2 second me aa raha hai...*", parse_mode="Markdown")
    else:
        await query.answer(text=f"❌ गलत जवाब! सही option {int(correct)+1} था।", show_alert=False)
        await query.edit_message_text(text=f"{query.message.text}\n\n❌ **गलत जवाब!** सही विकल्प {int(correct)+1} था।\n\n⏳ *Agla question 2 second me aa raha hai...*", parse_mode="Markdown")

    # Wait 2 seconds and automatically load the next question
    await asyncio.sleep(2)
    await send_new_question(query.message.chat_id, context, topic)

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("Bot is starting...")
    app.run_polling()
    
