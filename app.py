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
from flask import Flask, request
import google.generativeai as genai
from collections import defaultdict
from google.generativeai.types import HarmCategory, HarmBlockThreshold

load_dotenv()

app = Flask(__name__)

# ==================== History ====================
HISTORY_FILE = "history.json"
SAVE_INTERVAL = 60

try:
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        loaded_history = json.load(f)
        conversation_history = defaultdict(list, {str(k): v for k, v in loaded_history.items()})
    print(f"تم تحميل {len(conversation_history)} محادثة من history.json")
except FileNotFoundError:
    conversation_history = defaultdict(list)

def save_history_background():
    while True:
        time.sleep(SAVE_INTERVAL)
        try:
            temp_dict = dict(conversation_history)
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(temp_dict, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"خطأ في حفظ الـ history: {e}")

threading.Thread(target=save_history_background, daemon=True).start()

# ==================== Gemini Setup ====================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY مش موجود في .env")

genai.configure(api_key=GEMINI_API_KEY)

safety_settings = [
    {"category": HarmCategory.HARM_CATEGORY_HARASSMENT, "threshold": HarmBlockThreshold.BLOCK_NONE},
    {"category": HarmCategory.HARM_CATEGORY_HATE_SPEECH, "threshold": HarmBlockThreshold.BLOCK_NONE},
    {"category": HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, "threshold": HarmBlockThreshold.BLOCK_NONE},
    {"category": HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, "threshold": HarmBlockThreshold.BLOCK_NONE},
]

MODEL = genai.GenerativeModel(
    'gemini-2.0-flash',
    generation_config={"temperature": 0.85, "max_output_tokens": 2048},
    safety_settings=safety_settings
)

# ==================== Load CSV ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
csv_path = os.path.join(BASE_DIR, 'products.csv')
CSV_DATA = pd.read_csv(csv_path)

# ==================== WhatsApp Config ====================
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "afaq_whatsapp_only_2025")

# ==================== Helper: Download Media ====================
def download_media(media_id):
    url = f"https://graph.facebook.com/v20.0/{media_id}"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        media_url = r.json().get("url")
        if not media_url:
            return None
        media_data = requests.get(media_url, headers=headers, timeout=30).content
        return base64.b64encode(media_data).decode('utf-8')
    except Exception as e:
        print(f"Media download error: {e}")
        return None

# ==================== Send WhatsApp Message ====================
def send_whatsapp_message(to_number, text):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        print("WhatsApp credentials missing!")
        return
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": text[:4000]}
    }
    try:
        requests.post(url, headers=headers, json=payload, timeout=10)
    except Exception as e:
        print(f"Send failed: {e}")

# ==================== Gemini Chat ====================
def gemini_chat(user_message="", image_b64=None, from_number="unknown"):
    try:
        location = {"city": "القاهرة", "lat": 30.04, "lon": 31.23}
        try:
            ip = request.headers.get("X-Forwarded-For", "127.0.0.1").split(",")[0].strip()
            if not ip.startswith(("10.", "172.", "192.168.", "127.")):
                r = requests.get(f"https://ipwho.is/{ip}", timeout=5)
                data = r.json()
                if data.get("city"):
                    location = {"city": data["city"], "lat": data["latitude"], "lon": data["longitude"]}
        except:
            pass

        today_temp = 25
        try:
            weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={location['lat']}&longitude={location['lon']}&daily=temperature_2m_max,temperature_2m_min"
            w = requests.get(weather_url, timeout=5).json()
            today_temp = round((w["daily"]["temperature_2m_max"][0] + w["daily"]["temperature_2m_min"][0]) / 2, 1)
        except:
            pass

        products_text = ""
        for _, row in CSV_DATA.iterrows():
            name = str(row['product_name_ar']).strip()
            price = float(row['sell_price'])
            cat = str(row['category']).strip()
            pid = str(row['product_id'])
            products_text += f"• {name} | السعر: {price} جنيه | الكاتيجوري: {cat} | اللينك: https://afaq-stores.com/product-details/{pid}\n"

        history_lines = ""
        for entry in conversation_history[from_number][-50:]:
            if isinstance(entry, dict):
                time_str = entry.get("time", "")
                role = "العميل" if entry["role"] == "user" else "أحمد"
                text = entry["text"]
                history_lines += f"{time_str} - {role}: {text}\n"
            else:
                history_lines += f"{entry[0]}: {entry[1]}\n"

        full_message = f"""
أنت شاب مصري اسمه «أحمد»، بتتكلم عامية مصرية طبيعية جدًا وودودة، بتحب الموضة والعناية الشخصية وبتعرف تحلل الصور كويس.

الجو في {location["city"]} النهاردة حوالي {today_temp}°C

دول كل المنتجات اللي موجودة عندنا دلوقتي:
{products_text}

آخر رسايل المحادثة:
{history_lines}

العميل بيقول دلوقتي: {user_message or "بعت صورة"}

لو طلب لبس أو بعت صورة لبس أو منتج → رشحله من المنتجات بالشكل ده بالظبط:
تيشيرت قطن سادة ابيض
السعر: 130 جنيه
الكاتيجوري: لبس صيفي
اللينك: https://afaq-stores.com/product-details/1019

مهم جدًا: استخدم أسماء المنتجات زي ما هي من غير تغيير ولا حرف.

لو بعت صورة عادية → ابدأ بـ "ثانية بس أشوف الصورة..."

رد دلوقتي بالعامية المصرية 100% ومتحطش إيموجي ومتقولش إنك بوت.
""".strip()

        if image_b64:
            img_bytes = base64.b64decode(image_b64)
            img = Image.open(io.BytesIO(img_bytes))
            response = MODEL.generate_content([full_message, img])
        else:
            response = MODEL.generate_content(full_message)

        reply = response.text.strip() if response and hasattr(response, "text") and response.text else "ثواني بس، فيه حاجة غلط، جرب تاني"

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        conversation_history[from_number].append({"role": "user", "text": user_message or "[صورة]", "time": now})
        conversation_history[from_number].append({"role": "assistant", "text": reply, "time": now})

        if len(conversation_history[from_number]) > 200:
            conversation_history[from_number] = conversation_history[from_number][-200:]

        return reply

    except Exception as e:
        print(f"Gemini Error: {e}")
        return "ثواني بس وأرجعلك…"

def gemini_chat_audio(audio_file, from_number):
    try:
        products_text = "\n".join(
            f"• {row['product_name_ar']} | السعر: {row['sell_price']} جنيه | اللينك: https://afaq-stores.com/product-details/{row['product_id']}"
            for _, row in CSV_DATA.iterrows()
        )

        full_message = f"""
أنت أحمد، شاب مصري بتتكلم عامية مصرية ودودة جدًا.
المنتجات عندنا: {products_text}
العميل بعتلك ريكورد صوتي → اسمع كويس ورد عليه زي لو كاتب الكلام بالظبط، عامية مصرية 100% بدون إيموجي.
"""

        response = MODEL.generate_content([full_message, audio_file])
        reply = response.text.strip() if response and response.text else "الريكورد مجاش واضح، ابعته تاني"

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        conversation_history[from_number].append({"role": "user", "text": "[ريكورد صوتي]", "time": now})
        conversation_history[from_number].append({"role": "assistant", "text": reply, "time": now})

        if len(conversation_history[from_number]) > 200:
            conversation_history[from_number] = conversation_history[from_number][-200:]

        return reply

    except Exception as e:
        print(f"Audio error: {e}")
        return "الريكورد مجاش واضح، ابعته تاني"

# ==================== Routes ====================
@app.route("/")
def home():
    return "واتس آب شغال 100%"

@app.route("/webhook", methods=["GET"])
def webhook_verify():
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == WEBHOOK_VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def webhook_receive():
    data = request.get_json()
    if not data or not data.get("entry"):
        return "OK", 200

    try:
        msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
        from_number = msg["from"]
        msg_type = msg["type"]

        if msg_type == "text":
            reply = gemini_chat(msg["text"]["body"], from_number=from_number)

        elif msg_type == "image":
            image_id = msg["image"]["id"]
            image_b64 = download_media(image_id)
            reply = gemini_chat("بعت صورة", image_b64, from_number)

        elif msg_type in ["audio", "voice"]:
            audio_id = msg["audio"]["id"]
            audio_b64 = download_media(audio_id)
            if audio_b64:
                audio_file = io.BytesIO(base64.b64decode(audio_b64))
                audio_file.name = "voice.ogg"
                reply = gemini_chat_audio(audio_file, from_number)
            else:
                reply = "الريكورد ما وصلش كويس، ابعته تاني"

        elif msg_type == "video":
            reply = gemini_chat("ده فيديو حضرتك، ثانية أشوفه...", from_number=from_number)

        elif msg_type == "document":
            filename = msg["document"].get("filename", "مستند")
            reply = gemini_chat(f"ده مستند اسمه {filename}، ثانية أقراه...", from_number=from_number)

        else:
            reply = gemini_chat("انا مفهمتش الي انت باعته .. وضحلي الامور اكتر", from_number=from_number)

        send_whatsapp_message(from_number, reply)

    except Exception as e:
        print(f"Webhook Error: {e}")

    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
