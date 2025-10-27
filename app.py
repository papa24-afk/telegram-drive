import asyncio
import os
import io
import base64
import traceback
import sys
# Import Flask's 'session' to store data in browser cookies
from flask import Flask, request, jsonify, send_file, render_template, session
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
# Import StringSession to save sessions as text (in the cookie)
from telethon.sessions import StringSession
# Import the ASGI wrapper
from asgiref.wsgi import WsgiToAsgi

# --- 1. CONFIGURATION ---
API_ID = 22961414
API_HASH = 'c9222d33aea71740de812a2b7dc3226d'

app = Flask(__name__)
# Use an Environment Variable for the secret key
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'a-default-fallback-key-for-local-dev')

os.makedirs('uploads', exist_ok=True)

# --- 2. WEB PAGE ROUTE ---
@app.route('/')
def home():
    return render_template('index.html')

# --- 3. HELPER FUNCTION ---
def get_client():
    """Creates a Telethon client from the user's browser session."""
    session_string = session.get('telethon_session')
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
    client = TelegramClient(StringSession(), API_ID, API_HASH, loop=None)
    try:
        await client.connect()
        phone = request.json['phone']
        result = await client.send_code_request(phone)
        session['temp_session_hash'] = result.phone_code_hash
        session['phone_number'] = phone
        return jsonify({"success": True, "message": "Code sent!"})
    except Exception as e:
        print(f"!!! SEND_CODE FAILED: {str(e)}", flush=True)
        traceback.print_exc(file=sys.stderr)
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if client.is_connected():
            await client.disconnect()

@app.route('/api/login', methods=['POST'])
async def login():
    """Completes the login and saves the permanent session in the cookie."""
    client = None
    try:
        phone_code_hash = session.get('temp_session_hash')
        phone_number = session.get('phone_number')
        
        if not phone_code_hash or not phone_number:
            print("!!! LOGIN FAILED: Session data missing.", flush=True)
            return jsonify({"success": False, "message": "Session expired. Please re-enter phone number."}), 400

        client = TelegramClient(StringSession(), API_ID, API_HASH, loop=None)
        await client.connect()
        
        code = request.json['code']
        password = request.json.get('password')
        
        # Try to sign in
        await client.sign_in(
            phone=phone_number,
            code=code,
            phone_code_hash=phone_code_hash
        )
        
        # This code runs if 2FA is NOT needed
        session['telethon_session'] = client.session.save()
        session.pop('temp_session_hash', None)
        session.pop('phone_number', None)
        return jsonify({"success": True, "message": "Login successful!"})

    except SessionPasswordNeededError:
        print("--- 2FA Password needed ---", flush=True)
        password = request.json.get('password')
        if not password:
            # This is not an error, just asking for the password
            return jsonify({"success": False, "message": "2FA_REQUIRED"})
        
        # 2FA password was provided, so try to sign in again
        try:
            await client.sign_in(password=password)
            
            # On 2FA success, save the session
            session['telethon_session'] = client.session.save()
            session.pop('temp_session_hash', None)
            session.pop('phone_number', None)
            return jsonify({"success": True, "message": "Login successful!"})
        
        except Exception as e_2fa:
            # This is a REAL error (e.g., wrong password)
            print(f"!!! 2FA LOGIN FAILED: {str(e_2fa)}", flush=True)
            traceback.print_exc(file=sys.stderr)
            return jsonify({"success": False, "message": f"Login failed: {str(e_2fa)}"}), 500

    except Exception as e_main:
        # This is an unexpected error (e.g., wrong code)
        print(f"!!! MAIN LOGIN FAILED: {str(e_main)}", flush=True)
        traceback.print_exc(file=sys.stderr)
        return jsonify({"success": False, "message": f"Login failed: {str(e_main)}"}), 500

    finally:
        # This ensures the client always disconnects
        if client and client.is_connected():
            await client.disconnect()

@app.route('/api/logout', methods=['POST'])
async def logout():
    """Logs the user out by clearing their session cookie."""
    try:
        session.pop('telethon_session', None)
        session.pop('temp_session_hash', None)
        session.pop('phone_number', None)
        return jsonify({"success": True, "message": "Logged out."})
    except Exception as e:
        print(f"!!! LOGOUT FAILED: {str(e)}", flush=True)
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/me')
async def get_me():
    """Gets profile info using the client from the user's cookie."""
    client = get_client()
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return jsonify({"error": "Not logged in"}), 401
        
        me = await client.get_me()
        photo_bytes = await client.download_profile_photo('me', file=bytes)
        
        photo_base64 = None
        if photo_bytes:
             photo_base64 = base64.b64encode(photo_bytes).decode('utf-8')
        return jsonify({"first_name": me.first_name, "last_name": me.last_name, "username": me.username, "photo": photo_base64})
    except Exception as e:
        print(f"!!! GET_ME FAILED: {str(e)}", flush=True)
        return jsonify({"error": str(e)}), 500
    finally:
        if client.is_connected():
            await client.disconnect()

@app.route('/api/thumbnail/<int:message_id>')
async def get_thumbnail(message_id):
    client = get_client() # Get client from cookie
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return "Not authorized", 401
        
        message = await client.get_messages('me', ids=message_id)
        is_image = False
        if message and message.media:
            if hasattr(message.media, 'photo'): is_image = True
            elif hasattr(message.media, 'document') and message.media.document.mime_type and 'image' in message.media.document.mime_type: is_image = True
        if not is_image:
             return "Not an image", 404
        
        thumb_bytes = await client.download_media(message.media, thumb=-1, file=bytes)
        if not thumb_bytes: return "No thumbnail available", 404
        return send_file(io.BytesIO(thumb_bytes), mimetype='image/jpeg')
    except Exception as e:
        print(f"!!! GET_THUMBNAIL FAILED: {str(e)}", flush=True)
        return "Error generating thumbnail", 500
    finally:
        if client.is_connected():
            await client.disconnect()

@app.route('/api/files')
async def get_files():
    client = get_client() # Get client from cookie
    try:
        await client.connect()
        if not await client.is_user_authorized():
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
        
        return jsonify({
            "images": files_images, "documents": files_documents, "audio": files_audio,
            "video": files_video, "compressed": files_compressed, "other": files_other
        })
    except Exception as e:
        print(f"!!! GET_FILES FAILED: {str(e)}", flush=True)
        return jsonify({"error": str(e)}), 500
    finally:
        if client.is_connected():
            await client.disconnect()

@app.route('/api/download/<int:message_id>')
async def download_file(message_id):
    client = get_client() # Get client from cookie
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return "Not authorized", 401
        
        message = await client.get_messages('me', ids=message_id)
        if not message or not message.media:
            return "File not found", 404
        
        file_buffer = io.BytesIO()
        await client.download_media(message.media, file=file_buffer)
        file_buffer.seek(0)
        
        filename = f"download_{message_id}"
        if hasattr(message.media, 'document') and hasattr(message.media.document, 'attributes'):
             for attr in message.media.document.attributes:
                 if hasattr(attr, 'file_name') and attr.file_name: filename = attr.file_name; break
        elif hasattr(message.media, 'photo'): filename = f"photo_{message.id}.jpg"
        
        return send_file(file_buffer, download_name=filename, as_attachment=True)
    except Exception as e:
        print(f"!!! DOWNLOAD FAILED: {str(e)}", flush=True)
        return str(e), 500
    finally:
        if client.is_connected():
            await client.disconnect()

@app.route('/api/upload', methods=['POST'])
async def upload_file():
    client = get_client() # Get client from cookie
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return jsonify({"success": False, "message": "Not logged in"}), 401
        
        if 'file' not in request.files:
            return jsonify({"success": False, "message": "No file part"}), 400
        
        file = request.files['file']
        if not file or not file.filename:
            return jsonify({"success": False, "message": "No selected file"}), 400
        
        temp_path = ""
        from werkzeug.utils import secure_filename
        safe_filename = secure_filename(file.filename)
        if not safe_filename: 
            import time
            safe_filename = f"upload_{int(time.time())}"
        
        temp_path = os.path.join('uploads', safe_filename)
        file.save(temp_path)
        await client.send_file('me', temp_path, caption=safe_filename)
        
        return jsonify({"success": True, "message": "File uploaded!"})
    except Exception as e:
        print(f"!!! UPLOAD FAILED: {str(e)}", flush=True)
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        # Clean up the temp file
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        if client.is_connected():
            await client.disconnect()

# Wrap the app in the ASGI wrapper
asgi_app = WsgiToAsgi(app)

# --- 5. RUN THE APP ---
if __name__ == '__main__':
    # 'host' must be '0.0.0.0' to be reachable by other computers
    app.run(host='0.0.0.0', debug=True, port=5000)

