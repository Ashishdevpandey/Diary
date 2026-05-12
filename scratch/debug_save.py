import psycopg2
import json
import time

DATABASE_URL = "postgresql://neondb_owner:npg_NmGPcbzedK21@ep-rough-mouse-amf9zi51-pooler.c-5.us-east-1.aws.neon.tech/neondb?sslmode=require"

try:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    test_data = {
        "id": int(time.time() * 1000),
        "date": "2024-05-04",
        "time": "12:00 PM",
        "title": "Test Entry",
        "body": "This is a test body.",
        "mood": 5,
        "tags": ["#test"],
        "notes": ["test note"],
        "starred": False
    }
    
    query = """
    INSERT INTO entries (id, date, time, title, body, mood, tags, notes, starred)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id;
    """
    
    cur.execute(query, (
        test_data['id'],
        test_data['date'],
        test_data['time'],
        test_data['title'],
        test_data['body'],
        test_data['mood'],
        json.dumps(test_data['tags']),
        json.dumps(test_data['notes']),
        test_data['starred']
    ))
    
    new_id = cur.fetchone()[0]
    conn.commit()
    print(f"Success! Created entry with ID: {new_id}")
    
    cur.close()
    conn.close()
except Exception as e:
    print("FAILED TO SAVE ENTRY:", e)
