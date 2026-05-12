import psycopg2
import os

DATABASE_URL = "postgresql://neondb_owner:npg_NmGPcbzedK21@ep-rough-mouse-amf9zi51-pooler.c-5.us-east-1.aws.neon.tech/neondb?channel_binding=require&sslmode=require"

try:
    conn = psycopg2.connect(DATABASE_URL)
    print("Connection Successful!")
    cur = conn.cursor()
    cur.execute("SELECT version();")
    print("Database Version:", cur.fetchone())
    cur.close()
    conn.close()
except Exception as e:
    print("Connection Failed:", e)
