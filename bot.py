import os
import json
import logging
import random
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from groq import Groq
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, PollAnswerHandler, ContextTypes

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

# Active chat topics store karne ke liye dictionary
chat_topics = {}

def generate_quiz_data(topic):
    seed_id = random.randint(10000, 99999)
    
    # Randomly select section based on 70/30 weightage
    # 70% chance -> Rajasthan GK, 30% chance -> Current Affairs / India GK / Science
    weight_choice = random.choices(["RAJ_GK", "GENERAL_MIX"], weights=[70, 30], k=1)[0]
    
        prompt = f"""
    You are an expert exam paper setter for competitive exams like RPSC, RSMSSB, RAS, and CET in Rajasthan.
    
    Generate 1 HARD/ADVANCED LEVEL multiple choice quiz question in HINDI script.
    
    Strict Syllabus Distribution Rule for this question:
    - Selected Category: {weight_choice}
    - If 'RAJ_GK': Select a question strictly from Rajasthan GK (History, Polity, Geography, Art & Culture, or Economy).
    - If 'GENERAL_MIX': Select a question strictly from Latest Current Affairs, India GK, or General Science.
    
    Specific Topic requested by user (if any): {topic}

    Constraint ID: {seed_id} (Must create a completely unique question every time).

    Difficulty Rules:
    - Do NOT ask basic/easy questions like "Rajasthan ki rajdhani kya hai?".
    - Ask conceptual, modern, exam-standard moderate to tough questions.
    - Write question, options, and explanation strictly in Hindi (हिंदी).
    - Limit question to under 250 characters and each option to under 100 characters.
    - VERY IMPORTANT: Append "\n\n— Joined: @dailyquiz_manish" at the end of the question text.
    
    Return ONLY a valid raw JSON object with NO markdown, NO backticks.

    JSON Structure:
    {{
        "question": "कठिन या परीक्षा स्तर का प्रश्न (हिंदी में)\n\n— Join: @dailyquiz_manish",
        "options": ["विकल्प A", "विकल्प B", "विकल्प C", "विकल्प D"],
        "answer_index": 0,
        "explanation": "संक्षिप्त स्पष्टीकरण (हिंदी में)"
    }}
    """

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.85,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)

async def send_new_poll(chat_id, context, topic):
    try:
        data = generate_quiz_data(topic)
        
        await context.bot.send_poll(
            chat_id=chat_id,
            question=data["question"],
            options=data["options"],
            type="quiz",
            correct_option_id=int(data["answer_index"]),
            explanation=data.get("explanation", "सही उत्तर चुने!"),
            is_anonymous=False
        )
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Question banane mein problem aayi: {str(e)[:100]}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Rajasthan Exam Quiz Bot** me aapka swagat hai!\n\n"
        "Quiz start karne ke liye group me likhein:\n"
        "`/quiz` (For 70% Raj GK + 30% Current/Science/India GK)\n"
        "`/quiz Rajasthan Geography` (For specific topics)", 
        parse_mode="Markdown"
    )

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    topic = " ".join(context.args) if context.args else "Mixed Exam Special"
    chat_topics[chat_id] = topic
    
    temp_msg = await update.message.reply_text("🤖 *Exam Level Question tayar ho raha hai...*", parse_mode="Markdown")
    
    await send_new_poll(chat_id, context, topic)
    await temp_msg.delete()

# Answer dete hi auto next question
async def receive_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for chat_id, topic in list(chat_topics.items()):
        await send_new_poll(chat_id, context, topic)
        break

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(PollAnswerHandler(receive_poll_answer))
    
    print("Bot is starting...")
    app.run_polling()
    
