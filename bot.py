import os
import json
import logging
import random
import asyncio
import re
import threading
from io import BytesIO
import pypdf
from http.server import HTTPServer, BaseHTTPRequestHandler
from groq import Groq
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, PollAnswerHandler, CallbackQueryHandler, filters, ContextTypes
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

# Global Stores
chat_pdf_text = {}
chat_sessions = {}
active_polls = {}
user_scores = {}  # {chat_id: {user_id: {"name": str, "correct": int, "incorrect": int, "score": float}}}
user_creation_state = {}  # {user_id: {"state": str, "title": str, "polls": []}}
created_quizzes = {}      # {title: [poll_data]}

# Helper Function: Check Admin Status
async def is_user_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    user = update.effective_user

    # Private Chat mein user ko allow karein
    if chat.type == "private":
        return True

    # Group/Supergroup mein check karein ki user Admin hai ya nahi
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        return member.status in ["administrator", "creator"]
    except Exception as e:
        logging.error(f"Admin check error: {e}")
        return False

def clean_extracted_text(text):
    text = re.sub(r'\s+', ' ', text)
    return text[:1500].strip()

# PDF Question Generator
def generate_quiz_from_text(text_content):
    seed_id = random.randint(10000, 99999)
    safe_text = clean_extracted_text(text_content)
    
    prompt = f"""You are a quiz generator. Generate 1 multiple choice question in Hindi based on this text:
"{safe_text}"

Constraint ID: {seed_id}

Respond ONLY with valid JSON in this exact structure:
{{
    "question": "प्रश्न (हिंदी में)",
    "options": ["विकल्प A", "विकल्प B", "विकल्प C", "विकल्प D"],
    "answer_index": 0,
    "explanation": "स्पष्टीकरण (हिंदी में)"
}}"""

    try:
        response = client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logging.error(f"Error generating PDF quiz: {e}")
        return None

# General AI Question Generator
def generate_ai_quiz(topic):
    seed_id = random.randint(10000, 99999)
    
    prompt = f"""Generate 1 hard multiple choice question in Hindi script for Rajasthan exams on topic: {topic}.
Constraint ID: {seed_id}

Respond ONLY with valid JSON in this exact structure:
{{
    "question": "प्रश्न (हिंदी में)",
    "options": ["विकल्प A", "विकल्प B", "विकल्प C", "विकल्प D"],
    "answer_index": 0,
    "explanation": "स्पष्टीकरण (हिंदी में)"
}}"""

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logging.error(f"Error generating AI quiz: {e}")
        return None

# Send Poll Function
async def send_next_question(chat_id, context):
    if chat_id not in chat_sessions or not chat_sessions[chat_id]["active"]:
        return

    session = chat_sessions[chat_id]
    timer_val = session.get("timer", 15)
    q_data = None
    
    if chat_id in chat_pdf_text and chat_pdf_text[chat_id]:
        q_data = generate_quiz_from_text(chat_pdf_text[chat_id])
    
    if not q_data:
        q_data = generate_ai_quiz(session.get("topic", "Rajasthan GK"))

    if not q_data:
        await asyncio.sleep(2)
        return await send_next_question(chat_id, context)

    q_text = f"{q_data['question']}\n\n📢 @dailyquiz_manish"
    exp_text = f"{q_data.get('explanation', 'सही उत्तर चुनें!')}\n⚠️ नेगेटिव मार्किंग: 1/3 (-0.33)\n👉 Join: @dailyquiz_manish"

    try:
        poll_msg = await context.bot.send_poll(
            chat_id=chat_id,
            question=q_text,
            options=q_data["options"],
            type="quiz",
            correct_option_id=int(q_data["answer_index"]),
            explanation=exp_text,
            open_period=timer_val,
            is_anonymous=False
        )

        active_polls[poll_msg.poll.id] = {
            "chat_id": chat_id,
            "handled": False,
            "correct_option": int(q_data["answer_index"])
        }

        asyncio.create_task(auto_timeout_next(poll_msg.poll.id, chat_id, timer_val, context))

    except Exception as e:
        logging.error(f"Error sending poll: {e}")

async def auto_timeout_next(poll_id, chat_id, timer_val, context):
    await asyncio.sleep(timer_val + 1)
    if poll_id in active_polls and not active_polls[poll_id]["handled"]:
        active_polls[poll_id]["handled"] = True
        await send_next_question(chat_id, context)

# --- ADMIN RESTRICTED QUIZ CREATION FLOW (/create) ---
async def create_quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_admin(update, context):
        await update.message.reply_text("🚫 **यह कमांड केवल ग्रुप एडमिन ही इस्तेमाल कर सकते हैं!**")
        return

    user_id = update.effective_user.id
    user_creation_state[user_id] = {"state": "AWAITING_TITLE", "title": "", "polls": []}
    await update.message.reply_text("✨ **नया क्विज़ बनाएँ**\n\nकृपया क्विज़ का नाम (Title) लिखकर भेजें:")

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Creation Step 1: Title Input
    if user_id in user_creation_state and user_creation_state[user_id]["state"] == "AWAITING_TITLE":
        title = update.message.text.strip()
        user_creation_state[user_id]["title"] = title
        user_creation_state[user_id]["state"] = "AWAITING_POLLS"
        
        keyboard = [[InlineKeyboardButton("✅ क्विज़ पूरी करें (Done)", callback_data="finish_quiz_creation")]]
        await update.message.reply_text(
            f"📝 क्विज़ का नाम: **{title}**\n\n"
            f"अब किसी भी Telegram channel या group से क्विज़ polls फॉरवर्ड (Send) करें।\n"
            f"जब सारे polls भेज दें, तब नीचे **Done** बटन पर क्लिक करें।",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# Message Poll Handler for forwarded polls
async def handle_poll_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_creation_state and user_creation_state[user_id]["state"] == "AWAITING_POLLS":
        poll = update.message.poll
        if poll:
            user_creation_state[user_id]["polls"].append(poll)
            count = len(user_creation_state[user_id]["polls"])
            await update.message.reply_text(f"✅ Poll #{count} जुड़ गया है! और भेजें या Done पर क्लिक करें।")

# PDF Upload Handler
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    
    if doc.mime_type == 'application/pdf' or doc.file_name.endswith('.pdf'):
        msg = await update.message.reply_text("📄 *PDF मिल गई है, टेक्स्ट रीड कर रहा हूँ...*", parse_mode="Markdown")
        
        try:
            file = await context.bot.get_file(doc.file_id)
            pdf_bytes = await file.download_as_bytearray()
            
            pdf_reader = pypdf.PdfReader(BytesIO(pdf_bytes))
            extracted_text = ""
            for page in pdf_reader.pages:
                extracted_text += (page.extract_text() or "") + " "

            chat_id = update.effective_chat.id
            chat_pdf_text[chat_id] = extracted_text
            
            keyboard = [[InlineKeyboardButton("🚀 PDF Quiz चालू करें", callback_data="start_pdf_quiz")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await msg.edit_text("✅ *PDF Read हो गई!* नीचे दिए बटन पर क्लिक करके क्विज़ स्टार्ट करें:", parse_mode="Markdown", reply_markup=reply_markup)
        except Exception as e:
            logging.error(f"Error reading PDF: {e}")
            await msg.edit_text("❌ PDF पढ़ने में समस्या आई।")
    else:
        await update.message.reply_text("❌ कृपया सिर्फ PDF फाइल ही भेजें।")

# Start Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🚀 Normal Quiz", callback_data="start_normal_quiz"),
         InlineKeyboardButton("📄 PDF Quiz", callback_data="start_pdf_quiz")],
        [InlineKeyboardButton("➕ Create Quiz (Admin)", callback_data="btn_create_quiz"),
         InlineKeyboardButton("⏱️ Timer (15s/30s)", callback_data="menu_timer")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="show_leaderboard"),
         InlineKeyboardButton("🛑 Stop Quiz", callback_data="stop_quiz")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "👋 **Rajasthan Exam Quiz Bot** में आपका स्वागत है!\n\n"
        "• **1/3 Negative Marking** लागू है।\n"
        "नीचे दिए गए बटनों का उपयोग करके क्विज़ कंट्रोल करें:"
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

# Callback Query Handler
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user_id = query.from_user.id

    if query.data == "start_normal_quiz":
        chat_sessions[chat_id] = {"active": True, "topic": "Mixed Exam Special", "timer": chat_sessions.get(chat_id, {}).get("timer", 15)}
        if chat_id in chat_pdf_text:
            del chat_pdf_text[chat_id]
        await query.message.reply_text("🚀 **Normal AI Quiz Started!**")
        await send_next_question(chat_id, context)

    elif query.data == "start_pdf_quiz":
        if chat_id not in chat_pdf_text or not chat_pdf_text[chat_id]:
            await query.message.reply_text("❌ पहले कोई PDF फाइल सेंड करें!")
            return
        chat_sessions[chat_id] = {"active": True, "topic": "PDF Based", "timer": chat_sessions.get(chat_id, {}).get("timer", 15)}
        await query.message.reply_text("📄 **PDF Quiz Started!**")
        await send_next_question(chat_id, context)

    elif query.data == "btn_create_quiz":
        if not await is_user_admin(update, context):
            await query.message.reply_text("🚫 **यह सुविधा केवल एडमिन के लिए है!**")
            return
            
        user_creation_state[user_id] = {"state": "AWAITING_TITLE", "title": "", "polls": []}
        await query.message.reply_text("✨ **नया क्विज़ बनाएँ**\n\nकृपया क्विज़ का नाम (Title) लिखकर भेजें:")

    elif query.data == "finish_quiz_creation":
        if user_id in user_creation_state:
            data = user_creation_state[user_id]
            title = data["title"]
            poll_count = len(data["polls"])
            created_quizzes[title] = data["polls"]
            del user_creation_state[user_id]
            await query.message.reply_text(f"🎉 **Your quiz '{title}' will be created successfully!**\nकुल जुड़े Polls: {poll_count}")

    elif query.data == "stop_quiz":
        if chat_id in chat_sessions and chat_sessions[chat_id]["active"]:
            chat_sessions[chat_id]["active"] = False
            await query.message.reply_text("🛑 **क्विज़ रोक दी गई है!**")
        else:
            await query.message.reply_text("⚠️ कोई भी चालू क्विज़ नहीं मिली।")

    elif query.data == "menu_timer":
        keyboard = [
            [InlineKeyboardButton("15 Seconds", callback_data="timer_15"),
             InlineKeyboardButton("30 Seconds", callback_data="timer_30"),
             InlineKeyboardButton("45 Seconds", callback_data="timer_45")]
        ]
        await query.message.reply_text("⏱️ **क्विज़ के प्रति प्रश्न का समय चुनें:**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith("timer_"):
        timer_val = int(query.data.split("_")[1])
        if chat_id not in chat_sessions:
            chat_sessions[chat_id] = {"active": False, "topic": "Mixed Special"}
        chat_sessions[chat_id]["timer"] = timer_val
        await query.message.reply_text(f"✅ Timer को **{timer_val} सेकेंड** पर सेट कर दिया गया है!")

    elif query.data == "show_leaderboard":
        await send_leaderboard(query.message, chat_id)

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args_filtered = [arg for arg in context.args if not arg.startswith("@")]
    topic = " ".join(args_filtered) if args_filtered else "Mixed Exam Special"
    
    current_timer = chat_sessions.get(chat_id, {}).get("timer", 15)
    chat_sessions[chat_id] = {"active": True, "topic": topic, "timer": current_timer}
    
    if chat_id in chat_pdf_text:
        del chat_pdf_text[chat_id]

    await update.message.reply_text(f"🚀 **Quiz Started!**\nTopic: {topic}\nTimer: {current_timer}s")
    await send_next_question(chat_id, context)

async def pdfquiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in chat_pdf_text or not chat_pdf_text[chat_id]:
        await update.message.reply_text("❌ पहले कोई PDF फाइल सेंड करें!")
        return

    current_timer = chat_sessions.get(chat_id, {}).get("timer", 15)
    chat_sessions[chat_id] = {"active": True, "topic": "PDF Based", "timer": current_timer}
    await update.message.reply_text(f"📄 **PDF Quiz Started!**\nTimer: {current_timer}s")
    await send_next_question(chat_id, context)

async def clearpdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in chat_pdf_text:
        del chat_pdf_text[chat_id]
    await update.message.reply_text("🗑️ PDF मेमोरी से हटा दी गई है।")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in chat_sessions and chat_sessions[chat_id]["active"]:
        chat_sessions[chat_id]["active"] = False
        await update.message.reply_text("🛑 **क्विज़ रोक दी गई है!**")
    else:
        await update.message.reply_text("⚠️ कोई भी चालू क्विज़ नहीं मिली।")

# Leaderboard Helper Function
async def send_leaderboard(message_obj, chat_id):
    if chat_id not in user_scores or not user_scores[chat_id]:
        await message_obj.reply_text("🏆 **Leaderboard अभी खाली है!** क्विज़ खेलें और अंक हासिल करें।")
        return

    sorted_scores = sorted(user_scores[chat_id].values(), key=lambda x: x["score"], reverse=True)
    lb_text = "🏆 **क्विज़ लीडरबोर्ड (1/3 Negative Marking)** 🏆\n\n"
    
    medals = ["🥇", "🥈", "🥉"]
    for idx, u in enumerate(sorted_scores[:10]):
        prefix = medals[idx] if idx < 3 else f"{idx+1}."
        lb_text += f"{prefix} **{u['name']}** | स्कोर: {u['score']:.2f} (✅ {u['correct']} | ❌ {u['incorrect']})\n"

    await message_obj.reply_text(lb_text, parse_mode="Markdown")

async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_leaderboard(update.message, update.effective_chat.id)

# Poll Answer Tracker with 1/3 Negative Marking
async def receive_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    poll_id = answer.poll_id
    user = answer.user

    if poll_id in active_polls:
        correct_option = active_polls[poll_id]["correct_option"]
        chat_id = active_polls[poll_id]["chat_id"]

        if chat_id not in user_scores:
            user_scores[chat_id] = {}
        if user.id not in user_scores[chat_id]:
            user_scores[chat_id][user.id] = {"name": user.first_name, "correct": 0, "incorrect": 0, "score": 0.0}

        u_data = user_scores[chat_id][user.id]

        # 1/3 Negative Marking Logic
        if answer.option_ids and answer.option_ids[0] == correct_option:
            u_data["correct"] += 1
            u_data["score"] += 1.0
            msg = f"🎉 **{user.first_name}**, सही जवाब! (+1 अंक)"
        else:
            u_data["incorrect"] += 1
            u_data["score"] -= (1/3)
            msg = f"❌ **{user.first_name}**, गलत जवाब! (-0.33 नेगेटिव अंक)"

        total_q = u_data["correct"] + u_data["incorrect"]
        score_msg = (
            f"{msg}\n"
            f"📊 **आपका वर्तमान स्कोर:** {u_data['score']:.2f} / {total_q}\n"
            f"(✅ सही: {u_data['correct']} | ❌ गलत: {u_data['incorrect']})"
        )
        await context.bot.send_message(chat_id=chat_id, text=score_msg, parse_mode="Markdown")

        if not active_polls[poll_id]["handled"]:
            active_polls[poll_id]["handled"] = True
            await send_next_question(chat_id, context)

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("pdfquiz", pdfquiz))
    app.add_handler(CommandHandler("clearpdf", clearpdf))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("create", create_quiz_cmd))
    app.add_handler(CommandHandler("leaderboard", leaderboard_cmd))
    
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.POLL, handle_poll_forward))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_messages))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(PollAnswerHandler(receive_poll_answer))

    print("Bot is starting...")
    app.run_polling()
