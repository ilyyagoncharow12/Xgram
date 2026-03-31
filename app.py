import os
import uuid
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.utils import secure_filename
import database as db
import hashlib

app = Flask(__name__)
app.config['SECRET_KEY'] = 'xgram-secret-key-change-in-production'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB

socketio = SocketIO(app, cors_allowed_origins="*")

# Ensure upload directories exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'avatars'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'files'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'photos'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'videos'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'audio'), exist_ok=True)

ALLOWED_EXTENSIONS = {
    'photo': {'png', 'jpg', 'jpeg', 'gif', 'webp'},
    'video': {'mp4', 'webm', 'avi', 'mov', 'mkv'},
    'audio': {'mp3', 'wav', 'ogg', 'm4a'},
    'document': {'pdf', 'doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx', 'txt', 'zip', 'rar'}
}


def allowed_file(filename, file_type):
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    return ext in ALLOWED_EXTENSIONS.get(file_type, set())


def get_file_category(filename):
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    for category, extensions in ALLOWED_EXTENSIONS.items():
        if ext in extensions:
            return category
    return 'document'


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

        user = db.verify_user(phone, password)
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['phone'] = user['phone']
            db.update_last_seen(user['id'])
            return redirect(url_for('chat_page'))
        else:
            return render_template('login.html', error='Неверный номер телефона или пароль')

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        phone = request.form.get('phone')
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        if len(password) < 8:
            return render_template('register.html', error='Пароль должен быть не менее 8 символов')

        if password != confirm_password:
            return render_template('register.html', error='Пароли не совпадают')

        # Validate username (letters, numbers, underscore)
        import re
        if not re.match(r'^[a-zA-Z0-9_]+$', username):
            return render_template('register.html',
                                   error='Имя пользователя может содержать только латинские буквы, цифры и нижнее подчеркивание')

        user_id = db.create_user(phone, username, password)
        if user_id:
            session['user_id'] = user_id
            session['username'] = username
            session['phone'] = phone
            return redirect(url_for('chat_page'))
        else:
            return render_template('register.html', error='Пользователь с таким номером или именем уже существует')

    return render_template('register.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/chat')
def chat_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = db.get_user_by_id(session['user_id'])
    chats = db.get_user_chats(session['user_id'])
    contacts = db.get_contacts(session['user_id'])
    call_history = db.get_call_history(session['user_id'])
    favorites = db.get_favorites(session['user_id'])

    # Format chats for template
    chat_list = []
    for chat in chats:
        chat_list.append({
            'id': chat['chat_id'],
            'user_id': chat['other_user_id'],
            'username': chat['username'],
            'avatar': chat['avatar'],
            'last_message': chat['last_message'],
            'last_file_type': chat['last_file_type'],
            'last_message_time': chat['last_message_time'],
            'unread_count': chat['unread_count'],
            'is_read': chat['is_read'] if chat['last_sender_id'] != session['user_id'] else True
        })

    return render_template('chat.html',
                           user=user,
                           chats=chat_list,
                           contacts=contacts,
                           call_history=call_history,
                           favorites=favorites)


@app.route('/api/search_users')
def search_users():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    query = request.args.get('q', '')
    users = db.search_users(query, session['user_id'])

    return jsonify([{
        'id': u['id'],
        'username': u['username'],
        'phone': u['phone'],
        'avatar': u['avatar'],
        'bio': u['bio']
    } for u in users])


@app.route('/api/add_contact', methods=['POST'])
def api_add_contact():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    contact_id = data.get('contact_id')

    if db.add_contact(session['user_id'], contact_id):
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Already in contacts'}), 400


@app.route('/api/get_chat/<int:user_id>')
def get_chat_with_user(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    chat_id = db.get_or_create_chat(session['user_id'], user_id)
    messages = db.get_messages(chat_id, session['user_id'])
    other_user = db.get_user_by_id(user_id)

    return jsonify({
        'chat_id': chat_id,
        'other_user': {
            'id': other_user['id'],
            'username': other_user['username'],
            'phone': other_user['phone'],
            'avatar': other_user['avatar'],
            'bio': other_user['bio']
        },
        'messages': [{
            'id': m['id'],
            'sender_id': m['sender_id'],
            'content': m['content'],
            'file_type': m['file_type'],
            'file_path': m['file_path'],
            'file_name': m['file_name'],
            'file_size': m['file_size'],
            'is_read': m['is_read'],
            'created_at': m['created_at'],
            'sender_username': m['username'],
            'sender_avatar': m['avatar']
        } for m in messages]
    })


@app.route('/api/send_message', methods=['POST'])
def api_send_message():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    chat_id = request.form.get('chat_id')
    content = request.form.get('content', '')

    # Handle file upload
    file_type = None
    file_path = None
    file_name = None
    file_size = None

    if 'file' in request.files:
        file = request.files['file']
        if file and file.filename:
            file_name = secure_filename(file.filename)
            file_category = get_file_category(file_name)
            file_type = file_category

            # Generate unique filename
            ext = file_name.rsplit('.', 1)[1].lower() if '.' in file_name else ''
            unique_name = f"{uuid.uuid4().hex}.{ext}"

            # Save to appropriate folder
            folder = os.path.join(app.config['UPLOAD_FOLDER'], f"{file_category}s")
            os.makedirs(folder, exist_ok=True)
            file_path = os.path.join(folder, unique_name)
            file.save(file_path)
            file_size = os.path.getsize(file_path)

            # Store relative path
            file_path = f"uploads/{file_category}s/{unique_name}"

    message = db.send_message(
        chat_id=chat_id,
        sender_id=session['user_id'],
        content=content,
        file_type=file_type,
        file_path=file_path,
        file_name=file_name,
        file_size=file_size
    )

    # Emit via socketio
    socketio.emit('new_message', {
        'chat_id': chat_id,
        'message': {
            'id': message['id'],
            'sender_id': message['sender_id'],
            'content': message['content'],
            'file_type': message['file_type'],
            'file_path': message['file_path'],
            'file_name': message['file_name'],
            'file_size': message['file_size'],
            'is_read': message['is_read'],
            'created_at': message['created_at'],
            'sender_username': message['username'],
            'sender_avatar': message['avatar']
        }
    }, room=f"chat_{chat_id}")

    return jsonify({'success': True, 'message': dict(message)})


@app.route('/api/mark_read', methods=['POST'])
def api_mark_read():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    chat_id = data.get('chat_id')

    db.get_messages(chat_id, session['user_id'])  # This marks as read

    socketio.emit('messages_read', {
        'chat_id': chat_id,
        'user_id': session['user_id']
    }, room=f"chat_{chat_id}")

    return jsonify({'success': True})


@app.route('/api/make_call', methods=['POST'])
def api_make_call():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    receiver_id = data.get('receiver_id')
    call_type = data.get('call_type')  # 'audio' or 'video'

    call_id = db.add_call(session['user_id'], receiver_id, call_type, 'ringing')

    socketio.emit('incoming_call', {
        'call_id': call_id,
        'caller_id': session['user_id'],
        'caller_name': session['username'],
        'call_type': call_type
    }, room=f"user_{receiver_id}")

    return jsonify({'success': True, 'call_id': call_id})


@app.route('/api/answer_call', methods=['POST'])
def api_answer_call():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    call_id = data.get('call_id')

    db.update_call_status(call_id, 'answered')

    socketio.emit('call_answered', {
        'call_id': call_id,
        'user_id': session['user_id']
    })

    return jsonify({'success': True})


@app.route('/api/end_call', methods=['POST'])
def api_end_call():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    call_id = data.get('call_id')
    duration = data.get('duration', 0)

    db.update_call_status(call_id, 'ended', duration)

    return jsonify({'success': True})


@app.route('/api/update_profile', methods=['POST'])
def api_update_profile():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    username = request.form.get('username')
    bio = request.form.get('bio')

    updates = {}
    if username:
        # Check if username is available
        existing = db.get_user_by_username(username)
        if existing and existing['id'] != session['user_id']:
            return jsonify({'success': False, 'error': 'Username already taken'}), 400
        updates['username'] = username
        session['username'] = username

    if bio is not None:
        updates['bio'] = bio

    # Handle avatar upload
    if 'avatar' in request.files:
        file = request.files['avatar']
        if file and file.filename:
            ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'png'
            unique_name = f"{uuid.uuid4().hex}.{ext}"
            folder = os.path.join(app.config['UPLOAD_FOLDER'], 'avatars')
            os.makedirs(folder, exist_ok=True)
            file_path = os.path.join(folder, unique_name)
            file.save(file_path)
            updates['avatar'] = f"uploads/avatars/{unique_name}"

    if updates:
        db.update_user(session['user_id'], **updates)

    return jsonify({'success': True, 'user': updates})


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
        file_category = get_file_category(file_name)
        file_type = file_category

        ext = file_name.rsplit('.', 1)[1].lower() if '.' in file_name else ''
        unique_name = f"{uuid.uuid4().hex}.{ext}"
        folder = os.path.join(app.config['UPLOAD_FOLDER'], 'favorites')
        os.makedirs(folder, exist_ok=True)
        file_path = os.path.join(folder, unique_name)
        file.save(file_path)
        file_path = f"uploads/favorites/{unique_name}"

    fav_id = db.add_to_favorites(session['user_id'], file_type, file_path, file_name, note)

    return jsonify({'success': True, 'favorite_id': fav_id})


@app.route('/api/delete_account', methods=['POST'])
def api_delete_account():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    confirmation = data.get('confirmation', '')

    user = db.get_user_by_id(session['user_id'])

    # Simple confirmation check
    if confirmation == user['phone'] or confirmation == user['username']:
        db.delete_user(session['user_id'])
        session.clear()
        return jsonify({'success': True})

    return jsonify({'success': False, 'error': 'Incorrect confirmation'}), 400


@app.route('/api/update_settings', methods=['POST'])
def api_update_settings():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    # Store settings in session or database
    # For now, store in session
    if 'theme' in data:
        session['theme'] = data['theme']
    if 'font_size' in data:
        session['font_size'] = data['font_size']
    if 'chat_wallpaper' in data:
        session['chat_wallpaper'] = data['chat_wallpaper']

    return jsonify({'success': True})


@app.route('/api/get_privacy_settings')
def get_privacy_settings():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    # Default privacy settings (store in DB in production)
    return jsonify({
        'last_seen': 'everyone',
        'profile_photo': 'everyone',
        'forward_messages': 'everyone',
        'calls': 'everyone',
        'messages': 'everyone'
    })


@app.route('/api/update_privacy', methods=['POST'])
def update_privacy():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    # Store privacy settings in DB
    # For now, store in session
    session['privacy_settings'] = data

    return jsonify({'success': True})


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename))


# SocketIO events
@socketio.on('connect')
def handle_connect():
    if 'user_id' in session:
        join_room(f"user_{session['user_id']}")
        emit('connected', {'user_id': session['user_id']})


@socketio.on('disconnect')
def handle_disconnect():
    if 'user_id' in session:
        leave_room(f"user_{session['user_id']}")


@socketio.on('join_chat')
def handle_join_chat(data):
    if 'user_id' in session:
        chat_id = data.get('chat_id')
        join_room(f"chat_{chat_id}")
        emit('joined_chat', {'chat_id': chat_id})


@socketio.on('leave_chat')
def handle_leave_chat(data):
    if 'user_id' in session:
        chat_id = data.get('chat_id')
        leave_room(f"chat_{chat_id}")


@socketio.on('typing')
def handle_typing(data):
    if 'user_id' in session:
        chat_id = data.get('chat_id')
        emit('user_typing', {
            'user_id': session['user_id'],
            'username': session['username']
        }, room=f"chat_{chat_id}")


if __name__ == '__main__':
    db.init_db()
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)