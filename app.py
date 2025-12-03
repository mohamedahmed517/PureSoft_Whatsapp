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
from collections import defaultdict
import google.generativeai as genai
from flask import Flask, request, jsonify
from google.generativeai.types import HarmCategory, HarmBlockThreshold

load_dotenv()
app = Flask(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY مطلوب!")

HISTORY_FILE = "/data/history.json"
os.makedirs("/data", exist_ok=True)
conversation_history = defaultdict(list)

if os.path.exists(HISTORY_FILE):
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
            conversation_history = defaultdict(list, {str(k): v for k, v in loaded.items()})
        print(f"تم تحميل {len(conversation_history)} محادثة قديمة")
    except:
        pass

def save_history():
    while True:
        time.sleep(60)
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(dict(conversation_history), f, ensure_ascii=False, indent=2)
        except:
            pass
threading.Thread(target=save_history, daemon=True).start()

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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
csv_path = os.path.join(BASE_DIR, 'products.csv')
CSV_DATA = pd.read_csv(csv_path)

def gemini_chat(text="", image_b64=None, user_key="unknown"):
    try:
        if len(conversation_history[user_key]) == 0:
            return "أهلاً وسهلاً! أنا البوت الذكي بتاع آفاق ستورز 👋\nإزيك؟ تحب أساعدك في إيه النهاردة؟"

        try:
            ip = request.headers.get("X-Forwarded-For", request.remote_addr or "127.0.0.1").split(",")[0].strip()
            location = "cairo"
            temp = "25"
            if not ip.startswith(("10.", "172.", "192.168.", "127.")):
                r = requests.get(f"https://ipwho.is/{ip}", timeout=3).json()
                if r.get("city"):
                    location = r["city"]
                    w = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={r['latitude']}&longitude={r['longitude']}&daily=temperature_2m_max", timeout=3).json()
                    temp = str(round(w["daily"]["temperature_2m_max"][0])) if w.get("daily") else "25"
        except:
            location, temp = "cairo", "25"

        history_text = "\n".join([
            f"{'العميل' if e['role']=='user' else 'البوت'}: {e['text'][:100]}"
            for e in conversation_history[user_key][-10:]
        ])

        products_short = "\n".join([
            f"• {row['product_name_ar']} | {row['sell_price']} جنيه | https://afaq-stores.com/product-details/{row['product_id']}"
            for _, row in CSV_DATA.head(30).iterrows()
        ])

        prompt = f"""أنت البوت الذكي بتاع آفاق ستورز، بتتكلم عامية مصرية ودودة.
لو سألك "إنت مين؟" قوله: أيوه أنا البوت الذكي بتاع آفاق ستورز.

العميل في {location} والجو حوالي {temp}°C

آخر كلام:
{history_text}

المنتجات المتاحة (اختر منهم بس):
{products_short}

العميل بيقول: {text or "بعت صورة"}

لو صورة → قوله "ثانية بس أشوف الصورة..."
لو طلب حاجة → رشحله من المنتجات بالشكل ده:
تيشيرت قطن أبيض
السعر: 130 جنيه
اللينك: https://afaq-stores.com/product-details/123

رد دلوقتي بالعامية المصرية ومتستخدمش إيموجي كتير.""".strip()

        if image_b64:
            img = Image.open(io.BytesIO(base64.b64decode(image_b64)))
            response = MODEL.generate_content([prompt, img], stream=False)
        else:
            response = MODEL.generate_content(prompt, stream=False)

        reply = response.text.strip() if response and hasattr(response, "text") and response.text else "ثواني بس وأرجعلك..."

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        conversation_history[user_key].extend([
            {"role": "user", "text": text or "[صورة]", "time": now},
            {"role": "assistant", "text": reply, "time": now}
        ])
        if len(conversation_history[user_key]) > 200:
            conversation_history[user_key] = conversation_history[user_key][-200:]

        return reply

    except Exception as e:
        print(f"Gemini Error: {e}")
        return "ثواني بس، فيه مشكلة صغيرة وهرجعلك حالا..."
        
def download_media(media_id):
    try:
        url = f"https://graph.facebook.com/v20.0/{media_id}"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        j = requests.get(url, headers=headers, timeout=10).json()
        data = requests.get(j["url"], headers=headers, timeout=30).content
        return base64.b64encode(data).decode()
    except:
        return None

def send_whatsapp(to, text):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID: return
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text[:4000]}}
    requests.post(url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}, json=payload, timeout=10)

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
                user_key = f"whatsapp:{from_num}"

                if msg["type"] == "text":
                    reply = gemini_chat(msg["text"]["body"], user_key=user_key)
                elif msg["type"] == "image":
                    b64 = download_media(msg["image"]["id"])
                    reply = gemini_chat("بعت صورة", image_b64=b64, user_key=user_key)
                elif msg["type"] in ["audio", "voice"]:
                    b64 = download_media(msg["audio"]["id"])
                    if b64:
                        audio_file = io.BytesIO(base64.b64decode(b64))
                        audio_file.name = "voice.ogg"
                        reply = MODEL.generate_content(["اسمع الريكورد ده ورد بالعامية المصرية", audio_file]).text
                    else:
                        reply = "الصوت مش واضح"
                else:
                    reply = "ابعت نص أو صورة"

                send_whatsapp(from_num, reply)
    return "OK", 200

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    update = request.get_json()
    if not update or "message" not in update: return jsonify(success=True), 200

    msg = update["message"]
    chat_id = msg["chat"]["id"]
    user_id = str(msg["from"]["id"])
    user_key = f"telegram:{user_id}"

    if "text" in msg:
        reply = gemini_chat(msg["text"], user_key=user_key)
    elif "photo" in msg:
        file_id = msg["photo"][-1]["file_id"]
        file_info = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}").json()
        if file_info.get("ok"):
            path = file_info["result"]["file_path"]
            url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{path}"
            img_data = requests.get(url).content
            b64 = base64.b64encode(img_data).decode()
            reply = gemini_chat("بعت صورة", image_b64=b64, user_key=user_key)
        else:
            reply = "مش قادر أشوف الصورة"
    elif "voice" in msg or "audio" in msg:
        voice = msg.get("voice") or msg.get("audio")
        file_id = voice["file_id"]
        file_info = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}").json()
        if file_info.get("ok"):
            path = file_info["result"]["file_path"]
            url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{path}"
            audio_data = requests.get(url).content
            audio_io = io.BytesIO(audio_data)
            audio_io.name = "voice.ogg"
            reply = MODEL.generate_content(["اسمع الريكورد ده ورد بالعامية المصرية", audio_io]).text
        else:
            reply = "الصوت مش واضح"
    else:
        reply = "ابعت نص أو صورة أو صوت"

    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                  json={"chat_id": chat_id, "text": reply})
    return jsonify(success=True), 200

@app.route("/")
def home():
    if TELEGRAM_TOKEN:
        webhook_url = f"https://{request.host}/telegram"
        set_result = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={webhook_url}"
        ).json()
        status = "نجح" if set_result.get("ok") else "فشل"
        return f"<h1>بوت آفاق ستورز شغال 100%!</h1><p>Telegram Webhook: {status}</p>"
    return "<h1>البوت شغال!</h1>"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)



