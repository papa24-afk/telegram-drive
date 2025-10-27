import asyncio
import os
import io
import base64
# Import Flask's 'session' to store data in browser cookies
from flask import Flask, request, jsonify, send_file, render_template, session
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
# Import StringSession to save sessions as text (in the cookie)
from telethon.sessions import StringSession
# --- FIX 1: Add the ASGI wrapper import ---
from asgiref.wsgi import WsgiToAsgi

# --- 1. CONFIGURATION ---
API_ID = 22961414
API_HASH = 'c9222d33aea71740de812a2b7dc3226d'
# SESSION_FILE is no longer needed

app = Flask(__name__)
# --- FIX 2: Use an Environment Variable for the secret key ---
# This is more secure for a public app.
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'a-default-fallback-key-for-local-dev')

os.makedirs('uploads', exist_ok=True)
# login_data and client_lock are no longer needed

# --- 2. WEB PAGE ROUTE ---
@app.route('/')
def home():
    return render_template('index.html')

# --- 3. HELPER FUNCTION ---
def get_client():
    """Creates a Telethon client from the user's browser session."""
    session_string = session.get('telethon_session')
    # Create a new client with the session string (or None if not logged in)
    client = TelegramClient(StringSession(session_string), API_ID, API_HASH, loop=None)
    return client

# --- 4. API ROUTES (Modified for Public Use) ---
@app.route('/api/is_logged_in', methods=['GET'])
async def is_logged_in():
    """Checks if a session string exists in the user's cookie."""
    return jsonify({"logged_in": 'telethon_session' in session})

@app.route('/api/send_code', methods=['POST'])
async def send_code():
    """Starts the login process and saves a temporary session in the cookie."""
    # Create a brand new, empty client for this user
    client = TelegramClient(StringSession(), API_ID, API_HASH, loop=None)
    await client.connect()
    phone = request.json['phone']
    
    try:
        result = await client.send_code_request(phone)
        
        # Store the temporary session (to hold phone_code_hash) in the cookie
        session['temp_session_hash'] = result.phone_code_hash
        session['phone_number'] = phone
        
        await client.disconnect()
        return jsonify({"success": True, "message": "Code sent!"})
    except Exception as e:
        if client.is_connected(): await client.disconnect()
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/login', methods=['POST'])
async def login():
    """Completes the login and saves the permanent session in the cookie."""
    # Get the temporary data from the user's cookie
    phone_code_hash = session.get('temp_session_hash')
    phone_number = session.get('phone_number')
    
    if not phone_code_hash or not phone_number:
        return jsonify({"success": False, "message": "Session expired. Please re-enter phone number."}), 400

    # Create a new client just for this login attempt
    client = TelegramClient(StringSession(), API_ID, API_HASH, loop=None)
    await client.connect()
    
    code = request.json['code']
    password = request.json.get('password')
    
    try:
        # Sign in using the stored phone number and hash
        await client.sign_in(
            phone=phone_number,
            code=code,
            phone_code_hash=phone_code_hash
        )
        # On SUCCESS, save the *real* session string to the cookie
        session['telethon_session'] = client.session.save()
        
        # Clean up temporary data
        session.pop('temp_session_hash', None)
        session.pop('phone_number', None)
        
        await client.disconnect()
        return jsonify({"success": True, "message": "Login successful!"})
        
    except SessionPasswordNeededError:
        if not password:
            if client.is_connected(): await client.disconnect()
            return jsonify({"success": False, "message": "2FA_REQUIRED"})
        try:
            # Sign in again, this time with the password
            await client.sign_in(password=password)
            
            # On SUCCESS, save the *real* session string to the cookie
            session['telethon_session'] = client.session.save()
            
            # Clean up temporary data
            session.pop('temp_session_hash', None)
            session.pop('phone_number', None)
            
            await client.disconnect()
            return jsonify({"success": True, "message": "Login successful!"})
        except Exception as e:
            if client.is_connected(): await client.disconnect()
            return jsonify({"success": False, "message": str(e)}), 500
    except Exception as e:
        if client.is_connected(): await client.disconnect()
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/logout', methods=['POST'])
async def logout():
    """Logs the user out by clearing their session cookie."""
    try:
        # Just remove the session from the cookie
        session.pop('telethon_session', None)
        session.pop('temp_session_hash', None)
        session.pop('phone_number', None)
        return jsonify({"success": True, "message": "Logged out."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/me')
async def get_me():
    """Gets profile info using the client from the user's cookie."""
    client = get_client()
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        return jsonify({"error": "Not logged in"}), 401
    
    me = await client.get_me()
    photo_bytes = await client.download_profile_photo('me', file=bytes)
    await client.disconnect()
    
    photo_base64 = None
    if photo_bytes:
         photo_base64 = base64.b64encode(photo_bytes).decode('utf-8')
    return jsonify({"first_name": me.first_name, "last_name": me.last_name, "username": me.username, "photo": photo_base64})

@app.route('/api/thumbnail/<int:message_id>')
async def get_thumbnail(message_id):
    client = get_client() # Get client from cookie
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        return "Not authorized", 401
    try:
        message = await client.get_messages('me', ids=message_id)
        is_image = False
        if message and message.media:
            if hasattr(message.media, 'photo'): is_image = True
            elif hasattr(message.media, 'document') and message.media.document.mime_type and 'image' in message.media.document.mime_type: is_image = True
        if not is_image:
             if client.is_connected(): await client.disconnect()
             return "Not an image", 404
        thumb_bytes = await client.download_media(message.media, thumb=-1, file=bytes)
        await client.disconnect()
        if not thumb_bytes: return "No thumbnail available", 404
        return send_file(io.BytesIO(thumb_bytes), mimetype='image/jpeg')
    except Exception as e:
        if client.is_connected(): await client.disconnect()
        return "Error generating thumbnail", 500

@app.route('/api/files')
async def get_files():
    client = get_client() # Get client from cookie
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        return jsonify({"error": "Not logged in"}), 401

    files_images = []
    files_documents = []
    files_audio = []
    files_video = []
    files_compressed = []
    files_other = []

    search_query = request.args.get('search', None)
    async for message in client.iter_messages('me', limit=200, search=search_query):
        if not message.media: continue
        file_info = {"id": message.id, "date": message.date.isoformat(), "name": f"file_{message.id}"}
        if hasattr(message.media, 'document'):
            doc = message.media.document
            if hasattr(doc, 'attributes'):
                for attr in doc.attributes:
                    if hasattr(attr, 'file_name') and attr.file_name:
                        file_info['name'] = attr.file_name
                        break
            mime_type = getattr(doc, 'mime_type', '').lower()
            name_lower = file_info['name'].lower()
            if 'image' in mime_type:
                file_info['type'] = 'image'; files_images.append(file_info)
            elif 'audio' in mime_type or name_lower.endswith(('.mp3', '.wav', '.ogg', '.m4a', '.flac')):
                 file_info['type'] = 'audio'; files_audio.append(file_info)
            elif 'video' in mime_type or name_lower.endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
                 file_info['type'] = 'video'; files_video.append(file_info)
            elif 'zip' in mime_type or 'rar' in mime_type or name_lower.endswith(('.zip', '.rar', '.tar', '.gz', '.7z')):
                 file_info['type'] = 'compressed'; files_compressed.append(file_info)
            elif 'pdf' in mime_type or 'text' in mime_type or 'csv' in mime_type or \
                 'doc' in mime_type or 'xls' in mime_type or 'ppt' in mime_type or \
                 name_lower.endswith(('.pdf', '.txt', '.csv', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.md')):
                file_info['type'] = 'document'; files_documents.append(file_info)
            else:
                file_info['type'] = 'other'; files_other.append(file_info)
        elif hasattr(message.media, 'photo'):
            file_info['name'] = f"photo_{message.id}.jpg"
            file_info['type'] = 'image'; files_images.append(file_info)
    await client.disconnect()
    return jsonify({
        "images": files_images, "documents": files_documents, "audio": files_audio,
        "video": files_video, "compressed": files_compressed, "other": files_other
    })

@app.route('/api/download/<int:message_id>')
async def download_file(message_id):
    client = get_client() # Get client from cookie
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect(); return "Not authorized", 401
    try:
        message = await client.get_messages('me', ids=message_id)
        if not message or not message.media:
            if client.is_connected(): await client.disconnect(); return "File not found", 404
        file_buffer = io.BytesIO()
        await client.download_media(message.media, file=file_buffer)
        file_buffer.seek(0)
        filename = f"download_{message_id}"
        if hasattr(message.media, 'document') and hasattr(message.media.document, 'attributes'):
             for attr in message.media.document.attributes:
                 if hasattr(attr, 'file_name') and attr.file_name: filename = attr.file_name; break
        elif hasattr(message.media, 'photo'): filename = f"photo_{message.id}.jpg"
        await client.disconnect()
        return send_file(file_buffer, download_name=filename, as_attachment=True)
    except Exception as e:
        if client.is_connected(): await client.disconnect()
        return str(e), 500

@app.route('/api/upload', methods=['POST'])
async def upload_file():
    client = get_client() # Get client from cookie
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect(); return jsonify({"success": False, "message": "Not logged in"}), 401
    if 'file' not in request.files:
        await client.disconnect(); return jsonify({"success": False, "message": "No file part"}), 400
    file = request.files['file']
    if not file or not file.filename:
        await client.disconnect(); return jsonify({"success": False, "message": "No selected file"}), 400
    temp_path = ""
    try:
        from werkzeug.utils import secure_filename
        safe_filename = secure_filename(file.filename)
        if not safe_filename: import time; safe_filename = f"upload_{int(time.time())}"
        temp_path = os.path.join('uploads', safe_filename)
        file.save(temp_path)
        await client.send_file('me', temp_path, caption=safe_filename)
        if os.path.exists(temp_path): os.remove(temp_path)
        await client.disconnect()
        return jsonify({"success": True, "message": "File uploaded!"})
    except Exception as e:
        if temp_path and os.path.exists(temp_path): os.remove(temp_path)
        if client.is_connected(): await client.disconnect()
        return jsonify({"success": False, "message": str(e)}), 500

# --- FIX 3: Wrap the app in the ASGI wrapper ---
asgi_app = WsgiToAsgi(app)

# --- 5. RUN THE APP ---
if __name__ == '__main__':
    # 'host' must be '0.0.0.0' to be reachable by other computers
    app.run(host='0.0.0.0', debug=True, port=5000)
