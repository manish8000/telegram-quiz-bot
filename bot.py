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

# Helper function to generate HARD Rajasthan & Latest GK Questions
def generate_quiz_data(topic):
    seed_id = random.randint(10000, 99999)
    
    prompt = f"""
    You are an expert exam paper setter for competitive exams like RPSC, RSMSSB, RAS, and CET in Rajasthan.
    
    Generate 1 HARD/ADVANCED LEVEL multiple choice quiz question in HINDI script.
    
    Topics Priority:
    1. If user topic is specified, use that.
    2. Otherwise, give 70% weightage to Rajasthan General Knowledge (History, Culture, Geography, Polity, Economic Review) and Latest Current Affairs, and 30% to Indian GK.

    Constraint ID: {seed_id} (Must create a completely unique question every time).

    Difficulty Rules:
    - Do NOT ask basic/easy questions like "Rajasthan ki rajdhani kya hai?".
    - Ask conceptual, modern, exam-standard moderate to tough questions.
    - Write question and all options strictly in Hindi (हिंदी).
    
    Return ONLY a valid raw JSON object with NO markdown, NO backticks.

    JSON Structure:
    {{
        "question": "कठिन या परीक्षा स्तर का प्रश्न (हिंदी में)",
        "options": ["विकल्प A", "विकल्प B", "विकल्प C", "विकल्प D"],
        "answer_index": 0,
        "explanation": "संक्षिप्त स्पष्टीकरण"
    }}
    """
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.85,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)

# Send new fresh question as a NEW message
async def send_new_question(chat_id, context, topic):
    try:
        data = generate_quiz_data(topic)
        keyboard = []
        for i, opt in enumerate(data["options"]):
            keyboard.append([InlineKeyboardButton(opt, callback_data=f"ans_{i}_{data['answer_index']}")])
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"❓ **प्रश्न:** {data['question']}", 
            reply_markup=reply_markup, 
            parse_mode="Markdown"
        )
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Question banane mein problem aayi: {str(e)[:50]}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Rajasthan Exam Quiz Bot** me aapka swagat hai!\n\n"
        "RPSC / RSMSSB / CET level questions ke liye type karein:\n"
        "`/quiz` (For Rajasthan & Latest Mixed Quiz)\n"
        "`/quiz Rajasthan History` (For specific topics)", 
        parse_mode="Markdown"
    )

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args) if context.args else "Rajasthan Special & Latest GK"
    context.user_data['last_topic'] = topic
    
    # Send temporary status message
    temp_msg = await update.message.reply_text("🤖 *Naya question tayar ho raha hai...*", parse_mode="Markdown")
    
    # Delete temporary loading message and send actual question
    await send_new_question(update.effective_chat.id, context, topic)
    await temp_msg.delete()

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    _, selected, correct = query.data.split("_")
    topic = context.user_data.get('last_topic', 'Rajasthan Special & Latest GK')
    
    # Show instant Telegram Alert popup (No chat cluttering text!)
    if selected == correct:
        await query.answer(text="✅ Bilkul Sahi Jawab! 🎉", show_alert=True)
    else:
        await query.answer(text=f"❌ Galat Jawab! Sahi option ({int(correct)+1}) tha.", show_alert=True)

    # Automatically trigger next question immediately as a NEW message
    await send_new_question(query.message.chat_id, context, topic)

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("Bot is starting...")
    app.run_polling()
    
