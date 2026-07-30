import os
import json
import logging
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
    await update.message.reply_text("👋 Hello! AI Quiz Bot mein aapka swagat hai.\n\nQuiz start karne ke liye likhein:\n`/quiz <topic>`", parse_mode="Markdown")

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args) if context.args else "General Knowledge"
    msg = await update.message.reply_text(f"🤖 **{topic}** par AI question bana raha hai, 2 second rukiye...", parse_mode="Markdown")
    
    prompt = f"""
    Generate 1 multiple choice quiz question about '{topic}'.
    Return ONLY a valid JSON object with NO markdown formatting, NO backticks.
    JSON structure:
    {{
        "question": "Question text",
        "options": ["Option A", "Option B", "Option C", "Option D"],
        "answer_index": 0,
        "explanation": "Short explanation"
    }}
    """
    
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        
        data = json.loads(response.choices[0].message.content)
        
        keyboard = []
        for i, opt in enumerate(data["options"]):
            keyboard.append([InlineKeyboardButton(opt, callback_data=f"ans_{i}_{data['answer_index']}")])
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await msg.edit_text(f"❓ **Question:** {data['question']}", reply_markup=reply_markup, parse_mode="Markdown")
        
    except Exception as e:
        await msg.edit_text(f"❌ Question banane mein problem aayi: {str(e)[:50]}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    _, selected, correct = query.data.split("_")
    if selected == correct:
        await query.edit_message_text(text=f"{query.message.text}\n\n✅ **Sahi Jawab!** 🎉")
    else:
        await query.edit_message_text(text=f"{query.message.text}\n\n❌ **Galat Jawab!** Sahi answer option {int(correct)+1} tha.")

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("Bot is starting...")
    app.run_polling()
    
