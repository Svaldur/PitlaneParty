import os, hashlib, requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

@app.route("/")
def home():
    return "PitlaneParty server online!"

@app.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    email = data.get("email", "").lower().strip()
    password = data.get("password", "")
    if not email or not password:
        return jsonify({"ok": False, "error": "missing"}), 400

    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/users",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"},
        json={"email": email, "password": hash_pw(password)}
    )
    if r.status_code in [200, 201]:
        return jsonify({"ok": True})
    print("SUPABASE ERRO:", r.status_code, r.text)
    return jsonify({"ok": False, "error": r.text}), 400

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    email = data.get("email", "").lower().strip()
    password = data.get("password", "")
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/users?email=eq.{email}&select=*",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    )
    users = r.json()
    if users and users[0].get("password") == hash_pw(password):
        return jsonify({"ok": True, "email": email, "pontos": users[0].get("pontos", 0)})
    return jsonify({"ok": False, "error": "Login falhou"}), 401

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
