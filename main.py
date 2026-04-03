import os
import uuid
import re
import sqlite3
import hashlib
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.utils import secure_filename

from PIL import Image



app = Flask(__name__)

# ==================== НАСТРОЙКИ СЕССИИ (ПОСТОЯННЫЙ ВХОД НА 30 ДНЕЙ) ====================
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'swillgram-super-secret-key-2024')
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)  # 30 дней
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_COOKIE_SECURE'] = False  # Для локальной разработки
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# ==================================================================================

app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

socketio = SocketIO(app, cors_allowed_origins="*")

# Создаем папки
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'avatars'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'files'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'photos'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'videos'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'audio'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'wallpapers'), exist_ok=True)

DATABASE_PATH = 'swillgram.db'


def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            avatar TEXT,
            bio TEXT,
            birthday TEXT,
            last_seen DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            privacy_last_seen TEXT DEFAULT 'everyone',
            privacy_photo TEXT DEFAULT 'everyone',
            privacy_forward TEXT DEFAULT 'everyone',
            privacy_calls TEXT DEFAULT 'everyone',
            privacy_messages TEXT DEFAULT 'everyone',
            theme TEXT DEFAULT 'light',
            font_size INTEGER DEFAULT 14,
            bubble_radius INTEGER DEFAULT 18,
            wallpaper TEXT DEFAULT ''
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER NOT NULL,
            user2_id INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user1_id, user2_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            content TEXT,
            file_type TEXT,
            file_path TEXT,
            file_name TEXT,
            file_size INTEGER,
            is_read BOOLEAN DEFAULT 0,
            is_deleted BOOLEAN DEFAULT 0,
            deleted_for_all BOOLEAN DEFAULT 0,
            edited_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            contact_id INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, contact_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            file_type TEXT,
            file_path TEXT,
            file_name TEXT,
            note TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caller_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            call_type TEXT,
            status TEXT,
            duration INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def create_user(phone, username, password):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO users (phone, username, password, last_seen) VALUES (?, ?, ?, ?)',
            (phone, username, hash_password(password), datetime.now())
        )
        conn.commit()
        user_id = cursor.lastrowid

        # Создаем чат "Избранное" (сам с собой)
        cursor.execute('INSERT INTO chats (user1_id, user2_id) VALUES (?, ?)', (user_id, user_id))
        conn.commit()

        return user_id
    except:
        return None
    finally:
        conn.close()


def get_user_by_id(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user


def get_user_by_username(username):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
    user = cursor.fetchone()
    conn.close()
    return user


def verify_user(phone, password):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE phone = ?', (phone,))
    user = cursor.fetchone()
    conn.close()
    if user and user['password'] == hash_password(password):
        return user
    return None


def update_last_seen(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET last_seen = ? WHERE id = ?', (datetime.now(), user_id))
    conn.commit()
    conn.close()


def get_or_create_chat(user1_id, user2_id):
    if user1_id == user2_id:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM chats WHERE user1_id = ? AND user2_id = ?', (user1_id, user1_id))
        chat = cursor.fetchone()
        conn.close()
        if chat:
            return chat['id']
        return None

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id FROM chats WHERE (user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)',
        (user1_id, user2_id, user2_id, user1_id)
    )
    chat = cursor.fetchone()

    if chat:
        conn.close()
        return chat['id']

    cursor.execute('INSERT INTO chats (user1_id, user2_id) VALUES (?, ?)', (user1_id, user2_id))
    conn.commit()
    chat_id = cursor.lastrowid
    conn.close()
    return chat_id


def get_user_chats(user_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT c.id as chat_id, 
               CASE WHEN c.user1_id = ? THEN c.user2_id ELSE c.user1_id END as other_user_id,
               CASE WHEN c.user1_id = c.user2_id THEN 'Избранное'
                    ELSE u.username END as username,
               u.avatar,
               u.phone,
               u.last_seen,
               m.content as last_message,
               m.file_type as last_file_type,
               m.created_at as last_message_time,
               (SELECT COUNT(*) FROM messages WHERE chat_id = c.id AND sender_id != ? AND is_read = 0 AND is_deleted = 0) as unread_count
        FROM chats c
        LEFT JOIN users u ON (CASE WHEN c.user1_id = ? THEN c.user2_id ELSE c.user1_id END) = u.id
        LEFT JOIN messages m ON m.id = (SELECT id FROM messages WHERE chat_id = c.id AND is_deleted = 0 ORDER BY created_at DESC LIMIT 1)
        WHERE (c.user1_id = ? OR c.user2_id = ?)
        ORDER BY CASE WHEN c.user1_id = c.user2_id THEN 0 ELSE 1 END, m.created_at DESC
    ''', (user_id, user_id, user_id, user_id, user_id))

    chats = cursor.fetchall()
    conn.close()

    result = []
    for chat in chats:
        result.append({
            'id': chat['chat_id'],
            'user_id': chat['other_user_id'],
            'username': chat['username'] if chat['username'] else 'Избранное',
            'avatar': chat['avatar'],
            'phone': chat['phone'],
            'last_seen': chat['last_seen'],
            'last_message': chat['last_message'],
            'last_file_type': chat['last_file_type'],
            'last_message_time': chat['last_message_time'],
            'unread_count': chat['unread_count']
        })
    return result


def get_messages(chat_id, user_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('UPDATE messages SET is_read = 1 WHERE chat_id = ? AND sender_id != ?', (chat_id, user_id))
    conn.commit()

    cursor.execute('''
        SELECT m.*, u.username, u.avatar 
        FROM messages m
        LEFT JOIN users u ON m.sender_id = u.id
        WHERE m.chat_id = ? AND m.is_deleted = 0
        ORDER BY m.created_at ASC
    ''', (chat_id,))

    messages = cursor.fetchall()
    conn.close()
    return messages


def send_message(chat_id, sender_id, content, file_type=None, file_path=None, file_name=None, file_size=None):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO messages (chat_id, sender_id, content, file_type, file_path, file_name, file_size)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (chat_id, sender_id, content, file_type, file_path, file_name, file_size))

    conn.commit()
    message_id = cursor.lastrowid

    cursor.execute('''
        SELECT m.*, u.username, u.avatar 
        FROM messages m
        LEFT JOIN users u ON m.sender_id = u.id
        WHERE m.id = ?
    ''', (message_id,))

    message = cursor.fetchone()
    conn.close()
    return message


def edit_message(message_id, new_content):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE messages SET content = ?, edited_at = ? WHERE id = ?',
                   (new_content, datetime.now(), message_id))
    conn.commit()
    conn.close()


def delete_message(message_id, user_id, delete_for_all=False):
    conn = get_db()
    cursor = conn.cursor()
    if delete_for_all:
        cursor.execute('UPDATE messages SET is_deleted = 1, deleted_for_all = 1 WHERE id = ?', (message_id,))
    else:
        cursor.execute('UPDATE messages SET is_deleted = 1 WHERE id = ?', (message_id,))
    conn.commit()
    conn.close()


def forward_message(message_id, to_chat_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM messages WHERE id = ?', (message_id,))
    msg = cursor.fetchone()
    if msg:
        cursor.execute('''
            INSERT INTO messages (chat_id, sender_id, content, file_type, file_path, file_name, file_size)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (to_chat_id, msg['sender_id'], msg['content'], msg['file_type'], msg['file_path'], msg['file_name'],
              msg['file_size']))
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()
        return new_id
    conn.close()
    return None


def get_contacts(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT u.* FROM contacts c
        JOIN users u ON c.contact_id = u.id
        WHERE c.user_id = ?
        ORDER BY u.username
    ''', (user_id,))
    contacts = cursor.fetchall()
    conn.close()
    return contacts


def add_contact(user_id, contact_id):
    if user_id == contact_id:
        return False
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT INTO contacts (user_id, contact_id) VALUES (?, ?)', (user_id, contact_id))
        conn.commit()
        return True
    except:
        return False
    finally:
        conn.close()


def search_users(query, current_user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, username, phone, avatar, bio, last_seen
        FROM users
        WHERE (username LIKE ? OR phone LIKE ?) AND id != ?
        LIMIT 20
    ''', (f'%{query}%', f'%{query}%', current_user_id))
    users = cursor.fetchall()
    conn.close()
    return users


def get_favorites(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM favorites WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
    favorites = cursor.fetchall()
    conn.close()
    return favorites


def add_to_favorites(user_id, file_type, file_path, file_name, note=None):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO favorites (user_id, file_type, file_path, file_name, note)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, file_type, file_path, file_name, note))
    conn.commit()
    fav_id = cursor.lastrowid
    conn.close()
    return fav_id


def get_call_history(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT c.*,
               CASE WHEN c.caller_id = ? THEN u2.username ELSE u1.username END as contact_name,
               CASE WHEN c.caller_id = ? THEN u2.id ELSE u1.id END as contact_id,
               c.caller_id = ? as is_outgoing
        FROM calls c
        JOIN users u1 ON c.caller_id = u1.id
        JOIN users u2 ON c.receiver_id = u2.id
        WHERE c.caller_id = ? OR c.receiver_id = ?
        ORDER BY c.created_at DESC
        LIMIT 50
    ''', (user_id, user_id, user_id, user_id, user_id))
    calls = cursor.fetchall()
    conn.close()
    return calls


def add_call(caller_id, receiver_id, call_type, status):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO calls (caller_id, receiver_id, call_type, status)
        VALUES (?, ?, ?, ?)
    ''', (caller_id, receiver_id, call_type, status))
    conn.commit()
    call_id = cursor.lastrowid
    conn.close()
    return call_id


def update_call_status(call_id, status, duration=0):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE calls SET status = ?, duration = ? WHERE id = ?', (status, duration, call_id))
    conn.commit()
    conn.close()


def update_user_settings(user_id, **kwargs):
    conn = get_db()
    cursor = conn.cursor()
    for key, value in kwargs.items():
        if value is not None:
            cursor.execute(f'UPDATE users SET {key} = ? WHERE id = ?', (value, user_id))
    conn.commit()
    conn.close()


def resize_and_crop_image(image_path, size=(500, 500)):
    """Обрезаем и ресайзим изображение в квадрат"""
    img = Image.open(image_path)
    min_size = min(img.size)
    left = (img.size[0] - min_size) / 2
    top = (img.size[1] - min_size) / 2
    right = (img.size[0] + min_size) / 2
    bottom = (img.size[1] + min_size) / 2
    img = img.crop((left, top, right, bottom))
    img = img.resize(size, Image.Resampling.LANCZOS)
    img.save(image_path)


init_db()


# ==================== МАРШРУТЫ ====================

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('chat_page'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        phone = request.form.get('phone')
        password = request.form.get('password')
        user = verify_user(phone, password)
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['phone'] = user['phone']
            session.permanent = True  # ДЕЛАЕМ СЕССИЮ ПОСТОЯННОЙ!
            update_last_seen(user['id'])
            return redirect(url_for('chat_page'))
        return render_template('login.html', error='Неверный логин или пароль')
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        phone = request.form.get('phone')
        username = request.form.get('username')
        password = request.form.get('password')
        confirm = request.form.get('confirm_password')

        if len(password) < 8:
            return render_template('register.html', error='Пароль минимум 8 символов')
        if password != confirm:
            return render_template('register.html', error='Пароли не совпадают')
        if not re.match(r'^[a-zA-Z0-9_]+$', username):
            return render_template('register.html', error='Только латиница, цифры, _')

        user_id = create_user(phone, username, password)
        if user_id:
            session['user_id'] = user_id
            session['username'] = username
            session['phone'] = phone
            session.permanent = True  # ДЕЛАЕМ СЕССИЮ ПОСТОЯННОЙ!
            update_last_seen(user_id)
            return redirect(url_for('chat_page'))
        return render_template('register.html', error='Пользователь уже существует')
    return render_template('register.html')


@app.route('/logout')
def logout():
    if 'user_id' in session:
        update_last_seen(session['user_id'])
    session.clear()
    return redirect(url_for('login'))


@app.route('/chat')
def chat_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = get_user_by_id(session['user_id'])
    chats = get_user_chats(session['user_id'])
    contacts = get_contacts(session['user_id'])
    call_history = get_call_history(session['user_id'])
    favorites = get_favorites(session['user_id'])

    return render_template('chat.html',
                           user=user,
                           chats=chats,
                           contacts=contacts,
                           call_history=call_history,
                           favorites=favorites)


# ==================== API МАРШРУТЫ ====================

@app.route('/api/search_users')
def api_search_users():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    query = request.args.get('q', '')
    if len(query) < 2:
        return jsonify([])

    users = search_users(query, session['user_id'])
    return jsonify([dict(u) for u in users])


@app.route('/api/add_contact', methods=['POST'])
def api_add_contact():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    contact_id = data.get('contact_id')

    if add_contact(session['user_id'], contact_id):
        return jsonify({'success': True})
    return jsonify({'success': False}), 400


@app.route('/api/get_contacts')
def api_get_contacts():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    contacts = get_contacts(session['user_id'])
    return jsonify([dict(c) for c in contacts])


@app.route('/api/get_user/<int:user_id>')
def api_get_user(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    user = get_user_by_id(user_id)
    if user:
        return jsonify({
            'id': user['id'],
            'username': user['username'],
            'phone': user['phone'],
            'avatar': user['avatar'],
            'bio': user['bio'] or '',
            'birthday': user['birthday'] or '',
            'last_seen': user['last_seen']
        })
    return jsonify({'error': 'User not found'}), 404


@app.route('/api/get_my_user')
def api_get_my_user():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    user = get_user_by_id(session['user_id'])
    if user:
        return jsonify({
            'id': user['id'],
            'username': user['username'],
            'phone': user['phone'],
            'avatar': user['avatar'],
            'bio': user['bio'] or '',
            'birthday': user['birthday'] or ''
        })
    return jsonify({'error': 'User not found'}), 404


@app.route('/api/get_chat/<int:user_id>')
def api_get_chat(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    chat_id = get_or_create_chat(session['user_id'], user_id)
    messages = get_messages(chat_id, session['user_id'])
    other_user = get_user_by_id(user_id) if user_id != session['user_id'] else None

    return jsonify({
        'chat_id': chat_id,
        'other_user': {
            'id': user_id,
            'username': 'Избранное' if user_id == session['user_id'] else (
                other_user['username'] if other_user else ''),
            'phone': other_user['phone'] if other_user else '',
            'avatar': other_user['avatar'] if other_user else None,
            'bio': other_user['bio'] if other_user else 'Ваше облачное хранилище',
            'last_seen': other_user['last_seen'] if other_user else None
        } if user_id == session['user_id'] or other_user else None,
        'messages': [dict(m) for m in messages]
    })


@app.route('/api/send_message', methods=['POST'])
def api_send_message():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    chat_id = request.form.get('chat_id')
    content = request.form.get('content', '')

    file_type = None
    file_path = None
    file_name = None
    file_size = None

    if 'file' in request.files:
        file = request.files['file']
        if file and file.filename:
            file_name = secure_filename(file.filename)
            ext = file_name.rsplit('.', 1)[1].lower() if '.' in file_name else ''

            if ext in ['png', 'jpg', 'jpeg', 'gif', 'webp']:
                file_type = 'photo'
                folder = 'photos'
            elif ext in ['mp4', 'webm', 'avi', 'mov']:
                file_type = 'video'
                folder = 'videos'
            elif ext in ['mp3', 'wav', 'ogg', 'm4a']:
                file_type = 'audio'
                folder = 'audio'
            else:
                file_type = 'document'
                folder = 'files'

            os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], folder), exist_ok=True)
            unique_name = f"{uuid.uuid4().hex}.{ext}"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], folder, unique_name)
            file.save(file_path)
            file_size = os.path.getsize(file_path)
            file_path = f"uploads/{folder}/{unique_name}"

    message = send_message(
        chat_id=chat_id,
        sender_id=session['user_id'],
        content=content,
        file_type=file_type,
        file_path=file_path,
        file_name=file_name,
        file_size=file_size
    )

    if message:
        socketio.emit('new_message', {
            'chat_id': chat_id,
            'message': dict(message)
        }, room=f"chat_{chat_id}")

    return jsonify({'success': True})


@app.route('/api/edit_message', methods=['POST'])
def api_edit_message():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    message_id = data.get('message_id')
    new_content = data.get('content')

    edit_message(message_id, new_content)

    socketio.emit('message_edited', {
        'message_id': message_id,
        'new_content': new_content
    }, broadcast=True)

    return jsonify({'success': True})


@app.route('/api/delete_message', methods=['POST'])
def api_delete_message():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    message_id = data.get('message_id')
    delete_for_all = data.get('delete_for_all', False)

    delete_message(message_id, session['user_id'], delete_for_all)

    socketio.emit('message_deleted', {
        'message_id': message_id
    }, broadcast=True)

    return jsonify({'success': True})


@app.route('/api/forward_message', methods=['POST'])
def api_forward_message():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    message_id = data.get('message_id')
    to_chat_id = data.get('to_chat_id')

    new_id = forward_message(message_id, to_chat_id)

    if new_id:
        return jsonify({'success': True, 'message_id': new_id})
    return jsonify({'success': False}), 400


@app.route('/api/mark_read', methods=['POST'])
def api_mark_read():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    chat_id = data.get('chat_id')

    get_messages(chat_id, session['user_id'])
    return jsonify({'success': True})


@app.route('/api/make_call', methods=['POST'])
def api_make_call():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    receiver_id = data.get('receiver_id')
    call_type = data.get('call_type')

    call_id = add_call(session['user_id'], receiver_id, call_type, 'ringing')
    return jsonify({'success': True, 'call_id': call_id})


@app.route('/api/answer_call', methods=['POST'])
def api_answer_call():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    call_id = data.get('call_id')
    update_call_status(call_id, 'answered')
    return jsonify({'success': True})


@app.route('/api/reject_call', methods=['POST'])
def api_reject_call():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    call_id = data.get('call_id')
    update_call_status(call_id, 'rejected')

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM calls WHERE id = ?', (call_id,))
    call = cursor.fetchone()
    conn.close()

    socketio.emit('call_rejected', {
        'call_id': call_id
    }, room=f"user_{call['caller_id']}")

    return jsonify({'success': True})


@app.route('/api/end_call_route', methods=['POST'])
def api_end_call_route():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    call_id = data.get('call_id')
    duration = data.get('duration', 0)

    update_call_status(call_id, 'ended', duration)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM calls WHERE id = ?', (call_id,))
    call = cursor.fetchone()
    conn.close()

    other_user_id = call['caller_id'] if call['receiver_id'] == session['user_id'] else call['receiver_id']

    socketio.emit('call_ended', {
        'call_id': call_id,
        'duration': duration
    }, room=f"user_{other_user_id}")

    return jsonify({'success': True})


@app.route('/api/webrtc_signal', methods=['POST'])
def api_webrtc_signal():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    target_user_id = data.get('target_user_id')
    signal_data = data.get('signal_data')
    signal_type = data.get('signal_type')

    socketio.emit('webrtc_signal', {
        'from_user_id': session['user_id'],
        'signal_type': signal_type,
        'signal_data': signal_data
    }, room=f"user_{target_user_id}")

    return jsonify({'success': True})


@app.route('/api/get_call_history')
def api_get_call_history():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    calls = get_call_history(session['user_id'])
    return jsonify([dict(c) for c in calls])


@app.route('/api/add_to_favorites', methods=['POST'])
def api_add_to_favorites():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    note = request.form.get('note')
    file = request.files.get('file')

    file_type = None
    file_path = None
    file_name = None

    if file and file.filename:
        file_name = secure_filename(file.filename)
        ext = file_name.rsplit('.', 1)[1].lower() if '.' in file_name else ''

        if ext in ['png', 'jpg', 'jpeg', 'gif', 'webp']:
            file_type = 'photo'
        elif ext in ['mp4', 'webm', 'avi', 'mov']:
            file_type = 'video'
        elif ext in ['mp3', 'wav', 'ogg', 'm4a']:
            file_type = 'audio'
        else:
            file_type = 'document'

        os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'favorites'), exist_ok=True)
        unique_name = f"{uuid.uuid4().hex}.{ext}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], 'favorites', unique_name)
        file.save(file_path)
        file_path = f"uploads/favorites/{unique_name}"

    fav_id = add_to_favorites(session['user_id'], file_type, file_path, file_name, note)

    if note:
        chat_id = get_or_create_chat(session['user_id'], session['user_id'])
        if chat_id:
            send_message(chat_id, session['user_id'], note, None, None, None, None)

    return jsonify({'success': True, 'favorite_id': fav_id})


@app.route('/api/get_favorites')
def api_get_favorites():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    favorites = get_favorites(session['user_id'])
    return jsonify([dict(f) for f in favorites])


@app.route('/api/update_profile', methods=['POST'])
def api_update_profile():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    username = request.form.get('username')
    bio = request.form.get('bio')
    birthday = request.form.get('birthday')

    updates = {}
    if username:
        existing = get_user_by_username(username)
        if existing and existing['id'] != session['user_id']:
            return jsonify({'success': False, 'error': 'Username already taken'}), 400
        updates['username'] = username
        session['username'] = username
    if bio is not None:
        updates['bio'] = bio
    if birthday:
        updates['birthday'] = birthday

    if 'avatar' in request.files:
        file = request.files['avatar']
        if file and file.filename:
            ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'png'
            unique_name = f"{uuid.uuid4().hex}.{ext}"
            folder = os.path.join(app.config['UPLOAD_FOLDER'], 'avatars')
            os.makedirs(folder, exist_ok=True)
            file_path = os.path.join(folder, unique_name)
            file.save(file_path)
            resize_and_crop_image(file_path)
            updates['avatar'] = f"uploads/avatars/{unique_name}"

    if updates:
        update_user_settings(session['user_id'], **updates)

    return jsonify({'success': True, 'user': updates})


@app.route('/api/update_privacy', methods=['POST'])
def api_update_privacy():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    update_user_settings(session['user_id'],
                         privacy_last_seen=data.get('last_seen', 'everyone'),
                         privacy_photo=data.get('profile_photo', 'everyone'),
                         privacy_forward=data.get('forward_messages', 'everyone'),
                         privacy_calls=data.get('calls', 'everyone'),
                         privacy_messages=data.get('messages', 'everyone'))
    return jsonify({'success': True})


@app.route('/api/get_privacy')
def api_get_privacy():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    user = get_user_by_id(session['user_id'])
    return jsonify({
        'last_seen': user['privacy_last_seen'],
        'profile_photo': user['privacy_photo'],
        'forward_messages': user['privacy_forward'],
        'calls': user['privacy_calls'],
        'messages': user['privacy_messages']
    })


@app.route('/api/update_theme', methods=['POST'])
def api_update_theme():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    update_user_settings(session['user_id'], theme=data.get('theme', 'light'))
    return jsonify({'success': True})


@app.route('/api/update_font_size', methods=['POST'])
def api_update_font_size():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    update_user_settings(session['user_id'], font_size=data.get('font_size', 14))
    return jsonify({'success': True})


@app.route('/api/update_bubble_radius', methods=['POST'])
def api_update_bubble_radius():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    update_user_settings(session['user_id'], bubble_radius=data.get('bubble_radius', 18))
    return jsonify({'success': True})


@app.route('/api/update_wallpaper', methods=['POST'])
def api_update_wallpaper():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    wallpaper = data.get('wallpaper', '')
    update_user_settings(session['user_id'], wallpaper=wallpaper)
    return jsonify({'success': True})


@app.route('/api/get_settings')
def api_get_settings():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    user = get_user_by_id(session['user_id'])
    return jsonify({
        'theme': user['theme'],
        'font_size': user['font_size'],
        'bubble_radius': user['bubble_radius'],
        'wallpaper': user['wallpaper']
    })


@app.route('/api/delete_account', methods=['POST'])
def api_delete_account():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    confirmation = data.get('confirmation', '')

    user = get_user_by_id(session['user_id'])

    if confirmation == user['phone'] or confirmation == user['username']:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM users WHERE id = ?', (session['user_id'],))
        cursor.execute('DELETE FROM chats WHERE user1_id = ? OR user2_id = ?', (session['user_id'], session['user_id']))
        cursor.execute('DELETE FROM messages WHERE sender_id = ?', (session['user_id'],))
        cursor.execute('DELETE FROM contacts WHERE user_id = ? OR contact_id = ?',
                       (session['user_id'], session['user_id']))
        cursor.execute('DELETE FROM favorites WHERE user_id = ?', (session['user_id'],))
        cursor.execute('DELETE FROM calls WHERE caller_id = ? OR receiver_id = ?',
                       (session['user_id'], session['user_id']))
        conn.commit()
        conn.close()
        session.clear()
        return jsonify({'success': True})

    return jsonify({'success': False}), 400


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename))


@socketio.on('connect')
def handle_connect():
    if 'user_id' in session:
        join_room(f"user_{session['user_id']}")
        update_last_seen(session['user_id'])


@socketio.on('disconnect')
def handle_disconnect():
    if 'user_id' in session:
        update_last_seen(session['user_id'])


@socketio.on('join_chat')
def handle_join_chat(data):
    if 'user_id' in session:
        chat_id = data.get('chat_id')
        join_room(f"chat_{chat_id}")


@socketio.on('typing')
def handle_typing(data):
    if 'user_id' in session:
        chat_id = data.get('chat_id')
        emit('user_typing', {
            'user_id': session['user_id'],
            'username': session['username']
        }, room=f"chat_{chat_id}")


@socketio.on('webrtc_offer')
def handle_webrtc_offer(data):
    if 'user_id' in session:
        target_user_id = data.get('target_user_id')
        offer = data.get('offer')
        emit('webrtc_offer', {
            'from_user_id': session['user_id'],
            'offer': offer
        }, room=f"user_{target_user_id}")


@socketio.on('webrtc_answer')
def handle_webrtc_answer(data):
    if 'user_id' in session:
        target_user_id = data.get('target_user_id')
        answer = data.get('answer')
        emit('webrtc_answer', {
            'from_user_id': session['user_id'],
            'answer': answer
        }, room=f"user_{target_user_id}")


@socketio.on('webrtc_ice')
def handle_webrtc_ice(data):
    if 'user_id' in session:
        target_user_id = data.get('target_user_id')
        ice_candidate = data.get('ice_candidate')
        emit('webrtc_ice', {
            'from_user_id': session['user_id'],
            'ice_candidate': ice_candidate
        }, room=f"user_{target_user_id}")


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("🔌 SWILLGRAM ЗАПУЩЕН!")
    print("=" * 60)
    print("\n🌐 http://localhost:5000")
    print("=" * 60 + "\n")

    socketio.run(app, host='0.0.0.0', port=5000, debug=True)