from flask import Flask, send_from_directory, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
import os
import json
import traceback
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='')

# Database configuration
DATABASE_URL = os.environ.get('DATABASE_URL') or os.environ.get('POSTGRES_URL') or os.environ.get('STORAGE_URL')
JSON_DB_FILE = 'entries.json'

# --- JSON Fallback Logic for Local Dev ---
def load_json_entries():
    if not os.path.exists(JSON_DB_FILE):
        return []
    try:
        with open(JSON_DB_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def save_json_entries(entries):
    with open(JSON_DB_FILE, 'w') as f:
        json.dump(entries, f, indent=4)

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
            save_json_entries([])
        print("Using Local JSON Database")
        return

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id BIGINT PRIMARY KEY,
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
        conn.commit()
        cur.close()
        print("Cloud Database initialized.")
    except Exception as e:
        print("Error initializing Cloud DB:", e)
    finally:
        if conn:
            release_db_connection(conn)

init_db()

@app.route('/api/health')
def health():
    return jsonify({"status": "healthy", "mode": "cloud" if DATABASE_URL else "local"}), 200

@app.route('/api/entries', methods=['GET'])
def get_entries():
    if not DATABASE_URL:
        return jsonify(load_json_entries())
    
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM entries ORDER BY id DESC")
        rows = cur.fetchall()
        cur.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

@app.route('/api/entries', methods=['POST'])
def create_entry():
    data = request.json
    if not data:
        return jsonify({"error": "Invalid payload"}), 400

    if not DATABASE_URL:
        entries = load_json_entries()
        entries.insert(0, data)
        save_json_entries(entries)
        return jsonify(data), 201

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = """
        INSERT INTO entries (id, date, time, title, body, mood, tags, notes, starred)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """
        cur.execute(query, (
            data.get('id'),
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
def update_entry(entry_id):
    data = request.json
    if not DATABASE_URL:
        entries = load_json_entries()
        for i, e in enumerate(entries):
            if e['id'] == entry_id:
                entries[i] = data
                save_json_entries(entries)
                return jsonify(data)
        return jsonify({'error': 'Not found'}), 404

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = """
        UPDATE entries 
        SET date=%s, time=%s, title=%s, body=%s, mood=%s, tags=%s, notes=%s, starred=%s
        WHERE id=%s
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
            entry_id
        ))
        updated_row = cur.fetchone()
        conn.commit()
        cur.close()
        if updated_row:
            return jsonify(dict(updated_row))
        return jsonify({'error': 'Not found'}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

@app.route('/api/entries/<int:entry_id>', methods=['DELETE'])
def delete_entry(entry_id):
    if not DATABASE_URL:
        entries = load_json_entries()
        entries = [e for e in entries if e['id'] != entry_id]
        save_json_entries(entries)
        return '', 204

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM entries WHERE id = %s", (entry_id,))
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
    app.run(port=5050, debug=True)