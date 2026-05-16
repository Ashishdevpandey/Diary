from flask import Flask, send_from_directory, request, jsonify, session
import threading
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
import os
import json
import traceback
import bcrypt
import random
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from dotenv import load_dotenv
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user

load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get('SECRET_KEY', 'ink-and-impressions-secret-key-123')

# OTP Store: { email: { 'otp': '123456', 'expiry': timestamp } }
otp_store = {}

def send_email(target_email, subject, body, image_filename='Mail.png', **kwargs):
    sender = os.environ.get('MAIL_USERNAME')
    password = os.environ.get('MAIL_PASSWORD')
    if not sender or not password:
        print("Email credentials missing in .env")
        return False
    
    msg = MIMEMultipart()
    msg['From'] = f"Ink & Impressions <{sender}>"
    msg['To'] = target_email
    msg['Subject'] = subject
    
    # Attach HTML body
    msg.attach(MIMEText(body, 'html'))
    
    # Attach image if it exists (from local file)
    if image_filename and os.path.exists(image_filename):
        try:
            with open(image_filename, 'rb') as f:
                img_data = f.read()
            img = MIMEImage(img_data)
            img.add_header('Content-ID', '<mail_header_image>')
            img.add_header('Content-Disposition', 'inline', filename=image_filename)
            msg.attach(img)
        except Exception as img_err:
            print(f"Failed to attach image {image_filename}: {img_err}")
    
    # Attach raw image data if provided (from base64)
    if 'raw_image_data' in kwargs and kwargs['raw_image_data']:
        try:
            import base64
            img_data = base64.b64decode(kwargs['raw_image_data'])
            img = MIMEImage(img_data)
            img.add_header('Content-Disposition', 'attachment', filename='screenshot.png')
            msg.attach(img)
        except Exception as raw_img_err:
            print(f"Failed to attach raw image: {raw_img_err}")
    
    try:
        # Use Gmail SMTP with a 5-second timeout to prevent hanging on Vercel
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=5) as server:
            server.login(sender, password)
            server.send_message(msg)
        return True, None
    except Exception as e:
        error_str = str(e) or repr(e)
        print(f"Failed to send email: {error_str}")
        return False, error_str

# Setup Login Manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

# Database configuration
DATABASE_URL = os.environ.get('DATABASE_URL') or os.environ.get('POSTGRES_URL') or os.environ.get('STORAGE_URL')
JSON_DB_FILE = 'entries.json'

# --- JSON Fallback Logic for Local Dev ---
def load_json_db():
    if not os.path.exists(JSON_DB_FILE):
        return {"users": [], "entries": []}
    try:
        with open(JSON_DB_FILE, 'r') as f:
            data = json.load(f)
            if isinstance(data, list): # Migrate old format
                return {"users": [], "entries": data}
            return data
    except:
        return {"users": [], "entries": []}

def save_json_db(data):
    with open(JSON_DB_FILE, 'w') as f:
        json.dump(data, f, indent=4)

# --- Database Setup ---
db_pool = None
if DATABASE_URL:
    try:
        # Use SimpleConnectionPool
        db_pool = pool.SimpleConnectionPool(1, 10, DATABASE_URL, cursor_factory=RealDictCursor)
        print("Using Cloud Database (PostgreSQL)")
    except Exception as e:
        print("Error connecting to Cloud DB, falling back to JSON:", e)

def get_db_connection():
    if not DATABASE_URL:
        return None
    if not db_pool:
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return db_pool.getconn()

def release_db_connection(conn):
    if db_pool and conn:
        db_pool.putconn(conn)

def init_db():
    """Initializes the database if DATABASE_URL is present."""
    if not DATABASE_URL:
        # Initialize JSON file if not exists
        if not os.path.exists(JSON_DB_FILE):
            save_json_db({"users": [], "entries": []})
        else:
            # Check for migration
            db = load_json_db()
            save_json_db(db)
        print("Using Local JSON Database")
        return

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # Create users table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE,
                password_hash TEXT NOT NULL,
                deletion_scheduled BOOLEAN DEFAULT FALSE,
                deletion_date TIMESTAMP WITH TIME ZONE,
                data_wipe_scheduled BOOLEAN DEFAULT FALSE,
                data_wipe_date TIMESTAMP WITH TIME ZONE,
                data_wipe_confirmed_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Add deletion columns if they don't exist (migration)
        cur.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='deletion_scheduled') THEN
                    ALTER TABLE users ADD COLUMN deletion_scheduled BOOLEAN DEFAULT FALSE;
                    ALTER TABLE users ADD COLUMN deletion_date TIMESTAMP WITH TIME ZONE;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='data_wipe_scheduled') THEN
                    ALTER TABLE users ADD COLUMN data_wipe_scheduled BOOLEAN DEFAULT FALSE;
                    ALTER TABLE users ADD COLUMN data_wipe_date TIMESTAMP WITH TIME ZONE;
                    ALTER TABLE users ADD COLUMN data_wipe_confirmed_at TIMESTAMP WITH TIME ZONE;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='data_wipe_confirmed_at') THEN
                    ALTER TABLE users ADD COLUMN data_wipe_confirmed_at TIMESTAMP WITH TIME ZONE;
                END IF;
            END $$;
        """)
        # Create entries table with user_id
        cur.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id BIGINT PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                date TEXT NOT NULL,
                time TEXT,
                title TEXT,
                body TEXT,
                mood INTEGER,
                tags JSONB,
                notes JSONB,
                starred BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Check if user_id column exists, if not add it (migration)
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='entries' AND column_name='user_id';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE entries ADD COLUMN user_id INTEGER REFERENCES users(id);")
            
        conn.commit()
        cur.close()
        print("Cloud Database initialized.")
    except Exception as e:
        print("Error initializing Cloud DB:", e)
    finally:
        if conn:
            release_db_connection(conn)

init_db()

@login_manager.user_loader
def load_user(user_id):
    if not DATABASE_URL:
        db = load_json_db()
        for u in db['users']:
            if str(u['id']) == str(user_id):
                return User(u['id'], u['username'])
        return None
    
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, username FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        cur.close()
        if row:
            return User(row['id'], row['username'])
    except:
        pass
    finally:
        if conn: release_db_connection(conn)
    return None

# --- Auth Routes ---

@app.route('/api/otp/send', methods=['POST'])
def send_otp():
    data = request.json
    email = data.get('email')
    username = data.get('username', 'User')
    purpose = data.get('purpose', 'signup') # 'signup' or 'reset'
    
    if not email:
        return jsonify({"error": "Email required"}), 400
    
    # Check if user exists for signup purpose
    if purpose == 'signup':
        exists = False
        if not DATABASE_URL:
            db = load_json_db()
            exists = any(u.get('email') == email for u in db['users'])
        else:
            conn = None
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("SELECT id FROM users WHERE email = %s", (email,))
                exists = cur.fetchone() is not None
                cur.close()
            finally:
                if conn: release_db_connection(conn)
        
        if exists:
            return jsonify({"error": "Account already exists with this email"}), 400

    # Generate 6-digit OTP
    otp = f"{random.randint(100000, 999999)}"
    otp_store[email] = {
        "otp": otp,
        "expiry": time.time() + 300 # 5 minutes
    }
    
    subject = f"Your OTP for Ink & Impressions - {purpose.capitalize()}"
    body = f"""
    <div style="font-family: 'Lora', serif; padding: 20px; background: #f7f0df; border: 1px solid #c8a870; border-radius: 10px; color: #2e1f0d;">
        <div style="display: flex; align-items: center; margin-bottom: 20px;">
            <img src="cid:mail_header_image" alt="Ink & Impressions" style="max-width: 80px; border-radius: 8px; margin-right: 15px;">
            <h2 style="color: #6b4c2a; margin: 0;">Ink & Impressions</h2>
        </div>
        <p>Hello <b>{username}</b>,</p>
        <p>Your One-Time Password (OTP) for <b>{purpose}</b> is:</p>
        <div style="font-size: 32px; font-weight: bold; letter-spacing: 5px; color: #6b4c2a; margin: 20px 0; text-align: center; background: rgba(255,255,255,0.5); padding: 10px; border-radius: 5px;">
            {otp}
        </div>
        <p>This code will expire in 5 minutes.</p>
        <p style="font-size: 12px; color: #a07840;">If you didn't request this, please ignore this email.</p>
    </div>
    """
    
    success, err_msg = send_email(email, subject, body)
    if success:
        return jsonify({"message": "OTP sent successfully"})
    else:
        return jsonify({"error": f"Failed to send OTP: {err_msg}"}), 500

@app.route('/api/signup', methods=['POST'])
def signup():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    email = data.get('email')
    otp = data.get('otp')
    
    if not username or not password or not email or not otp:
        return jsonify({"error": "All fields required"}), 400
    
    # Verify OTP
    stored = otp_store.get(email)
    if not stored or stored['otp'] != otp or time.time() > stored['expiry']:
        return jsonify({"error": "Invalid or expired OTP"}), 400
    
    # Clear OTP after use
    del otp_store[email]
    
    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    if not DATABASE_URL:
        db = load_json_db()
        if any(u['username'] == username for u in db['users']):
            return jsonify({"error": "Username already exists"}), 400
        if any(u.get('email') == email for u in db['users']):
            return jsonify({"error": "Email already registered"}), 400
        new_user = {"id": int(os.urandom(4).hex(), 16) % 1000000, "username": username, "email": email, "password_hash": hashed}
        db['users'].append(new_user)
        save_json_db(db)
        login_user(User(new_user['id'], username))
        return jsonify({"message": "Signed up successfully", "user": {"id": new_user['id'], "username": username}}), 201

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s) RETURNING id", (username, email, hashed))
        new_id = cur.fetchone()['id']
        conn.commit()
        cur.close()
        login_user(User(new_id, username))
        return jsonify({"message": "Signed up successfully", "user": {"id": new_id, "username": username}}), 201
    except psycopg2.IntegrityError:
        return jsonify({"error": "Username or Email already exists"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: release_db_connection(conn)

@app.route('/api/password_reset/request', methods=['POST'])
def reset_request():
    data = request.json
    email = data.get('email')
    if not email:
        return jsonify({"error": "Email required"}), 400
    
    # Check if user exists and get username
    user_data = None
    if not DATABASE_URL:
        db = load_json_db()
        user_data = next((u for u in db['users'] if u.get('email') == email), None)
    else:
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT username FROM users WHERE email = %s", (email,))
            user_data = cur.fetchone()
            cur.close()
        finally:
            if conn: release_db_connection(conn)
    
    if not user_data:
        return jsonify({"error": "No account found with this email"}), 404
        
    username = user_data['username'] if isinstance(user_data, dict) else user_data[0]
    
    # Generate 6-digit OTP
    otp = f"{random.randint(100000, 999999)}"
    otp_store[email] = {
        "otp": otp,
        "expiry": time.time() + 300 # 5 minutes
    }
    
    subject = "Reset Your Password - Ink & Impressions"
    body = f"""
    <div style="font-family: 'Lora', serif; padding: 20px; background: #f7f0df; border: 1px solid #c8a870; border-radius: 10px; color: #2e1f0d;">
        <div style="display: flex; align-items: center; margin-bottom: 20px;">
            <img src="cid:mail_header_image" alt="Ink & Impressions" style="max-width: 80px; border-radius: 8px; margin-right: 15px;">
            <h2 style="color: #6b4c2a; margin: 0;">Ink & Impressions</h2>
        </div>
        <p>Hello <b>{username}</b>,</p>
        <p>You requested to reset your password. Use the following code:</p>
        <div style="font-size: 32px; font-weight: bold; letter-spacing: 5px; color: #6b4c2a; margin: 20px 0; text-align: center; background: rgba(255,255,255,0.5); padding: 10px; border-radius: 5px;">
            {otp}
        </div>
        <p>This code will expire in 5 minutes.</p>
        <p style="font-size: 12px; color: #a07840;">If you didn't request this, please ignore this email.</p>
    </div>
    """
    
    success, err_msg = send_email(email, subject, body, image_filename='change pswd.png')
    if success:
        return jsonify({"message": "OTP sent successfully"})
    else:
        return jsonify({"error": f"Failed to send OTP: {err_msg}"}), 500

@app.route('/api/password_reset/confirm', methods=['POST'])
def reset_confirm():
    data = request.json
    email = data.get('email')
    otp = data.get('otp')
    new_password = data.get('password')
    
    if not email or not otp or not new_password:
        return jsonify({"error": "All fields required"}), 400
    
    # Verify OTP
    stored = otp_store.get(email)
    if not stored or stored['otp'] != otp or time.time() > stored['expiry']:
        return jsonify({"error": "Invalid or expired OTP"}), 400
    
    # Clear OTP
    del otp_store[email]
    
    hashed = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    if not DATABASE_URL:
        db = load_json_db()
        for u in db['users']:
            if u.get('email') == email:
                u['password_hash'] = hashed
                save_json_db(db)
                return jsonify({"message": "Password reset successfully"})
        return jsonify({"error": "User not found"}), 404

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET password_hash = %s WHERE email = %s", (hashed, email))
        conn.commit()
        cur.close()
        return jsonify({"message": "Password reset successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: release_db_connection(conn)

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if not DATABASE_URL:
        db = load_json_db()
        # Check both username and email
        user_data = next((u for u in db['users'] if u['username'] == username or u.get('email') == username), None)
        if user_data and bcrypt.checkpw(password.encode('utf-8'), user_data['password_hash'].encode('utf-8')):
            # Check if account was scheduled for deletion — welcome them back!
            welcomed_back = False
            if user_data.get('deletion_scheduled'):
                user_data['deletion_scheduled'] = False
                user_data['deletion_date'] = None
                save_json_db(db)
                welcomed_back = True
                # Send welcome back email
                if user_data.get('email'):
                    _send_welcome_back_email(user_data['email'], user_data['username'])
            user = User(user_data['id'], user_data['username'])
            login_user(user, remember=True)
            resp = {
                "message": "Logged in", 
                "user": {
                    "id": user.id, 
                    "username": user.username,
                    "data_wipe_scheduled": user_data.get('data_wipe_scheduled', False),
                    "data_wipe_date": user_data.get('data_wipe_date')
                }
            }
            if welcomed_back:
                resp["welcomed_back"] = True
            return jsonify(resp)
        return jsonify({"error": "Invalid credentials"}), 401

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, username, password_hash, email, deletion_scheduled, data_wipe_scheduled, data_wipe_date, data_wipe_confirmed_at FROM users WHERE username = %s OR email = %s", (username, username))
        row = cur.fetchone()
        cur.close()
        if row and bcrypt.checkpw(password.encode('utf-8'), row['password_hash'].encode('utf-8')):
            welcomed_back = False
            if row.get('deletion_scheduled'):
                cur2 = conn.cursor()
                cur2.execute("UPDATE users SET deletion_scheduled=FALSE, deletion_date=NULL WHERE id=%s", (row['id'],))
                conn.commit()
                cur2.close()
                welcomed_back = True
                if row.get('email'):
                    _send_welcome_back_email(row['email'], row['username'])
            user = User(row['id'], row['username'])
            login_user(user, remember=True)
            resp = {
                "message": "Logged in", 
                "user": {
                    "id": user.id, 
                    "username": user.username,
                    "data_wipe_scheduled": row.get('data_wipe_scheduled', False),
                    "data_wipe_date": row.get('data_wipe_date').isoformat() if row.get('data_wipe_date') else None,
                    "data_wipe_confirmed_at": row.get('data_wipe_confirmed_at').isoformat() if row.get('data_wipe_confirmed_at') else None
                }
            }
            if welcomed_back:
                resp["welcomed_back"] = True
            return jsonify(resp)
        return jsonify({"error": "Invalid credentials"}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn: release_db_connection(conn)

@app.route('/api/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return jsonify({"message": "Logged out"})

@app.route('/api/user_info')
def user_info():
    if current_user.is_authenticated:
        u_id = current_user.id
        wipe_scheduled = False
        wipe_date = None
        
        if not DATABASE_URL:
            db = load_json_db()
            user_data = next((u for u in db['users'] if u['id'] == u_id), None)
            if user_data:
                wipe_scheduled = user_data.get('data_wipe_scheduled', False)
                wipe_date = user_data.get('data_wipe_date')
                wipe_confirmed_at = user_data.get('data_wipe_confirmed_at')
        else:
            conn = None
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("SELECT data_wipe_scheduled, data_wipe_date, data_wipe_confirmed_at FROM users WHERE id = %s", (u_id,))
                row = cur.fetchone()
                if row:
                    wipe_scheduled = row['data_wipe_scheduled']
                    wipe_date = row['data_wipe_date'].isoformat() if row['data_wipe_date'] else None
                    wipe_confirmed_at = row['data_wipe_confirmed_at'].isoformat() if row['data_wipe_confirmed_at'] else None
                cur.close()
            finally:
                if conn: release_db_connection(conn)

        return jsonify({
            "authenticated": True, 
            "user": {
                "id": u_id, 
                "username": current_user.username,
                "data_wipe_scheduled": wipe_scheduled,
                "data_wipe_date": wipe_date,
                "data_wipe_confirmed_at": wipe_confirmed_at
            }
        })
    return jsonify({"authenticated": False}), 200

@app.route('/api/health')
def health():
    run_cleanup() # Run cleanup when health is checked
    return jsonify({"status": "healthy", "mode": "cloud" if DATABASE_URL else "local"}), 200

# ─── Email helpers ───────────────────────────────────────────────────────────

def _send_farewell_email(email, username):
    subject = "We're sad to see you go 💔 — Ink & Impressions"
    body = f"""
    <div style="font-family:'Lora',serif;padding:30px;background:#f7f0df;border:1px solid #c8a870;border-radius:12px;color:#2e1f0d;max-width:480px;margin:auto;">
      <div style="display: flex; align-items: center; margin-bottom: 20px;">
        <img src="cid:mail_header_image" alt="Ink & Impressions" style="max-width: 120px; border-radius: 8px; margin-right: 15px;">
        <h2 style="color: #6b4c2a; margin: 0;">Ink & Impressions</h2>
      </div>
      <hr style="border:none;border-top:1px solid #d5c090;margin-bottom:20px;">
      <p style="font-size:18px;">Hey <b>{username}</b>,</p>
      <p>It's heartbreaking to let you go. 💔</p>
      <p>We've marked your account for deletion. But life is full of second chances —
         if you change your mind, just log back in within <b>14 days</b> and your diary
         will be right here, exactly as you left it.</p>
      <p style="font-size:13px;color:#a07840;margin-top:24px;">
        After 14 days, your account and all your entries will be permanently erased.
      </p>
      <p style="margin-top:24px;">Until then, we'll keep your words safe. 🪶</p>
      <p style="color:#6b4c2a;font-style:italic;">— The Ink & Impressions Team</p>
    </div>
    """
    send_email(email, subject, body, image_filename='Deletion request.png')

def _send_welcome_back_email(email, username):
    subject = "Welcome back! We knew you'd return 🥹 — Ink & Impressions"
    body = f"""
    <div style="font-family:'Lora',serif;padding:30px;background:#f7f0df;border:1px solid #c8a870;border-radius:12px;color:#2e1f0d;max-width:480px;margin:auto;">
      <div style="display: flex; align-items: center; margin-bottom: 20px;">
        <img src="cid:mail_header_image" alt="Ink & Impressions" style="max-width: 80px; border-radius: 8px; margin-right: 15px;">
        <h2 style="color: #6b4c2a; margin: 0;">Ink & Impressions</h2>
      </div>
      <hr style="border:none;border-top:1px solid #d5c090;margin-bottom:20px;">
      <p style="font-size:20px;">Welcome back, <b>{username}</b>! 🥹</p>
      <p>We never stopped believing you'd return. Your diary, your memories, your words —
         they've been waiting for you.</p>
      <p>Your account deletion has been cancelled. Everything is just as you left it. 🪶</p>
      <p style="margin-top:24px;font-style:italic;color:#6b4c2a;">
        We're so glad you're back. ❤️
      </p>
      <p style="color:#6b4c2a;font-style:italic;">— The Ink & Impressions Team</p>
    </div>
    """
    send_email(email, subject, body, image_filename='Welcome back.png')

def _send_deleted_email(email, username):
    subject = "Your account has been deleted — Ink & Impressions"
    body = f"""
    <div style="font-family:'Lora',serif;padding:30px;background:#f7f0df;border:1px solid #c8a870;border-radius:12px;color:#2e1f0d;max-width:480px;margin:auto;">
      <div style="display: flex; align-items: center; margin-bottom: 20px;">
        <img src="cid:mail_header_image" alt="Ink & Impressions" style="max-width: 80px; border-radius: 8px; margin-right: 15px;">
        <h2 style="color: #6b4c2a; margin: 0;">Ink & Impressions</h2>
      </div>
      <hr style="border:none;border-top:1px solid #d5c090;margin-bottom:20px;">
      <p>Dear <b>{username}</b>,</p>
      <p>Your account and all associated diary entries have been permanently deleted, as requested.</p>
      <p>We hope your words brought you peace while they lasted. If you ever feel the urge to write again,
         you're always welcome to create a new account.</p>
      <p style="margin-top:24px;font-style:italic;color:#6b4c2a;">Take care. 🪶</p>
      <p style="color:#6b4c2a;font-style:italic;">— The Ink & Impressions Team</p>
    </div>
    """
    send_email(email, subject, body, image_filename='Farewell.png')
    
def _send_data_wipe_email(email, username, date_str):
    subject = "Your data has been scheduled for wiping — Ink & Impressions"
    body = f"""
    <div style="font-family:'Lora',serif;padding:30px;background:#f7f0df;border:1px solid #c8a870;border-radius:12px;color:#2e1f0d;max-width:480px;margin:auto;">
      <h2 style="color: #6b4c2a; margin: 0;">Data Wipe Scheduled</h2>
      <p>Dear <b>{username}</b>,</p>
      <p>As requested, your diary entries have been scheduled for wiping.</p>
      <p>Your data will be permanently deleted on <b>{date_str}</b>.</p>
      <p>If this was a mistake, you can log in to your account anytime before then and click "Restore Data" to cancel this process.</p>
      <p style="margin-top:24px;font-style:italic;color:#6b4c2a;">Take care. 🪶</p>
    </div>
    """
    send_email(email, subject, body, image_filename='Deletion request.png')

def run_cleanup():
    """Performs the 14-day grace period cleanup once. 
    Called during normal requests since Vercel doesn't support background threads."""
    import datetime
    try:
        if not DATABASE_URL:
            db = load_json_db()
            now = datetime.datetime.utcnow().isoformat()
            to_delete = []
            for u in db['users']:
                if u.get('deletion_scheduled') and u.get('deletion_date'):
                    if u['deletion_date'] < now:
                        to_delete.append(u)
            for u in to_delete:
                db['users'] = [x for x in db['users'] if x['id'] != u['id']]
                db['entries'] = [e for e in db['entries'] if e.get('user_id') != u['id']]
                if u.get('email'):
                    _send_deleted_email(u['email'], u['username'])
            
            to_wipe = []
            for u in db['users']:
                if u.get('data_wipe_scheduled') and u.get('data_wipe_date'):
                    if u['data_wipe_date'] < now:
                        to_wipe.append(u)
            for u in to_wipe:
                confirmed_at = u.get('data_wipe_confirmed_at')
                if confirmed_at:
                    # Convert confirmed_at ISO string to milliseconds timestamp
                    # Handle 'Z' if present
                    dt = datetime.datetime.fromisoformat(confirmed_at.replace('Z', '+00:00'))
                    cutoff_ms = dt.timestamp() * 1000
                    db['entries'] = [e for e in db['entries'] if not (e.get('user_id') == u['id'] and e.get('id', 0) < cutoff_ms)]
                else:
                    # Fallback: delete all if no confirmed_at (shouldn't happen with new logic)
                    db['entries'] = [e for e in db['entries'] if e.get('user_id') != u['id']]
                
                u['data_wipe_scheduled'] = False
                u['data_wipe_date'] = None
                u['data_wipe_confirmed_at'] = None

            if to_delete or to_wipe:
                save_json_db(db)
        else:
            conn = None
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                # Account deletions
                cur.execute("SELECT id, username, email FROM users WHERE deletion_scheduled=TRUE AND deletion_date < NOW()")
                rows = cur.fetchall()
                for row in rows:
                    cur.execute("DELETE FROM entries WHERE user_id=%s", (row['id'],))
                    cur.execute("DELETE FROM users WHERE id=%s", (row['id'],))
                    if row.get('email'):
                        _send_deleted_email(row['email'], row['username'])
                # Data wipes - only delete entries created BEFORE the wipe was confirmed
                cur.execute("SELECT id, data_wipe_confirmed_at FROM users WHERE data_wipe_scheduled=TRUE AND data_wipe_date < NOW()")
                wipe_rows = cur.fetchall()
                for row in wipe_rows:
                    if row['data_wipe_confirmed_at']:
                        cur.execute("DELETE FROM entries WHERE user_id=%s AND created_at < %s", (row['id'], row['data_wipe_confirmed_at']))
                    else:
                        cur.execute("DELETE FROM entries WHERE user_id=%s", (row['id'],))
                    cur.execute("UPDATE users SET data_wipe_scheduled=FALSE, data_wipe_date=NULL, data_wipe_confirmed_at=NULL WHERE id=%s", (row['id'],))
                
                conn.commit()
                cur.close()
            finally:
                if conn: release_db_connection(conn)
    except Exception as ex:
        print(f"Cleanup error: {ex}")

def _cleanup_deleted_accounts_thread():
    """Local dev background thread."""
    while True:
        run_cleanup()
        import time as t
        t.sleep(3600)  # run every hour

@app.route('/api/report_problem', methods=['POST'])
@login_required
def report_problem():
    data = request.json
    error_msg = data.get('error')
    username = getattr(current_user, 'username', 'Unknown User')
    
    if not error_msg:
        return jsonify({"error": "Error description required"}), 400
        
    developer_email = "ashishkumar02082003@gmail.com"
    subject = f"New Problem Reported by {username} — Ink & Impressions"
    
    body = f"""
    <div style="font-family:'Lora',serif;padding:30px;background:#fdfcf0;border:1px solid #d5c090;border-radius:12px;color:#2e1f0d;max-width:500px;margin:auto;">
      <h2 style="color: #6b4c2a; margin-top: 0;">Problem Report</h2>
      <p>Dear Developer,</p>
      <p>A user has reported an issue in the application.</p>
      <hr style="border:none;border-top:1px solid #d5c090;margin:20px 0;">
      <p><b>From Complainant name:</b> {username}</p>
      <p><b>Error is:</b><br>
      <i style="color: #a33;">{error_msg}</i></p>
      <hr style="border:none;border-top:1px solid #d5c090;margin:20px 0;">
      <p>Thanks</p>
      <p style="color:#6b4c2a;font-size:12px;font-style:italic;">— Ink & Impressions System</p>
    </div>
    """
    
    # Send synchronously so we can catch errors and inform the user
    # Also Vercel background threads are unreliable
    success, err_msg = send_email(developer_email, subject, body, image_filename=None, raw_image_data=data.get('image'))
    
    if success:
        return jsonify({"message": "Report submitted successfully"})
    else:
        return jsonify({"error": f"Failed to send email: {err_msg}"}), 500

# ─── Account Deletion Routes ─────────────────────────────────────────────────

@app.route('/api/account/delete/request', methods=['POST'])
@login_required
def account_delete_request():
    """Step 1: Send OTP to the user's email to confirm deletion."""
    if not DATABASE_URL:
        db = load_json_db()
        user_data = next((u for u in db['users'] if u['id'] == current_user.id), None)
    else:
        conn = None
        user_data = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT email, username FROM users WHERE id=%s", (current_user.id,))
            user_data = cur.fetchone()
            cur.close()
        finally:
            if conn: release_db_connection(conn)

    if not user_data or not user_data.get('email'):
        return jsonify({"error": "No email on file. Cannot verify deletion."}), 400

    otp = f"{random.randint(100000, 999999)}"
    otp_store[user_data['email']] = {"otp": otp, "expiry": time.time() + 300}

    subject = "Confirm Account Deletion — Ink & Impressions"
    body = f"""
    <div style="font-family:'Lora',serif;padding:30px;background:#f7f0df;border:1px solid #c8a870;border-radius:12px;color:#2e1f0d;max-width:480px;margin:auto;">
      <div style="display: flex; align-items: center; margin-bottom: 20px;">
        <img src="cid:mail_header_image" alt="Ink & Impressions" style="max-width: 120px; border-radius: 8px; margin-right: 15px;">
        <h2 style="color: #6b4c2a; margin: 0;">Ink & Impressions</h2>
      </div>
      <p>Hello <b>{user_data['username']}</b>,</p>
      <p>We received a request to <b>delete your account</b>. If this was you, use the code below:</p>
      <div style="font-size:32px;font-weight:bold;letter-spacing:6px;color:#6b4c2a;text-align:center;background:rgba(255,255,255,0.5);padding:12px;border-radius:8px;margin:20px 0;">{otp}</div>
      <p>This code expires in 5 minutes.</p>
      <p style="font-size:12px;color:#a07840;">If you didn't request this, please ignore this email. Your account is safe.</p>
    </div>
    """
    success, err_msg = send_email(user_data['email'], subject, body, image_filename='Deletion request.png')
    if success:
        return jsonify({"message": "OTP sent successfully"})
    else:
        return jsonify({"error": f"Failed to send OTP: {err_msg}"}), 500

@app.route('/api/account/delete/confirm', methods=['POST'])
@login_required
def account_delete_confirm():
    """Step 2: Verify OTP and schedule deletion in 14 days."""
    import datetime
    data = request.json
    otp = data.get('otp')

    if not DATABASE_URL:
        db = load_json_db()
        user_data = next((u for u in db['users'] if u['id'] == current_user.id), None)
    else:
        conn = None
        user_data = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT email, username FROM users WHERE id=%s", (current_user.id,))
            user_data = cur.fetchone()
            cur.close()
        finally:
            if conn: release_db_connection(conn)

    if not user_data or not user_data.get('email'):
        return jsonify({"error": "No email on file"}), 400

    email = user_data['email']
    stored = otp_store.get(email)
    if not stored or stored['otp'] != otp or time.time() > stored['expiry']:
        return jsonify({"error": "Invalid or expired OTP"}), 400
    del otp_store[email]

    deletion_date = (datetime.datetime.utcnow() + datetime.timedelta(days=14)).isoformat()

    if not DATABASE_URL:
        for u in db['users']:
            if u['id'] == current_user.id:
                u['deletion_scheduled'] = True
                u['deletion_date'] = deletion_date
        save_json_db(db)
    else:
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE users SET deletion_scheduled=TRUE, deletion_date=%s WHERE id=%s",
                        (deletion_date, current_user.id))
            conn.commit()
            cur.close()
        finally:
            if conn: release_db_connection(conn)

    # Send farewell email
    _send_farewell_email(email, user_data['username'])
    logout_user()
    return jsonify({"message": "Account scheduled for deletion"})

@app.route('/api/data/wipe/request', methods=['POST'])
@login_required
def data_wipe_request():
    if not DATABASE_URL:
        db = load_json_db()
        user_data = next((u for u in db['users'] if u['id'] == current_user.id), None)
    else:
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT email, username FROM users WHERE id=%s", (current_user.id,))
            user_data = cur.fetchone()
            cur.close()
        finally:
            if conn: release_db_connection(conn)

    if not user_data or not user_data.get('email'):
        return jsonify({"error": "No email on file. Cannot verify wipe."}), 400

    otp = f"{random.randint(100000, 999999)}"
    otp_store[user_data['email']] = {"otp": otp, "expiry": time.time() + 300}

    subject = "Confirm Data Wipe — Ink & Impressions"
    body = f"""
    <div style="font-family:'Lora',serif;padding:30px;background:#f7f0df;border:1px solid #c8a870;border-radius:12px;color:#2e1f0d;max-width:480px;margin:auto;">
      <h2 style="color: #6b4c2a; margin: 0;">Confirm Data Wipe</h2>
      <p>Hello <b>{user_data['username']}</b>,</p>
      <p>Use the code below to confirm wiping all your diary data:</p>
      <div style="font-size:32px;font-weight:bold;letter-spacing:6px;color:#6b4c2a;text-align:center;background:rgba(255,255,255,0.5);padding:12px;border-radius:8px;margin:20px 0;">{otp}</div>
      <p>This code expires in 5 minutes.</p>
    </div>
    """
    success, err_msg = send_email(user_data['email'], subject, body, image_filename='Deletion request.png')
    if success:
        return jsonify({"message": "OTP sent successfully"})
    else:
        return jsonify({"error": f"Failed to send OTP: {err_msg}"}), 500

@app.route('/api/data/wipe/confirm', methods=['POST'])
@login_required
def data_wipe_confirm():
    import datetime
    data = request.json
    otp = data.get('otp')

    if not DATABASE_URL:
        db = load_json_db()
        user_data = next((u for u in db['users'] if u['id'] == current_user.id), None)
    else:
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT email, username FROM users WHERE id=%s", (current_user.id,))
            user_data = cur.fetchone()
            cur.close()
        finally:
            if conn: release_db_connection(conn)

    if not user_data or not user_data.get('email'):
        return jsonify({"error": "No email on file"}), 400

    email = user_data['email']
    stored = otp_store.get(email)
    if not stored or stored['otp'] != otp or time.time() > stored['expiry']:
        return jsonify({"error": "Invalid or expired OTP"}), 400
    del otp_store[email]

    now_utc = datetime.datetime.utcnow()
    wipe_date = (now_utc + datetime.timedelta(days=5)).isoformat()
    confirmed_at = now_utc.isoformat()

    if not DATABASE_URL:
        for u in db['users']:
            if u['id'] == current_user.id:
                u['data_wipe_scheduled'] = True
                u['data_wipe_date'] = wipe_date
                u['data_wipe_confirmed_at'] = confirmed_at
        save_json_db(db)
    else:
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE users SET data_wipe_scheduled=TRUE, data_wipe_date=%s, data_wipe_confirmed_at=%s WHERE id=%s",
                        (wipe_date, confirmed_at, current_user.id))
            conn.commit()
            cur.close()
        finally:
            if conn: release_db_connection(conn)

    _send_data_wipe_email(email, user_data['username'], wipe_date)
    return jsonify({"message": "Data wipe scheduled", "data_wipe_date": wipe_date})

@app.route('/api/data/wipe/restore', methods=['POST'])
@login_required
def data_wipe_restore():
    if not DATABASE_URL:
        db = load_json_db()
        for u in db['users']:
            if u['id'] == current_user.id:
                u['data_wipe_scheduled'] = False
                u['data_wipe_date'] = None
                u['data_wipe_confirmed_at'] = None
        save_json_db(db)
    else:
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE users SET data_wipe_scheduled=FALSE, data_wipe_date=NULL, data_wipe_confirmed_at=NULL WHERE id=%s", (current_user.id,))
            conn.commit()
            cur.close()
        finally:
            if conn: release_db_connection(conn)
            
    return jsonify({"message": "Data restored successfully"})

@app.route('/api/entries', methods=['GET'])
@login_required
def get_entries():
    if not DATABASE_URL:
        db = load_json_db()
        user_entries = [e for e in db['entries'] if e.get('user_id') == current_user.id]
        return jsonify(user_entries)
    
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM entries WHERE user_id = %s ORDER BY id DESC", (current_user.id,))
        rows = cur.fetchall()
        cur.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

@app.route('/api/entries', methods=['POST'])
@login_required
def create_entry():
    data = request.json
    if not data:
        return jsonify({"error": "Invalid payload"}), 400

    if not DATABASE_URL:
        db = load_json_db()
        data['user_id'] = current_user.id
        db['entries'].insert(0, data)
        save_json_db(db)
        return jsonify(data), 201

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = """
        INSERT INTO entries (id, user_id, date, time, title, body, mood, tags, notes, starred)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """
        cur.execute(query, (
            data.get('id'),
            current_user.id,
            data.get('date'),
            data.get('time'),
            data.get('title'),
            data.get('body'),
            data.get('mood'),
            json.dumps(data.get('tags', [])),
            json.dumps(data.get('notes', [])),
            data.get('starred', False)
        ))
        new_row = cur.fetchone()
        conn.commit()
        cur.close()
        return jsonify(dict(new_row)), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

@app.route('/api/entries/<int:entry_id>', methods=['PUT'])
@login_required
def update_entry(entry_id):
    data = request.json
    if not DATABASE_URL:
        db = load_json_db()
        for i, e in enumerate(db['entries']):
            if e['id'] == entry_id and e.get('user_id') == current_user.id:
                data['user_id'] = current_user.id
                db['entries'][i] = data
                save_json_db(db)
                return jsonify(data)
        return jsonify({'error': 'Not found or unauthorized'}), 404

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = """
        UPDATE entries 
        SET date=%s, time=%s, title=%s, body=%s, mood=%s, tags=%s, notes=%s, starred=%s
        WHERE id=%s AND user_id=%s
        RETURNING *
        """
        cur.execute(query, (
            data.get('date'),
            data.get('time'),
            data.get('title'),
            data.get('body'),
            data.get('mood'),
            json.dumps(data.get('tags', [])),
            json.dumps(data.get('notes', [])),
            data.get('starred', False),
            entry_id,
            current_user.id
        ))
        updated_row = cur.fetchone()
        conn.commit()
        cur.close()
        if updated_row:
            return jsonify(dict(updated_row))
        return jsonify({'error': 'Not found or unauthorized'}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

@app.route('/api/entries/<int:entry_id>', methods=['DELETE'])
@login_required
def delete_entry(entry_id):
    if not DATABASE_URL:
        db = load_json_db()
        db['entries'] = [e for e in db['entries'] if not (e['id'] == entry_id and e.get('user_id') == current_user.id)]
        save_json_db(db)
        return '', 204

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM entries WHERE id = %s AND user_id = %s", (entry_id, current_user.id))
        conn.commit()
        cur.close()
        return '', 204
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('.', path)

if __name__ == '__main__':
    # Start background cleanup thread for local dev
    cleanup_thread = threading.Thread(target=_cleanup_deleted_accounts_thread, daemon=True)
    cleanup_thread.start()
    app.run(debug=True, port=5050, host='0.0.0.0')# Force redeploy
