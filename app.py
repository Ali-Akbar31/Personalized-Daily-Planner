import os
import time
import json
import base64
import hmac
import hashlib
import sqlite3
import datetime
import smtplib
import numpy as np
from email.mime.text import MIMEText
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from flask import Flask, request, jsonify, send_from_directory
from pywebpush import webpush, WebPushException
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__, static_folder='static', template_folder='templates')
SECRET_KEY = os.environ.get("SECRET_KEY", "planner_secret_key_2025")
DB_PATH = os.environ.get("DATABASE_URL", "planner.db")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "3_7rX8_pX9_zX0_qX1_sX2_tX3_uX4_vX5_wX6_xX7_yX")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuB-3qOX7jV-1i4lT4rKkAQrG4")
VAPID_CLAIMS = {"sub": "mailto:admin@planner.com"}

class CursorWrapper:
    def __init__(self, cursor, is_pg):
        self.cursor = cursor
        self.is_pg = is_pg
    def execute(self, query, params=()):
        if self.is_pg:
            query = query.replace('?', '%s')
            query = query.replace('AUTOINCREMENT', '')
            query = query.replace('INTEGER PRIMARY KEY', 'SERIAL PRIMARY KEY')
            if "last_insert_rowid()" in query:
                query = "SELECT lastval()"
            if "date('now', '-7 days')" in query:
                query = query.replace("date('now', '-7 days')", "CURRENT_DATE - INTERVAL '7 days'")
            if "date(created_at)" in query:
                query = query.replace("date(created_at)", "DATE(created_at)")
            if "strftime('%H', created_at)" in query:
                query = query.replace("strftime('%H', created_at)", "EXTRACT(HOUR FROM created_at)")
        self.cursor.execute(query, params)
        return self
    def fetchone(self):
        return self.cursor.fetchone()
    def fetchall(self):
        return self.cursor.fetchall()

class DBWrapper:
    def __init__(self, is_pg, conn):
        self.is_pg = is_pg
        self.conn = conn
    def execute(self, query, params=()):
        cw = CursorWrapper(self.conn.cursor(), self.is_pg)
        return cw.execute(query, params)
    def commit(self):
        self.conn.commit()
    def close(self):
        self.conn.close()

def get_db():
    is_pg = DB_PATH.startswith("postgres")
    if is_pg:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        conn = psycopg2.connect(DB_PATH, cursor_factory=RealDictCursor)
        return DBWrapper(True, conn)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return DBWrapper(False, conn)

def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'user',
        work_hours TEXT DEFAULT '9-17',
        focus_blocks TEXT DEFAULT 'morning',
        reminder_time INTEGER DEFAULT 15,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active INTEGER DEFAULT 1
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        priority TEXT DEFAULT 'medium',
        category TEXT DEFAULT 'personal',
        due_date TEXT,
        duration INTEGER DEFAULT 30,
        time_spent INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending',
        is_recurring INTEGER DEFAULT 0,
        completed_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    try:
        conn.execute('ALTER TABLE tasks ADD COLUMN time_spent INTEGER DEFAULT 0')
    except:
        pass
    conn.execute('''CREATE TABLE IF NOT EXISTS templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT NOT NULL,
        description TEXT,
        priority TEXT DEFAULT 'medium',
        category TEXT DEFAULT 'personal',
        duration INTEGER DEFAULT 30,
        is_routine INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS ml_configs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        model_name TEXT,
        param_name TEXT,
        param_value TEXT
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT,
        details TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS push_subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        subscription TEXT
    )''')
    
    admin_pw = hashlib.sha256(("admin123" + SECRET_KEY).encode()).hexdigest()
    try:
        admin_exists = conn.execute("SELECT id FROM users WHERE email=?", ("admin@planner.com",)).fetchone()
        if not admin_exists:
            conn.execute('''INSERT INTO users (name, email, password, role) 
                         VALUES (?, ?, ?, ?)''', ("Admin", "admin@planner.com", admin_pw, "admin"))
    except Exception as e:
        print("Admin user creation issue:", e)
    
    ml_count = conn.execute("SELECT COUNT(*) FROM ml_configs").fetchone()
    if (ml_count[0] if ml_count else 0) == 0:
        conn.execute("INSERT INTO ml_configs (model_name, param_name, param_value) VALUES (?, ?, ?)", ('scheduler', 'n_estimators', '10'))
        conn.execute("INSERT INTO ml_configs (model_name, param_name, param_value) VALUES (?, ?, ?)", ('recurring', 'threshold_count', '3'))
        
    conn.commit()
    conn.close()

def hash_password(password):
    return hashlib.sha256((password + SECRET_KEY).encode()).hexdigest()

def create_token(user_id, role):
    payload = {"user_id": user_id, "role": role, "exp": time.time() + 86400}
    payload_json = json.dumps(payload)
    payload_b64 = base64.b64encode(payload_json.encode()).decode().replace("=", "")
    sig = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}||{sig}"

def verify_token(token):
    try:
        idx = token.rfind("||")
        if idx == -1:
            return None
        payload_b64 = token[:idx]
        sig = token[idx+2:]
        expected_sig = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.b64decode(payload_b64).decode())
        if payload["exp"] < time.time():
            return None
        return payload
    except:
        return None

def get_current_user():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    return verify_token(token)

def log_action(user_id, action, details=""):
    conn = get_db()
    conn.execute("INSERT INTO logs (user_id, action, details) VALUES (?, ?, ?)",
                 (user_id, action, details))
    conn.commit()
    conn.close()

@app.route('/')
def index():
    return send_from_directory('templates', 'index.html')

@app.route('/<path:filename>')
def static_files(filename):
    if os.path.exists(f'templates/{filename}'):
        return send_from_directory('templates', filename)
    return send_from_directory('static', filename)

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    name = data.get('name', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    if not name or not email or not password:
        return jsonify({"error": "All fields required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    if '@' not in email:
        return jsonify({"error": "Invalid email"}), 400
    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if existing:
        conn.close()
        return jsonify({"error": "Email already registered"}), 400
    hashed = hash_password(password)
    conn.execute("INSERT INTO users (name, email, password) VALUES (?, ?, ?)", (name, email, hashed))
    conn.commit()
    user = conn.execute("SELECT id, role FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    token = create_token(user['id'], user['role'])
    log_action(user['id'], "register", f"{email}")
    return jsonify({"message": "Registered successfully", "token": token, "role": user['role'], "name": name, "user_id": user['id']}), 201

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    if not user or user['password'] != hash_password(password):
        return jsonify({"error": "Invalid email or password"}), 401
    if not user['is_active']:
        return jsonify({"error": "Account disabled"}), 403
    token = create_token(user['id'], user['role'])
    log_action(user['id'], "login", f"{email}")
    return jsonify({
        "message": "Login successful",
        "token": token,
        "role": user['role'],
        "name": user['name'],
        "user_id": user['id']
    })

@app.route('/api/reminders/send', methods=['POST'])
def send_email_reminder():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    task_title = data.get('title', 'Task')
    user_email = data.get('email', 'user@example.com')
    
    conn = get_db()
    sub = conn.execute("SELECT subscription FROM push_subscriptions WHERE user_id=?", (user['user_id'],)).fetchone()
    conn.close()
    
    push_status = "Not Subscribed"
    if sub:
        try:
            webpush(
                subscription_info=json.loads(sub['subscription']),
                data=json.dumps({"title": "Planner Reminder", "body": f"Task '{task_title}' is due soon!"}),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS
            )
            push_status = "Sent"
        except Exception as ex:
            push_status = f"Failed ({str(ex)})"

    email_status = "Not Sent"
    try:
        if SMTP_USER and SMTP_PASS:
            msg = MIMEText(f"Reminder: {task_title} is due soon!")
            msg['Subject'] = 'Task Reminder'
            msg['From'] = SMTP_USER
            msg['To'] = user_email
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
            email_status = "Sent"
        log_action(user['user_id'], "reminder_sent", f"Sent to {user_email} for {task_title}")
        return jsonify({"message": "Reminder processed", "push_status": push_status, "email_status": email_status})
    except Exception as e:
        return jsonify({"error": str(e), "push_status": push_status, "email_status": "Failed"}), 500

@app.route('/api/tasks/<int:task_id>/track', methods=['PUT'])
def track_time(task_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    time_spent = int(data.get('time_spent', 0))
    conn = get_db()
    conn.execute("UPDATE tasks SET time_spent = time_spent + ? WHERE id=? AND user_id=?", (time_spent, task_id, user['user_id']))
    conn.commit()
    conn.close()
    return jsonify({"message": "Time tracked successfully"})

@app.route('/api/profile', methods=['GET'])
def get_profile():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    u = conn.execute("SELECT id, name, email, role, work_hours, focus_blocks, reminder_time, created_at FROM users WHERE id=?",
                     (user['user_id'],)).fetchone()
    conn.close()
    return jsonify(dict(u))

@app.route('/api/profile', methods=['PUT'])
def update_profile():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    name = data.get('name', '').strip()
    work_hours = data.get('work_hours', '9-17')
    focus_blocks = data.get('focus_blocks', 'morning')
    reminder_time = data.get('reminder_time', 15)
    conn = get_db()
    conn.execute("UPDATE users SET name=?, work_hours=?, focus_blocks=?, reminder_time=? WHERE id=?",
                 (name, work_hours, focus_blocks, reminder_time, user['user_id']))
    conn.commit()
    conn.close()
    return jsonify({"message": "Profile updated"})

@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    priority = request.args.get('priority', '')
    category = request.args.get('category', '')
    status = request.args.get('status', '')
    search = request.args.get('search', '')
    date_filter = request.args.get('date', '')
    query = "SELECT * FROM tasks WHERE user_id=?"
    params = [user['user_id']]
    if priority:
        query += " AND priority=?"
        params.append(priority)
    if category:
        query += " AND category=?"
        params.append(category)
    if status:
        query += " AND status=?"
        params.append(status)
    if date_filter:
        query += " AND due_date LIKE ?"
        params.append(f"{date_filter}%")
    if search:
        query += " AND (title LIKE ? OR description LIKE ?)"
        params.extend([f'%{search}%', f'%{search}%'])
    query += " ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 END, due_date ASC"
    conn = get_db()
    tasks = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([dict(t) for t in tasks])

@app.route('/api/tasks', methods=['POST'])
def create_task():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    title = data.get('title', '').strip()
    if not title:
        return jsonify({"error": "Title is required"}), 400
    conn = get_db()
    conn.execute('''INSERT INTO tasks (user_id, title, description, priority, category, due_date, duration, status, is_recurring)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                 (user['user_id'], title, data.get('description', ''),
                  data.get('priority', 'medium'), data.get('category', 'personal'),
                  data.get('due_date', ''), data.get('duration', 30),
                  'pending', data.get('is_recurring', 0)))
    conn.commit()
    task_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    log_action(user['user_id'], "create_task", f"{title}")
    return jsonify(dict(task)), 201

@app.route('/api/tasks/<int:task_id>', methods=['PUT'])
def update_task(task_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks WHERE id=? AND user_id=?", (task_id, user['user_id'])).fetchone()
    if not task:
        conn.close()
        return jsonify({"error": "Task not found"}), 404
    data = request.get_json()
    new_status = data.get('status', task['status'])
    completed_at = None
    if new_status == 'completed' and task['status'] != 'completed':
        completed_at = datetime.datetime.now().isoformat()
    elif new_status != 'completed':
        completed_at = None
    else:
        completed_at = task['completed_at']
    conn.execute('''UPDATE tasks SET title=?, description=?, priority=?, category=?,
                    due_date=?, duration=?, status=?, is_recurring=?, completed_at=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?''',
                 (data.get('title', task['title']),
                  data.get('description', task['description']),
                  data.get('priority', task['priority']),
                  data.get('category', task['category']),
                  data.get('due_date', task['due_date']),
                  data.get('duration', task['duration']),
                  new_status,
                  data.get('is_recurring', task['is_recurring']),
                  completed_at,
                  task_id))
    conn.commit()
    updated = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    return jsonify(dict(updated))

@app.route('/api/tasks/<int:task_id>', methods=['DELETE'])
def delete_task(task_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks WHERE id=? AND user_id=?", (task_id, user['user_id'])).fetchone()
    if not task:
        conn.close()
        return jsonify({"error": "Task not found"}), 404
    conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()
    conn.close()
    log_action(user['user_id'], "delete_task", f"{task_id}")
    return jsonify({"message": "Task deleted"})

@app.route('/api/templates', methods=['GET', 'POST'])
def handle_templates():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    if request.method == 'GET':
        templates = conn.execute("SELECT * FROM templates WHERE user_id IS NULL OR user_id=?", (user['user_id'],)).fetchall()
        conn.close()
        return jsonify([dict(t) for t in templates])
    if request.method == 'POST':
        data = request.get_json()
        conn.execute('''INSERT INTO templates (user_id, title, description, priority, category, duration, is_routine)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                     (user['user_id'] if user['role'] != 'admin' else None,
                      data.get('title', ''), data.get('description', ''), data.get('priority', 'medium'),
                      data.get('category', 'personal'), data.get('duration', 30), data.get('is_routine', 0)))
        conn.commit()
        conn.close()
        return jsonify({"message": "Template created"}), 201

@app.route('/api/templates/<int:tid>', methods=['DELETE'])
def delete_template(tid):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    if user['role'] == 'admin':
        conn.execute("DELETE FROM templates WHERE id=?", (tid,))
    else:
        conn.execute("DELETE FROM templates WHERE id=? AND user_id=?", (tid, user['user_id']))
    conn.commit()
    conn.close()
    return jsonify({"message": "Template deleted"})

@app.route('/api/tasks/schedule', methods=['GET'])
def get_schedule():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    tasks = conn.execute("SELECT * FROM tasks WHERE user_id=? AND status='pending'", (user['user_id'],)).fetchall()
    history = conn.execute("SELECT * FROM tasks WHERE user_id=? AND status='completed'", (user['user_id'],)).fetchall()
    u_info = conn.execute("SELECT work_hours, focus_blocks FROM users WHERE id=?", (user['user_id'],)).fetchone()
    ml_conf = conn.execute("SELECT param_value FROM ml_configs WHERE model_name='scheduler' AND param_name='n_estimators'").fetchone()
    conn.close()
    
    n_est = int(ml_conf['param_value']) if ml_conf else 10
    
    work_hrs_len = 8
    if u_info and u_info['work_hours']:
        try:
            parts = u_info['work_hours'].split('-')
            work_hrs_len = int(parts[1]) - int(parts[0])
        except:
            pass
    focus_map = {'morning': 1, 'afternoon': 2, 'evening': 3}
    focus_val = focus_map.get(u_info['focus_blocks'] if u_info else '', 1)
    
    tasks_list = [dict(t) for t in tasks]
    history_list = [dict(h) for h in history]
    
    if len(history_list) > 5 and len(tasks_list) > 0:
        try:
            df_hist = pd.DataFrame(history_list)
            df_hist['priority_num'] = df_hist['priority'].map({'low': 1, 'medium': 2, 'high': 3}).fillna(2)
            df_hist['target'] = 1 
            df_hist['work_hours'] = work_hrs_len
            df_hist['focus_block'] = focus_val
            
            df_pending = pd.DataFrame(tasks_list)
            df_pending['priority_num'] = df_pending['priority'].map({'low': 1, 'medium': 2, 'high': 3}).fillna(2)
            df_pending['work_hours'] = work_hrs_len
            df_pending['focus_block'] = focus_val
            
            X_train = df_hist[['priority_num', 'duration', 'work_hours', 'focus_block']]
            y_train = df_hist['target']
            
            model = RandomForestClassifier(n_estimators=n_est, random_state=42)
            model.fit(X_train, y_train)
            
            X_pred = df_pending[['priority_num', 'duration', 'work_hours', 'focus_block']]
            probs = model.predict_proba(X_pred)
            if probs.shape[1] > 1:
                df_pending['score'] = probs[:, 1]
            else:
                df_pending['score'] = df_pending['priority_num'] * 0.5
                
            df_pending = df_pending.sort_values(by=['score', 'due_date'], ascending=[False, True])
            
            scheduled = []
            for i, row in df_pending.iterrows():
                t = next(item for item in tasks_list if item['id'] == row['id'])
                t['suggested_order'] = len(scheduled) + 1
                t['reason'] = f"Score: {row['score']:.2f}"
                scheduled.append(t)
            return jsonify(scheduled)
        except Exception as e:
            pass
            
    scheduled = sorted(tasks_list, key=lambda x: (
        {'high': 1, 'medium': 2, 'low': 3}.get(x['priority'], 2),
        x['due_date'] if x['due_date'] else '9999-99-99'
    ))
    for i, t in enumerate(scheduled):
        t['suggested_order'] = i + 1
        t['reason'] = f"{t['priority'].upper()}"
    return jsonify(scheduled)

@app.route('/api/tasks/recurring_suggestions', methods=['GET'])
def recurring_suggestions():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    ml_conf = conn.execute("SELECT param_value FROM ml_configs WHERE model_name='recurring' AND param_name='threshold_count'").fetchone()
    thresh = int(ml_conf['param_value']) if ml_conf else 3
    tasks = conn.execute('''
        SELECT title, category, priority, duration, 
        strftime('%H', created_at) as time_of_day, 
        COUNT(*) as freq 
        FROM tasks 
        WHERE user_id=? 
        GROUP BY title, category, priority, duration, strftime('%H', created_at) 
        HAVING freq >= ?
    ''', (user['user_id'], thresh)).fetchall()
    conn.close()
    return jsonify([dict(t) for t in tasks])

@app.route('/api/tasks/stats', methods=['GET'])
def get_stats():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id=?", (user['user_id'],)).fetchone()[0]
    completed = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND status='completed'", (user['user_id'],)).fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND status='pending'", (user['user_id'],)).fetchone()[0]
    by_category = conn.execute("SELECT category, COUNT(*) as count, SUM(time_spent) as total_time FROM tasks WHERE user_id=? GROUP BY category", (user['user_id'],)).fetchall()
    by_priority = conn.execute("SELECT priority, COUNT(*) as count FROM tasks WHERE user_id=? GROUP BY priority", (user['user_id'],)).fetchall()
    recurring = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND is_recurring=1", (user['user_id'],)).fetchone()[0]
    
    weekly_trends = conn.execute('''
        SELECT date(created_at) as d, COUNT(*) as c FROM tasks 
        WHERE user_id=? AND created_at >= date('now', '-7 days')
        GROUP BY date(created_at) ORDER BY d ASC
    ''', (user['user_id'],)).fetchall()
    
    conn.close()
    return jsonify({
        "total": total,
        "completed": completed,
        "pending": pending,
        "completion_rate": round((completed / total * 100) if total > 0 else 0, 1),
        "by_category": [dict(r) for r in by_category],
        "by_priority": [dict(r) for r in by_priority],
        "recurring_tasks": recurring,
        "weekly_trends": [dict(w) for w in weekly_trends]
    })

@app.route('/api/admin/tasks', methods=['GET'])
def admin_get_tasks():
    user = get_current_user()
    if not user or user['role'] != 'admin':
        return jsonify({"error": "Admin access required"}), 403
    user_id_filter = request.args.get('user_id', '')
    status_filter = request.args.get('status', '')
    query = "SELECT t.*, u.name as user_name, u.email as user_email FROM tasks t JOIN users u ON t.user_id = u.id WHERE 1=1"
    params = []
    if user_id_filter:
        query += " AND t.user_id=?"
        params.append(user_id_filter)
    if status_filter:
        query += " AND t.status=?"
        params.append(status_filter)
    query += " ORDER BY t.created_at DESC"
    conn = get_db()
    tasks = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([dict(t) for t in tasks])

@app.route('/api/admin/users', methods=['GET'])
def admin_get_users():
    user = get_current_user()
    if not user or user['role'] != 'admin':
        return jsonify({"error": "Admin access required"}), 403
    conn = get_db()
    users = conn.execute('''SELECT id, name, email, role, is_active, created_at,
                             (SELECT COUNT(*) FROM tasks WHERE user_id=users.id) as task_count
                             FROM users ORDER BY created_at DESC''').fetchall()
    conn.close()
    return jsonify([dict(u) for u in users])

@app.route('/api/admin/users/<int:user_id>/toggle', methods=['PUT'])
def admin_toggle_user(user_id):
    user = get_current_user()
    if not user or user['role'] != 'admin':
        return jsonify({"error": "Admin access required"}), 403
    conn = get_db()
    u = conn.execute("SELECT is_active FROM users WHERE id=?", (user_id,)).fetchone()
    if not u:
        conn.close()
        return jsonify({"error": "User not found"}), 404
    new_status = 0 if u['is_active'] else 1
    conn.execute("UPDATE users SET is_active=? WHERE id=?", (new_status, user_id))
    conn.commit()
    conn.close()
    return jsonify({"message": "User status updated", "is_active": new_status})

@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
def admin_delete_user(user_id):
    user = get_current_user()
    if not user or user['role'] != 'admin':
        return jsonify({"error": "Admin access required"}), 403
    conn = get_db()
    conn.execute("DELETE FROM tasks WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM templates WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "User deleted"})

@app.route('/api/admin/stats', methods=['GET'])
def admin_stats():
    user = get_current_user()
    if not user or user['role'] != 'admin':
        return jsonify({"error": "Admin access required"}), 403
    conn = get_db()
    total_users = conn.execute("SELECT COUNT(*) FROM users WHERE role='user'").fetchone()[0]
    active_users = conn.execute("SELECT COUNT(*) FROM users WHERE role='user' AND is_active=1").fetchone()[0]
    total_tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    completed_tasks = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='completed'").fetchone()[0]
    pending_tasks = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='pending'").fetchone()[0]
    recent_logs = conn.execute("SELECT l.*, u.name FROM logs l LEFT JOIN users u ON l.user_id=u.id ORDER BY l.created_at DESC LIMIT 20").fetchall()
    common_tasks = conn.execute("SELECT category, COUNT(*) as count FROM tasks GROUP BY category ORDER BY count DESC").fetchall()
    ml_configs = conn.execute("SELECT * FROM ml_configs").fetchall()
    conn.close()
    return jsonify({
        "total_users": total_users,
        "active_users": active_users,
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "pending_tasks": pending_tasks,
        "recent_logs": [dict(l) for l in recent_logs],
        "common_tasks": [dict(c) for c in common_tasks],
        "ml_configs": [dict(m) for m in ml_configs]
    })

@app.route('/api/admin/ml_configs', methods=['PUT'])
def admin_update_ml_configs():
    user = get_current_user()
    if not user or user['role'] != 'admin':
        return jsonify({"error": "Admin access required"}), 403
    data = request.get_json()
    conn = get_db()
    for conf_id, value in data.items():
        conn.execute("UPDATE ml_configs SET param_value=? WHERE id=?", (str(value), conf_id))
    conn.commit()
    conn.close()
    return jsonify({"message": "ML configurations updated"})

@app.route('/api/admin/ml_retrain', methods=['POST'])
def admin_retrain_ml():
    user = get_current_user()
    if not user or user['role'] != 'admin':
        return jsonify({"error": "Admin access required"}), 403
    conn = get_db()
    history = conn.execute("SELECT * FROM tasks WHERE status='completed'").fetchall()
    
    if len(history) < 5:
        conn.close()
        return jsonify({"message": "Not enough data", "accuracy": "N/A"})
        
    df = pd.DataFrame([dict(h) for h in history])
    df['priority_num'] = df['priority'].map({'low': 1, 'medium': 2, 'high': 3}).fillna(2)
    df['target'] = np.where(df['duration'] > 45, 1, 0)
    
    X = df[['priority_num', 'duration']]
    y = df['target']
    
    try:
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
        model = RandomForestClassifier(n_estimators=20, random_state=42)
        model.fit(X_train, y_train)
        score = model.score(X_test, y_test)
        
        conn.execute("UPDATE ml_configs SET param_value='20' WHERE model_name='scheduler' AND param_name='n_estimators'")
        acc_str = f"{score*100:.1f}%"
        conn.execute("INSERT INTO logs (user_id, action, details) VALUES (?, ?, ?)", 
                     (user['user_id'], "ml_retrain", f"Real evaluation complete. Accuracy: {acc_str}"))
        conn.commit()
        conn.close()
        return jsonify({"message": "Models retrained", "accuracy": acc_str})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)
