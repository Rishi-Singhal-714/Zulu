import os, json, csv, random, re, requests
from pathlib import Path
from flask import Flask, request, jsonify
import openai

########## ENV LOAD ##########
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GALLABOX_API_KEY = os.getenv("GALLABOX_API_KEY", "")
GALLABOX_API_SECRET = os.getenv("GALLABOX_API_SECRET", "")
GALLABOX_CHANNEL_ID = os.getenv("GALLABOX_CHANNEL_ID", "")
GALLABOX_API_URL = "https://backend.gallabox.com/api/v1/messages/whatsapp"

if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY
else:
    print("⚠️  OpenAI API key not found")

app = Flask(__name__)

# Enable CORS for all routes
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

########## PERSISTENCE ##########
conversations = {}

########## ZULU CLUB INFO ##########
ZULU_CLUB_INFO = """
We're building a new way to shop and discover lifestyle products online.

We all love visiting a premium store — exploring new arrivals, discovering chic home pieces, finding stylish outfits, or picking adorable toys for kids. But we know making time for mall visits isn't always easy.

Introducing Zulu Club — your personalized lifestyle shopping experience, delivered right to your doorstep.

Browse and shop high-quality lifestyle products across categories you love:

- Women's Fashion — dresses, tops, co-ords, winterwear, loungewear & more
- Men's Fashion — shirts, tees, jackets, athleisure & more
- Kids — clothing, toys, learning kits & accessories
- Footwear — sneakers, heels, flats, sandals & kids shoes
- Home Decor — showpieces, vases, lamps, aroma decor, premium home accessories
- Beauty & Self-Care — skincare, bodycare, fragrances & grooming essentials
- Fashion Accessories — bags, jewelry, watches, sunglasses & belts
- Lifestyle Gifting — curated gift sets & décor-based gifting

Your selection arrives in just 100 minutes. Try at home, keep what you love, return instantly — smooth, personal, and stress-free.

Now live in Gurgaon — Visit zulu.club or our pop-ups at AIPL Joy Street & AIPL Central.
"""

########## GPT CHAT ##########
def get_chatgpt_response(user_message, conversation_history=None, company_info=ZULU_CLUB_INFO):
    if not OPENAI_API_KEY:
        return "Hello! I'm here to help you with Zulu Club. Please visit zulu.club."

    try:
        system_message = f"""
You are a friendly customer service assistant for Zulu Club.

Use ONLY the following information:
{company_info}

Guidelines:
1. Be helpful, concise, and friendly.
2. Highlight 100-minute delivery, try-at-home, easy returns, and premium curation.
3. If someone says hi/hello, greet them warmly and introduce Zulu Club.
4. If they ask about products, use the product category logic to show them.
5. If something is not in the info, politely say you're not sure.
6. Mention we're available in Gurgaon and at pop-ups: AIPL Joy Street & AIPL Central.
7. Never invent details beyond the provided info.
"""

        messages = [{"role": "system", "content": system_message}]
        if conversation_history:
            messages += conversation_history[-6:]
        messages.append({"role": "user", "content": user_message})

        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        res = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=350,
            temperature=0.7
        )
        return res.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ GPT Error: {e}")
        return "Hey there! Welcome to Zulu Club — your premium lifestyle shopping experience with 100-minute delivery."

########## CATEGORY DETECTION ##########
_products, _categories, _category_index = [], set(), {}

def _canonicalize(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower().strip())) if text else ""

def _load_products():
    global _products, _categories, _category_index
    try:
        # For Vercel, the CSV should be in the same directory as the Python file
        import os
        current_dir = os.path.dirname(os.path.abspath(__file__))
        csv_path = os.path.join(current_dir, "products.csv")
        
        if not os.path.exists(csv_path):
            print("⚠️ products.csv not found at:", csv_path)
            # Try alternative path
            csv_path = "products.csv"
            
        with open(csv_path, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for r in reader:
                name = r.get("name", "").strip()
                category = r.get("category", "").strip()
                price = r.get("price", "").strip()
                image_url = r.get("image_url", "").strip()
                if not name or not category:
                    continue
                if price and not price.startswith("₹"):
                    price = f"₹{price}"
                _products.append({"name": name, "category": category, "price": price, "image_url": image_url})
        
        _categories.update(_canonicalize(p["category"]) for p in _products)
        for p in _products:
            key = _canonicalize(p["category"])
            _category_index.setdefault(key, []).append(p)
        print(f"✅ Loaded {len(_products)} products across {len(_categories)} categories.")
    except Exception as e:
        print(f"❌ Error loading products: {e}")

def detect_category_with_gpt(user_message):
    try:
        if not _categories:
            return None
        categories = list(_categories)
        prompt = f"""
Given this message, return ONLY the matching category from this list.
Message: "{user_message}"
Categories: {categories}
If none match, respond 'none'.
"""
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        res = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": prompt}],
            max_tokens=15,
            temperature=0
        )
        category = res.choices[0].message.content.strip().lower()
        if category == "none": return None
        for c in _categories:
            if category in c or c in category:
                return c
        return None
    except Exception as e:
        print("⚠️ Category GPT Error:", e)
        return None

def get_random_products(cat, n=3):
    items = _category_index.get(cat, [])
    return random.sample(items, min(n, len(items))) if items else []

# Load products on startup
_load_products()

########## SEND MESSAGE VIA GALLABOX API ##########
def send_whatsapp_message(phone, message_text):
    """Send a text message to user via Gallabox API"""
    if not all([GALLABOX_API_KEY, GALLABOX_API_SECRET, GALLABOX_CHANNEL_ID]):
        print("❌ Gallabox credentials missing")
        return False
        
    headers = {
        "Content-Type": "application/json",
        "x-api-key": GALLABOX_API_KEY,
        "x-api-secret": GALLABOX_API_SECRET
    }
    payload = {
        "channelId": GALLABOX_CHANNEL_ID,
        "to": phone,
        "type": "text",
        "message": {"text": message_text}
    }
    try:
        r = requests.post(GALLABOX_API_URL, headers=headers, json=payload, timeout=10)
        print("📤 Gallabox send response:", r.status_code, r.text)
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Gallabox send error: {e}")
        return False

def send_whatsapp_image(phone, image_url, caption):
    """Send image message via Gallabox API"""
    if not all([GALLABOX_API_KEY, GALLABOX_API_SECRET, GALLABOX_CHANNEL_ID]):
        print("❌ Gallabox credentials missing")
        return False
        
    headers = {
        "Content-Type": "application/json",
        "x-api-key": GALLABOX_API_KEY,
        "x-api-secret": GALLABOX_API_SECRET
    }
    payload = {
        "channelId": GALLABOX_CHANNEL_ID,
        "to": phone,
        "type": "image",
        "message": {"image": image_url, "caption": caption}
    }
    try:
        r = requests.post(GALLABOX_API_URL, headers=headers, json=payload, timeout=10)
        print("📤 Gallabox image response:", r.status_code, r.text)
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Gallabox image send error: {e}")
        return False

########## MESSAGE HANDLER ##########
def handle_message(session_id, msg):
    msgl = msg.lower().strip()
    print(f"📩 Message from {session_id}: {msg}")

    if session_id not in conversations:
        conversations[session_id] = {"history": []}
    conversations[session_id]["history"].append({"role": "user", "content": msg})

    cat = detect_category_with_gpt(msgl)
    if cat:
        items = get_random_products(cat, 3)
        if items:
            return {"type": "products", "category": cat, "items": items}

    reply = get_chatgpt_response(msg, conversations[session_id]["history"])
    conversations[session_id]["history"].append({"role": "assistant", "content": reply})
    return {"type": "text", "content": reply}

########## GALLABOX WEBHOOK ##########
@app.route("/gallabox_webhook", methods=["POST", "GET", "OPTIONS"])
def gallabox_webhook():
    """Handle WhatsApp messages via Gallabox"""
    
    # Handle OPTIONS for CORS preflight
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
    
    # Handle GET requests for webhook verification
    if request.method == "GET":
        print("🔍 Webhook verification request received")
        verify_token = request.args.get('verify_token')
        challenge = request.args.get('challenge')
        
        # Return challenge for webhook verification
        if challenge:
            return challenge, 200
        return jsonify({"status": "ok", "message": "Webhook is active"}), 200
    
    # Handle POST requests for actual messages
    try:
        data = request.get_json(force=True) if request.data else {}
        print("📩 Received Gallabox message data")
        
        if not data:
            return jsonify({"status": "error", "message": "No data received"}), 400

        # Extract message data based on Gallabox webhook structure
        user_phone = data.get("data", {}).get("from", "unknown")
        user_message = data.get("data", {}).get("message", {}).get("text", "").strip()

        if not user_message:
            send_whatsapp_message(user_phone, "Hi 👋! Welcome to Zulu Club — your premium lifestyle shopping destination!")
            return jsonify({"status": "ok"}), 200

        response = handle_message(user_phone, user_message)

        if response["type"] == "text":
            send_whatsapp_message(user_phone, response["content"])

        elif response["type"] == "products":
            send_whatsapp_message(user_phone, f"Here are some picks from our *{response['category'].title()}* collection 💫")
            for item in response["items"]:
                send_whatsapp_image(user_phone, item["image_url"], f"{item['name']} — {item['price']}\nAvailable on zulu.club ✨")

        else:
            send_whatsapp_message(user_phone, "Hey there 👋! Welcome to Zulu Club!")

        return jsonify({"status": "sent"}), 200

    except Exception as e:
        print(f"❌ Gallabox Webhook Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 200

########## HEALTH CHECK ##########
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "ok", 
        "message": "Zulu Club Chat Assistant is running on Vercel!",
        "endpoints": {
            "webhook": "/gallabox_webhook (POST)",
            "health": "/ping (GET)"
        },
        "environment_configured": {
            "openai": bool(OPENAI_API_KEY),
            "gallabox": bool(GALLABOX_API_KEY and GALLABOX_API_SECRET and GALLABOX_CHANNEL_ID)
        }
    })

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "message": "Zulu Club Chat Assistant is running!"})

# Vercel compatibility
if __name__ == "__main__":
    print("🚀 Zulu Club Chat Assistant started...")
