import os
import io
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, Poll
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    PollAnswerHandler,
    ContextTypes,
    filters,
)
import pypdf
from groq import Groq

# --- LOGGING ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# --- WEB SERVER FOR KOYEB ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

    def log_message(self, format, *args):
        return

def run_health_check_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_check_server, daemon=True).start()

# --- ENVIRONMENT VARIABLES ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# --- DATA STORES ---
poll_answers_store = {}
user_scores = {}
last_questions = {}

# --- COMMAND HANDLERS ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "<b>📄 Advanced PDF & Text Quiz Bot</b>\n\n"
        "• मुझे कोई भी <b>PDF या Text फाइल</b> भेजें!\n"
        "• AI फाइल से सवाल पढ़कर तुरंत क्विज़ शुरू कर देगा।\n"
        "• AI पढ़ने के बाद फाइल <b>Auto Delete</b> हो जाएगी।\n\n"
        "<b>⚡ फीचर्स:</b>\n"
        "⏱️ 15-सेकंड ऑटो-टाइमर\n"
        "❌ 1/3 नेगेटिव मार्किंग (-0.33 points)\n"
        "💡 व्याख्या (Explanation) हर उत्तर पर\n"
        "✏️ प्रश्न सुधारने के लिए <code>/edit</code> चलाएं"
    )
    await update.message.reply_text(welcome_text, parse_mode="HTML")

# --- AI PARSER FOR LARGE FILES ---
def extract_questions_with_ai(text_chunk):
    prompt = (
        "Extract or generate multiple choice questions in Hindi from this text. "
        "Strictly return a JSON Array of objects with this structure: "
        '[{"question": "प्रश्न...", "options": ["A", "B", "C", "D"], "answer_index": 0, "explanation": "व्याख्या..."}] '
        "Text Content:\n" + text_chunk[:4000]
    )

    try:
        response = client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            timeout=15.0,
            response_format={"type": "json_object"}
        )
        res_data = json.loads(response.choices[0].message.content)
        if isinstance(res_data, list):
            return res_data
        elif "questions" in res_data:
            return res_data["questions"]
        else:
            return [res_data]
    except Exception as e:
        logging.error(f"Groq Extraction Error: {e}")
        return []

# --- DOCUMENT HANDLER ---
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    file_name = doc.file_name.lower()

    if not (file_name.endswith('.pdf') or file_name.endswith('.txt')):
        await update.message.reply_text("⚠️ कृपया केवल <b>.pdf</b> या <b>.txt</b> फाइल ही भेजें।", parse_mode="HTML")
        return

    bot_username = (await context.bot.get_me()).username
    msg = await update.message.reply_text("⏳ बड़ी फाइल का विश्लेषण किया जा रहा है... AI प्रश्न तैयार कर रहा है...")

    try:
        file = await doc.get_file()
        file_bytes = await file.download_as_bytearray()

        text_content = ""
        if file_name.endswith('.pdf'):
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            for page in reader.pages[:15]:
                text_content += page.extract_text() + "\n"
        else:
            text_content = file_bytes.decode('utf-8', errors='ignore')

        del file_bytes

        questions = extract_questions_with_ai(text_content)
        del text_content

        if not questions:
            await msg.edit_text("❌ फाइल में से प्रश्न नहीं निकाले जा सके। कृपया फाइल का फॉर्मेट चेक करें।")
            return

        await msg.edit_text(f"✅ {len(questions)} प्रश्न तैयार हैं! फाइल ऑटो-डिलीट हो गई है। क्विज़ शुरू हो रही है...\n")

        for q in questions:
            exp_text = f"💡 {q.get('explanation', 'सही उत्तर चुनें')}\n\n🤖 Quiz Bot: @{bot_username}"
            
            sent_poll = await context.bot.send_poll(
                chat_id=update.effective_chat.id,
                question=q["question"],
                options=q["options"],
                type=Poll.QUIZ,
                correct_option_id=q["answer_index"],
                explanation=exp_text,
                open_period=15,
                is_anonymous=False
            )

            poll_answers_store[sent_poll.poll.id] = {
                "correct_option": q["answer_index"],
                "chat_id": update.effective_chat.id
            }
            last_questions[update.effective_chat.id] = q

    except Exception as e:
        logging.error(f"Error: {e}")
        await msg.edit_text("❌ फाइल प्रोसेस करने में दिक्कत आई।")

# --- EDIT COMMAND ---
async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in last_questions:
        await update.message.reply_text("❌ एडिट करने के लिए हाल ही का कोई प्रश्न नहीं मिला।")
        return

    args = " ".join(context.args).split("|")
    if len(args) < 6:
        await update.message.reply_text(
            "✏️ <b>प्रश्न एडिट करने का तरीका:</b>\n\n"
            "<code>/edit नया प्रश्न? | ऑप्शन A | ऑप्शन B | ऑप्शन C | ऑप्शन D | 1</code>",
            parse_mode="HTML"
        )
        return

    q_text = args[0].strip()
    opts = [args[1].strip(), args[2].strip(), args[3].strip(), args[4].strip()]
    correct_idx = int(args[5].strip())

    bot_username = (await context.bot.get_me()).username
    exp_text = f"💡 एडिटेड प्रश्न\n\n🤖 Quiz Bot: @{bot_username}"

    await context.bot.send_poll(
        chat_id=chat_id,
        question=q_text,
        options=opts,
        type=Poll.QUIZ,
        correct_option_id=correct_idx,
        explanation=exp_text,
        open_period=15,
        is_anonymous=False
    )
    await update.message.reply_text("✅ नया संशोधित (Edited) प्रश्न भेज दिया गया है!")

# --- NEGATIVE MARKING ---
async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    poll_id = answer.poll_id
    user_id = answer.user.id
    user_name = answer.user.first_name

    if poll_id not in poll_answers_store:
        return

    selected_option = answer.option_ids[0] if answer.option_ids else None
    correct_option = poll_answers_store[poll_id]["correct_option"]

    if user_id not in user_scores:
        user_scores[user_id] = {"name": user_name, "score": 0.0}

    if selected_option == correct_option:
        user_scores[user_id]["score"] += 1.0
    else:
        user_scores[user_id]["score"] -= 0.33

# --- MAIN RUNNER ---
def main():
    if not BOT_TOKEN:
        print("BOT_TOKEN Missing!")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("edit", edit_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(PollAnswerHandler(handle_poll_answer))

    print("Bot Started...")
    app.run_polling()

if __name__ == "__main__":
    main()
            
