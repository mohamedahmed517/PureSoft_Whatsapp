import os
import io
import time
import json
import base64
import requests
import threading
import pandas as pd
from PIL import Image
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify
import google.generativeai as genai
from collections import defaultdict
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import logging

logging.basicConfig(level=logging.INFO)
load_dotenv()
app = Flask(__name__)

# ==================== المتغيرات ====================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "afaq_whatsapp_only_2025")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8319255971:AAH5Hs8jrXcE9YGJvEi7TB9NTFJ16sQVYtk")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY مطلوب!")

# ==================== History ====================
HISTORY_FILE = "history.json"
conversation_history = defaultdict(list)

try:
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        conversation_history = defaultdict(list, {str(k): v for k, v in json.load(f).items()})
except:
    pass

def save_history():
    while True:
        time.sleep(60)
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(dict(conversation_history), f, ensure_ascii=False, indent=2)
        except: pass
threading.Thread(target=save_history, daemon=True).start()

# ==================== Gemini Setup (تم تصليح الـ safety_settings) ====================
genai.configure(api_key=GEMINI_API_KEY)

MODEL = genai.GenerativeModel(
    'gemini-1.5-flash',
    generation_config={"temperature": 0.9, "max_output_tokens": 2048},
    safety_settings=[
        {"category": HarmCategory.HARM_CATEGORY_HARASSMENT, "threshold": HarmBlockThreshold.BLOCK_NONE},
        {"category": HarmCategory.HARM_CATEGORY_HATE_SPEECH, "threshold": HarmBlockThreshold.BLOCK_NONE},
        {"category": HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, "threshold": HarmBlockThreshold.BLOCK_NONE},
        {"category": HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, "threshold": HarmBlockThreshold.BLOCK_NONE},
    ]
)

# ==================== Load Products ====================
try:
    CSV_DATA = pd.read_csv('products.csv')
except:
    CSV_DATA = pd.DataFrame()  # لو الملف مش موجود

# ==================== Gemini Chat ====================
def gemini_chat(text="", image_b64=None, user_id="unknown"):
    try:
        products = "\n".join(
            f"• {r['product_name_ar']} | {r['sell_price']} جنيه | https://afaq-stores.com/product-details/{r['product_id']}"
            for _, r in CSV_DATA.iterrows()
        ) if not CSV_DATA.empty else "مش لاقي منتجات دلوقتي"

        history = "\n".join(
            f"{e.get('time','')} - {'العميل' if e['role']=='user' else 'البوت'}: {e['text']}"
            for e in conversation_history[user_id][-40:]
        )

        prompt = f"""
أنا بوت ذكي من آفاق ستورز، بتكلم عامية مصرية ودودة.
المنتجات: {products}
آخر رسايل: {history}
العميل قال: {text or "بعت صورة"}
- لو صورة → ابدأ بـ "ثانية بس أشوف الصورة..."
- لو طلب → رشح منتج من القايمة كده:
تيشيرت قطن سادة
السعر: 150 جنيه
اللينك: https://afaq-stores.com/product-details/123
- متستخدمش إيموجي
رد دلوقتي بالعامية المصرية:
""".strip()

        if image_b64:
            img = Image.open(io.BytesIO(base64.b64decode(image_b64)))
            resp = MODEL.generate_content([prompt, img])
        else:
            resp = MODEL.generate_content(prompt)

        reply = (resp.text or "ثواني وأرجعلك...").strip()

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        conversation_history[user_id].extend([
            {"role": "user", "text": text or "[صورة]", "time": now},
            {"role": "assistant", "text": reply, "time": now}
        ])
        if len(conversation_history[user_id]) > 200:
            conversation_history[user_id] = conversation_history[user_id][-200:]

        return reply
    except Exception as e:
        print("Gemini Error:", e)
        return "ثواني وأرجعلك..."

# ==================== WhatsApp ====================
def download_media(media_id):
    try:
        url = f"https://graph.facebook.com/v20.0/{media_id}"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        j = requests.get(url, headers=headers, timeout=10).json()
        data = requests.get(j["url"], headers=headers, timeout=30).content
        return base64.b64encode(data).decode()
    except Exception as e:
        print("Download error:", e)
        return None

def send_whatsapp(to, text):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID: return
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text[:4000]}}
    try:
        requests.post(url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}, json=payload, timeout=10)
    except: pass

@app.route("/whatsapp", methods=["GET", "POST"])
def whatsapp_webhook():
    if request.method == "GET":
        if request.args.get("hub.verify_token") == WEBHOOK_VERIFY_TOKEN:
            return request.args.get("hub.challenge")
        return "Forbidden", 403

    data = request.get_json()
    if not data or "entry" not in data: return "OK", 200

    for entry in data["entry"]:
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                from_num = msg["from"]
                if msg["type"] == "text":
                    reply = gemini_chat(msg["text"]["body"], from_number=from_num)
                elif msg["type"] == "image":
                    b64 = download_media(msg["image"]["id"])
                    reply = gemini_chat("بعت صورة", b64, from_num)
                elif msg["type"] in ["audio", "voice"]:
                    b64 = download_media(msg["audio"]["id"])
                    if b64:
                        audio_file = io.BytesIO(base64.b64decode(b64))
                        audio_file.name = "voice.ogg"
                        reply = MODEL.generate_content(["اسمع الريكورد ده ورد بالعامية المصرية", audio_file]).text
                    else:
                        reply = "الصوت مش واضح"
                else:
                    reply = "مش فاهم، ابعت نص أو صورة"
                send_whatsapp(from_num, reply)
    return "OK", 200

# ==================== Telegram Webhook ====================
@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    update = request.get_json()
    if not update or "message" not in update: return jsonify({}), 200

    msg = update["message"]
    user_id = str(msg["from"]["id"])
    chat_id = msg["chat"]["id"]

    if "text" in msg:
        reply = gemini_chat(msg["text"], from_number=user_id)
    elif "photo" in msg:
        file_id = msg["photo"][-1]["file_id"]
        file_info = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}").json()
        if file_info.get("ok"):
            file_path = file_info["result"]["file_path"]
            file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
            img_data = requests.get(file_url).content
            b64 = base64.b64encode(img_data).decode()
            reply = gemini_chat("بعت صورة", b64, user_id)
        else:
            reply = "مش قادر أشوف الصورة"
    elif "voice" in msg or "audio" in msg:
        voice = msg.get("voice") or msg.get("audio")
        file_id = voice["file_id"]
        file_info = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}").json()
        if file_info.get("ok"):
            file_path = file_info["result"]["file_path"]
            file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
            audio_data = requests.get(file_url).content
            audio_io = io.BytesIO(audio_data)
            audio_io.name = "voice.ogg"
            reply = MODEL.generate_content(["اسمع الريكورد ده ورد بالعامية المصرية", audio_io]).text
        else:
            reply = "الصوت مش واضح"
    else:
        reply = "ابعت نص أو صورة أو صوت"

    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                  json={"chat_id": chat_id, "text": reply})
    return jsonify({}), 200

# ==================== Home + Set Webhook ====================
@app.route("/")
def home():
    if TELEGRAM_TOKEN:
        webhook_url = request.url_root.rstrip("/") + "/telegram"
        set_webhook = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={webhook_url}"
        ).json()
        status = "نجح" if set_webhook.get("ok") else "فشل"
        return f"<h1>بوت آفاق ستورز شغال 100%!</h1><p>Telegram Webhook: {status}</p><pre>{set_webhook}</pre>"
    return "<h1>البوت شغال!</h1>"

# ==================== Run ====================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
