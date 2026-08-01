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
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, PollAnswerHandler, filters, ContextTypes
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

def clean_extracted_text(text):
    text = re.sub(r'\s+', ' ', text)
    return text[:1500].strip()

# PDF Text Se Questions Banane Ka Function
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
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logging.error(f"Error generating PDF quiz: {e}")
        return None

# General AI Quiz Function
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

# Poll Send Karne Ka Function
async def send_next_question(chat_id, context):
    if chat_id not in chat_sessions or not chat_sessions[chat_id]["active"]:
        return

    session = chat_sessions[chat_id]
    q_data = None
    
    if chat_id in chat_pdf_text and chat_pdf_text[chat_id]:
        q_data = generate_quiz_from_text(chat_pdf_text[chat_id])
    
    # Fallback to general AI quiz if PDF fails or doesn't exist
    if not q_data:
        q_data = generate_ai_quiz(session.get("topic", "Rajasthan GK"))

    if not q_data:
        await asyncio.sleep(2)
        return await send_next_question(chat_id, context)

    q_text = f"{q_data['question']}\n\n📢 @dailyquiz_manish"
    exp_text = f"{q_data.get('explanation', 'सही उत्तर चुनें!')}\n\n👉 Join: @dailyquiz_manish"

    try:
        poll_msg = await context.bot.send_poll(
            chat_id=chat_id,
            question=q_text,
            options=q_data["options"],
            type="quiz",
            correct_option_id=int(q_data["answer_index"]),
            explanation=exp_text,
            open_period=15,
            is_anonymous=False
        )

        active_polls[poll_msg.poll.id] = {
            "chat_id": chat_id,
            "handled": False
        }

        asyncio.create_task(auto_timeout_next(poll_msg.poll.id, chat_id, context))

    except Exception as e:
        logging.error(f"Error sending poll: {e}")

async def auto_timeout_next(poll_id, chat_id, context):
    await asyncio.sleep(16)
    if poll_id in active_polls and not active_polls[poll_id]["handled"]:
        active_polls[poll_id]["handled"] = True
        await send_next_question(chat_id, context)

# PDF Handler
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
            
            await msg.edit_text("✅ *PDF Read हो गई!* अब `/pdfquiz` टाइप करके इस PDF से क्विज़ स्टार्ट करें।", parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Error reading PDF: {e}")
            await msg.edit_text("❌ PDF पढ़ने में समस्या आई।")
    else:
        await update.message.reply_text("❌ कृपया सिर्फ PDF फाइल ही भेजें।")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Rajasthan Exam Quiz Bot**\n\n"
        "• `/quiz` - Normal AI Quiz स्टार्ट करें\n"
        "• **PDF से क्विज़:** PDF भेजें और `/pdfquiz` लिखें।\n"
        "• `/stop` - चालू क्विज़ को रोकें\n"
        "• `/clearpdf` - Saved PDF हटाएं", 
        parse_mode="Markdown"
    )

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    topic = " ".join(context.args) if context.args else "Mixed Exam Special"
    chat_sessions[chat_id] = {"active": True, "topic": topic}
    
    if chat_id in chat_pdf_text:
        del chat_pdf_text[chat_id]

    await update.message.reply_text("🚀 **Normal AI Quiz Started!**")
    await send_next_question(chat_id, context)

async def pdfquiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in chat_pdf_text or not chat_pdf_text[chat_id]:
        await update.message.reply_text("❌ पहले कोई PDF फाइल सेंड करें!")
        return

    chat_sessions[chat_id] = {"active": True, "topic": "PDF Based"}
    await update.message.reply_text("📄 **PDF Quiz Started!**")
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

async def receive_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    poll_id = update.poll_answer.poll_id
    if poll_id in active_polls and not active_polls[poll_id]["handled"]:
        active_polls[poll_id]["handled"] = True
        chat_id = active_polls[poll_id]["chat_id"]
        await send_next_question(chat_id, context)

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("pdfquiz", pdfquiz))
    app.add_handler(CommandHandler("clearpdf", clearpdf))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(PollAnswerHandler(receive_poll_answer))

    print("Bot is starting...")
    app.run_polling()
    
