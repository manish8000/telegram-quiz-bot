import os
import json
import logging
import random
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from groq import Groq
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, PollAnswerHandler, ContextTypes
)

# --- DUMMY WEB SERVER FOR KOYEB HEALTH CHECK ---
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

# --- LOGGING & ENVS ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

client = Groq(api_key=GROQ_API_KEY)

# --- GLOBAL DATA STORES ---
chat_sessions = {}      # Stores active quiz state per chat
scores = {}             # Scores per chat: { chat_id: { user_id: {"name": str, "score": int} } }
active_polls = {}       # Maps poll_id to quiz details
custom_questions = []   # Stores custom added questions

# Helper: Generate AI Question
def generate_ai_quiz(topic):
    seed_id = random.randint(10000, 99999)
    weight_choice = random.choices(["RAJ_GK", "GENERAL_MIX"], weights=[70, 30], k=1)[0]
    
    prompt = f"""
    You are an expert exam paper setter for competitive exams like RPSC, RSMSSB, RAS, and CET in Rajasthan.
    Generate 1 HARD/ADVANCED LEVEL multiple choice quiz question in HINDI script.
    
    Category: {weight_choice} (70% Rajasthan GK, 30% Science/India GK/Current).
    Topic: {topic}
    Constraint ID: {seed_id}

    Rules:
    - Keep question under 180 chars, each option under 80 chars.
    - Write question, options, and explanation strictly in Hindi.
    - Return ONLY valid raw JSON with NO markdown backticks.

    JSON Structure:
    {{
        "question": "प्रश्न text",
        "options": ["विकल्प A", "विकल्प B", "विकल्प C", "विकल्प D"],
        "answer_index": 0,
        "explanation": "स्पष्टीकरण text"
    }}
    """
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.85,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)

# Send Poll with 15-second timer
async def send_next_question(chat_id, context):
    if chat_id not in chat_sessions or not chat_sessions[chat_id]["active"]:
        return

    session = chat_sessions[chat_id]
    session["count"] += 1
    current_count = session["count"]

    # Har 100 questions ke baad Leaderboard/Scorecard dikhana
    if current_count > 100:
        await display_leaderboard(chat_id, context)
        session["count"] = 1  # Reset count for next batch of 100
        current_count = 1

    # Check for custom questions first, else use AI
    if custom_questions:
        q_data = custom_questions.pop(0)
    else:
        q_data = generate_ai_quiz(session["topic"])

    q_text = f"[{current_count}/100] {q_data['question']}\n\n📢 @dailyquiz_manish"
    exp_text = f"{q_data.get('explanation', 'सही उत्तर चुनें!')}\n\n👉 Join: @dailyquiz_manish"

    try:
        poll_msg = await context.bot.send_poll(
            chat_id=chat_id,
            question=q_text,
            options=q_data["options"],
            type="quiz",
            correct_option_id=int(q_data["answer_index"]),
            explanation=exp_text,
            open_period=15,  # ⏱️ 15 Second Telegram Native Timer
            is_anonymous=False
        )

        # Store Poll Metadata to track answers
        active_polls[poll_msg.poll.id] = {
            "chat_id": chat_id,
            "correct_option": int(q_data["answer_index"]),
            "handled": False
        }

        # ⏱️ Auto-Trigger Next Question after 16 seconds (if nobody answers or time expires)
        asyncio.create_task(auto_timeout_next(poll_msg.poll.id, chat_id, context))

    except Exception as e:
        logging.error(f"Error sending poll: {e}")

# Timeout Handler for 15s Timer
async def auto_timeout_next(poll_id, chat_id, context):
    await asyncio.sleep(16)
    if poll_id in active_polls:
        if not active_polls[poll_id]["handled"]:
            active_polls[poll_id]["handled"] = True
            await send_next_question(chat_id, context)

# Display Leaderboard/Scorecard
async def display_leaderboard(chat_id, context):
    if chat_id not in scores or not scores[chat_id]:
        await context.bot.send_message(chat_id, "📊 **100 Questions Complete!**\n\nKoi score record nahi hua.", parse_mode="Markdown")
        return

    sorted_scores = sorted(scores[chat_id].values(), key=lambda x: x["score"], reverse=True)
    
    text = "🏆 **TOP RANKERS - SCORECARD (100 Questions)** 🏆\n\n"
    medals = ["🥇", "🥈", "🥉"]
    
    for i, user in enumerate(sorted_scores[:10]):  # Top 10 users
        rank_icon = medals[i] if i < 3 else f"{i+1}."
        text += f"{rank_icon} **{user['name']}** — {user['score']} Points\n"

    text += "\n🎉 Badhai ho sabhi toppers ko!\n📢 Join: @dailyquiz_manish"
    await context.bot.send_message(chat_id, text, parse_mode="Markdown")

# --- COMMAND HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 **Rajasthan Exam Quiz Bot** me aapka swagat hai!\n\n"
        "📌 **Commands:**\n"
        "• `/quiz` - 15-second Timer Quiz Start karein\n"
        "• `/stop` - Quiz ko rokein\n"
        "• `/score` - Current Leaderboard dekhein\n"
        "• `/addquestion Q | Opt1 | Opt2 | Opt3 | Opt4 | AnsIndex(0-3) | Exp` - Custom Question Add karein\n"
        "• `/listcustom` - Custom Questions dekhein"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    topic = " ".join(context.args) if context.args else "Mixed Exam Special"
    
    chat_sessions[chat_id] = {"active": True, "count": 0, "topic": topic}
    scores[chat_id] = {}  # Reset session score

    await update.message.reply_text("🚀 **Quiz Started!** Har question ke paas 15s ka timer hai.", parse_mode="Markdown")
    await send_next_question(chat_id, context)

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in chat_sessions:
        chat_sessions[chat_id]["active"] = False
        await update.message.reply_text("🛑 **Quiz Stop ho gaya hai.** Final score ke liye `/score` type karein.")
    else:
        await update.message.reply_text("Koi active quiz nahi chal raha.")

async def score_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await display_leaderboard(update.effective_chat.id, context)

# Add Custom Question Handler
async def add_custom_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = " ".join(context.args)
    parts = [p.strip() for p in raw_text.split("|")]

    if len(parts) < 6:
        await update.message.reply_text(
            "❌ **Wrong Format!** Format follows:\n"
            "`/addquestion Question | OptA | OptB | OptC | OptD | CorrectIndex(0-3) | Explanation`",
            parse_mode="Markdown"
        )
        return

    q_dict = {
        "question": parts[0],
        "options": [parts[1], parts[2], parts[3], parts[4]],
        "answer_index": int(parts[5]),
        "explanation": parts[6] if len(parts) > 6 else "सही उत्तर चुने!"
    }
    custom_questions.append(q_dict)
    await update.message.reply_text(f"✅ **Custom Question Added!** Total Pending Custom: {len(custom_questions)}")

# Track Poll Answers & Update Score
async def receive_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    poll_id = answer.poll_id
    user = answer.user

    if poll_id not in active_polls:
        return

    poll_data = active_polls[poll_id]
    chat_id = poll_data["chat_id"]

    # Score Track Logic
    if chat_id not in scores:
        scores[chat_id] = {}

    if user.id not in scores[chat_id]:
        scores[chat_id][user.id] = {"name": user.first_name, "score": 0}

    # Answer checking
    if answer.option_ids and answer.option_ids[0] == poll_data["correct_option"]:
        scores[chat_id][user.id]["score"] += 1

    # Immediately trigger next question upon first answer
    if not poll_data["handled"]:
        poll_data["handled"] = True
        await send_next_question(chat_id, context)

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("score", score_cmd))
    app.add_handler(CommandHandler("addquestion", add_custom_question))
    app.add_handler(PollAnswerHandler(receive_poll_answer))

    print("Bot is starting...")
    app.run_polling()
        
