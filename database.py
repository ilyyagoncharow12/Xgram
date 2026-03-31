import sqlite3
import hashlib
from datetime import datetime
import os

DB_PATH = 'xgram.db'


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            avatar TEXT,
            bio TEXT,
            last_seen DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Chats table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER NOT NULL,
            user2_id INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user1_id, user2_id)
        )
    ''')

    # Messages table
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
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (chat_id) REFERENCES chats(id),
            FOREIGN KEY (sender_id) REFERENCES users(id)
        )
    ''')

    # Calls table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caller_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            call_type TEXT,
            status TEXT,
            duration INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (caller_id) REFERENCES users(id),
            FOREIGN KEY (receiver_id) REFERENCES users(id)
        )
    ''')

    # Contacts table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            contact_id INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, contact_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (contact_id) REFERENCES users(id)
        )
    ''')

    # Favorites (cloud storage) table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            file_type TEXT,
            file_path TEXT,
            file_name TEXT,
            note TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
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
        return user_id
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def get_user_by_phone(phone):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE phone = ?', (phone,))
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


def get_user_by_id(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user


def verify_user(phone, password):
    user = get_user_by_phone(phone)
    if user and user['password'] == hash_password(password):
        return user
    return None


def get_or_create_chat(user1_id, user2_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        'SELECT id FROM chats WHERE (user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)',
        (user1_id, user2_id, user2_id, user1_id)
    )
    chat = cursor.fetchone()

    if chat:
        return chat['id']

    cursor.execute(
        'INSERT INTO chats (user1_id, user2_id) VALUES (?, ?)',
        (user1_id, user2_id)
    )
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
               u.username, u.phone, u.avatar, u.bio,
               m.content as last_message,
               m.file_type as last_file_type,
               m.created_at as last_message_time,
               m.is_read,
               m.sender_id as last_sender_id,
               (SELECT COUNT(*) FROM messages WHERE chat_id = c.id AND sender_id != ? AND is_read = 0) as unread_count
        FROM chats c
        JOIN users u ON (CASE WHEN c.user1_id = ? THEN c.user2_id ELSE c.user1_id END) = u.id
        LEFT JOIN messages m ON m.id = (
            SELECT id FROM messages WHERE chat_id = c.id ORDER BY created_at DESC LIMIT 1
        )
        WHERE c.user1_id = ? OR c.user2_id = ?
        ORDER BY m.created_at DESC
    ''', (user_id, user_id, user_id, user_id, user_id))

    chats = cursor.fetchall()
    conn.close()
    return chats


def get_messages(chat_id, user_id):
    conn = get_db()
    cursor = conn.cursor()

    # Mark messages as read
    cursor.execute(
        'UPDATE messages SET is_read = 1 WHERE chat_id = ? AND sender_id != ?',
        (chat_id, user_id)
    )
    conn.commit()

    cursor.execute('''
        SELECT m.*, u.username, u.avatar 
        FROM messages m
        JOIN users u ON m.sender_id = u.id
        WHERE m.chat_id = ?
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

    # Get the message with user info
    cursor.execute('''
        SELECT m.*, u.username, u.avatar 
        FROM messages m
        JOIN users u ON m.sender_id = u.id
        WHERE m.id = ?
    ''', (message_id,))

    message = cursor.fetchone()
    conn.close()
    return message


def add_contact(user_id, contact_id):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO contacts (user_id, contact_id) VALUES (?, ?)',
            (user_id, contact_id)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_contacts(user_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT u.*, c.created_at as added_at
        FROM contacts c
        JOIN users u ON c.contact_id = u.id
        WHERE c.user_id = ?
        ORDER BY u.username
    ''', (user_id,))

    contacts = cursor.fetchall()
    conn.close()
    return contacts


def search_users(query, current_user_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, username, phone, avatar, bio
        FROM users
        WHERE (username LIKE ? OR phone LIKE ?) AND id != ?
        LIMIT 20
    ''', (f'%{query}%', f'%{query}%', current_user_id))

    users = cursor.fetchall()
    conn.close()
    return users


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

    cursor.execute(
        'UPDATE calls SET status = ?, duration = ? WHERE id = ?',
        (status, duration, call_id)
    )
    conn.commit()
    conn.close()


def get_call_history(user_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT c.*,
               CASE WHEN c.caller_id = ? THEN u2.username ELSE u1.username END as contact_name,
               CASE WHEN c.caller_id = ? THEN u2.avatar ELSE u1.avatar END as contact_avatar,
               CASE WHEN c.caller_id = ? THEN u2.id ELSE u1.id END as contact_id,
               c.caller_id = ? as is_outgoing
        FROM calls c
        JOIN users u1 ON c.caller_id = u1.id
        JOIN users u2 ON c.receiver_id = u2.id
        WHERE c.caller_id = ? OR c.receiver_id = ?
        ORDER BY c.created_at DESC
        LIMIT 50
    ''', (user_id, user_id, user_id, user_id, user_id, user_id))

    calls = cursor.fetchall()
    conn.close()
    return calls


def update_user(user_id, **kwargs):
    conn = get_db()
    cursor = conn.cursor()

    for key, value in kwargs.items():
        if value is not None:
            cursor.execute(f'UPDATE users SET {key} = ? WHERE id = ?', (value, user_id))

    conn.commit()
    conn.close()


def delete_user(user_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
    cursor.execute('DELETE FROM contacts WHERE user_id = ? OR contact_id = ?', (user_id, user_id))
    cursor.execute('DELETE FROM chats WHERE user1_id = ? OR user2_id = ?', (user_id, user_id))
    cursor.execute('DELETE FROM messages WHERE sender_id = ?', (user_id,))
    cursor.execute('DELETE FROM calls WHERE caller_id = ? OR receiver_id = ?', (user_id, user_id))
    cursor.execute('DELETE FROM favorites WHERE user_id = ?', (user_id,))

    conn.commit()
    conn.close()


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


def get_favorites(user_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT * FROM favorites
        WHERE user_id = ?
        ORDER BY created_at DESC
    ''', (user_id,))

    favorites = cursor.fetchall()
    conn.close()
    return favorites


def update_last_seen(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE users SET last_seen = ? WHERE id = ?',
        (datetime.now(), user_id)
    )
    conn.commit()
    conn.close()