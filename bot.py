
import os
import json
import logging
import random
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Hello! AI Quiz Bot mein aapka swagat hai.\n\nQuiz start karne ke liye likhein:\n`/quiz <topic>`\nExample: `/quiz History` ya `/quiz Cricket`", parse_mode="Markdown")

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args) if context.args else "General Knowledge"
    msg = await update.message.reply_text(f"🤖 **{topic}** par naya question bana raha hoon, rukiye...", parse_mode="Markdown")
    
    # Har baar unique seed/id bhejenge taaki AI duplicate question na de
    seed_id = random.randint(1000, 9999)
    
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
    
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,  # Dynamic & fresh responses ke liye
            response_format={"type": "json_object"}
        )
        
        data = json.loads(response.choices[0].message.content)
        
        keyboard = []
        for i, opt in enumerate(data["options"]):
            keyboard.append([InlineKeyboardButton(opt, callback_data=f"ans_{i}_{data['answer_index']}")])
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await msg.edit_text(f"❓ **प्रश्न:** {data['question']}", reply_markup=reply_markup, parse_mode="Markdown")
        
    except Exception as e:
        await msg.edit_text(f"❌ Question banane mein problem aayi: {str(e)[:50]}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    _, selected, correct = query.data.split("_")
    if selected == correct:
        await query.edit_message_text(text=f"{query.message.text}\n\n✅ **सही जवाब!** 🎉")
    else:
        await query.edit_message_text(text=f"{query.message.text}\n\n❌ **गलत जवाब!** सही विकल्प {int(correct)+1} था।")

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("Bot is starting...")
    app.run_polling()
    
