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
# Ensure you set FLASK_SECRET_KEY in Render's environment variables
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'a-default-fallback-key-for-local-dev')

# Create uploads directory if it doesn't exist
os.makedirs('uploads', exist_ok=True)

# --- 2. WEB PAGE ROUTE ---
@app.route('/')
def home():
    return render_template('index.html')

# --- 3. HELPER FUNCTION ---
def get_client():
    """Creates a Telethon client from the user's permanent browser session cookie."""
    session_string = session.get('telethon_session')
    client = TelegramClient(StringSession(session_string), API_ID, API_HASH, loop=None)
    return client

# --- 4. API ROUTES ---
@app.route('/api/is_logged_in', methods=['GET'])
async def is_logged_in():
    return jsonify({"logged_in": 'telethon_session' in session})

@app.route('/api/send_code', methods=['POST'])
async def send_code():
    """Starts login, sends code, stores temporary session string and hash in cookie."""
    client = TelegramClient(StringSession(), API_ID, API_HASH, loop=None)
    try:
        await client.connect()
        phone = request.json['phone']
        print(f"--- Sending code to {phone} ---", flush=True)
        result = await client.send_code_request(phone)

        # Store the hash, phone number, AND the temporary session string itself
        session['temp_session_hash'] = result.phone_code_hash
        session['phone_number'] = phone
        session['temp_telethon_session'] = client.session.save() # <-- Store temp session state

        print(f"--- Stored temp_session_hash: {result.phone_code_hash} ---", flush=True)
        print(f"--- Stored temp_telethon_session string ---", flush=True)
        return jsonify({"success": True, "message": "Code sent!"})
    except Exception as e:
        print(f"!!! SEND_CODE FAILED: {str(e)}", flush=True)
        traceback.print_exc(file=sys.stderr)
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if client.is_connected():
            await client.disconnect()
            print("--- Send code client disconnected ---", flush=True)

@app.route('/api/login', methods=['POST'])
async def login():
    """Completes login using temp session state and saves permanent session."""
    client = None
    print("\n--- Attempting Login ---", flush=True)
    try:
        # Retrieve all temporary data
        phone_code_hash = session.get('temp_session_hash')
        phone_number = session.get('phone_number')
        temp_session_string = session.get('temp_telethon_session') # <-- Get temp session state

        print(f"Retrieved from session - Phone Number: {phone_number}", flush=True)
        print(f"Retrieved from session - Phone Code Hash: {phone_code_hash}", flush=True)
        print(f"Retrieved from session - Temp Session String: {'Present' if temp_session_string else 'MISSING!'}", flush=True)

        if not phone_code_hash or not phone_number or not temp_session_string: # <-- Check temp session too
            print("!!! LOGIN FAILED: Temporary session data missing.", flush=True)
            return jsonify({"success": False, "message": "Session expired or invalid. Please request code again."}), 400

        # Load client using the temporary session string
        client = TelegramClient(StringSession(temp_session_string), API_ID, API_HASH, loop=None) # <-- Use temp session
        print("Connecting client for login using temp session...", flush=True)
        await client.connect()
        print("Client connected.", flush=True)

        code = request.json['code']
        password = request.json.get('password')
        print(f"Code entered: {code}", flush=True)
        print(f"Password provided: {'Yes' if password else 'No'}", flush=True)

        print("Attempting initial sign in...", flush=True)
        # Sign in should work now because the client state matches the code request
        await client.sign_in(
            phone=phone_number,
            code=code,
            phone_code_hash=phone_code_hash
        )
        print("Initial sign in successful (2FA not needed). Saving permanent session...", flush=True)

        # Save the *now permanent* session string
        session['telethon_session'] = client.session.save()
        # Clean up all temporary keys
        session.pop('temp_session_hash', None)
        session.pop('phone_number', None)
        session.pop('temp_telethon_session', None)
        print("Permanent session saved, temporary data cleared.", flush=True)
        return jsonify({"success": True, "message": "Login successful!"})

    except SessionPasswordNeededError:
        print("--- 2FA Password needed ---", flush=True)
        password = request.json.get('password')
        if not password:
            print("Password not provided in request, returning 2FA_REQUIRED.", flush=True)
            # IMPORTANT: Do *not* clear temp data here, it's needed for the 2FA attempt
            return jsonify({"success": False, "message": "2FA_REQUIRED"})

        try:
            # Client is already connected and has the necessary state
            print("Attempting 2FA sign in with provided password...", flush=True)
            await client.sign_in(password=password)
            print("2FA sign in successful. Saving permanent session...", flush=True)

            # Save the permanent session
            session['telethon_session'] = client.session.save()
            # Clean up temp data
            session.pop('temp_session_hash', None)
            session.pop('phone_number', None)
            session.pop('temp_telethon_session', None)
            print("Permanent session saved after 2FA, temporary data cleared.", flush=True)
            return jsonify({"success": True, "message": "Login successful!"})

        except Exception as e_2fa:
            print(f"!!! 2FA LOGIN FAILED: {str(e_2fa)}", flush=True)
            traceback.print_exc(file=sys.stderr)
            return jsonify({"success": False, "message": f"Login failed (2FA): {str(e_2fa)}"}), 500

    except Exception as e_main:
        print(f"!!! MAIN LOGIN FAILED: {str(e_main)}", flush=True)
        traceback.print_exc(file=sys.stderr)
        return jsonify({"success": False, "message": f"Login failed: {str(e_main)}"}), 500

    finally:
        if client and client.is_connected():
            print("Disconnecting login client.", flush=True)
            await client.disconnect()
            print("Login client disconnected.", flush=True)

@app.route('/api/logout', methods=['POST'])
async def logout():
    """Logs the user out by clearing all relevant session data from their cookie."""
    try:
        print("--- Attempting Logout ---", flush=True)
        session.pop('telethon_session', None)
        session.pop('temp_session_hash', None)
        session.pop('phone_number', None)
        session.pop('temp_telethon_session', None) # Clear temp session too
        print("Session data cleared.", flush=True)
        return jsonify({"success": True, "message": "Logged out."})
    except Exception as e:
        print(f"!!! LOGOUT FAILED: {str(e)}", flush=True)
        return jsonify({"success": False, "message": str(e)}), 500

# --- (Rest of the API routes: /api/me, /api/thumbnail, /api/files, /api/download, /api/upload remain unchanged) ---
# --- (But ensure they also have try...finally blocks for disconnection) ---

@app.route('/api/me')
async def get_me():
    client = get_client()
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return jsonify({"error": "Not logged in"}), 401
        me = await client.get_me()
        photo_bytes = await client.download_profile_photo('me', file=bytes)
        photo_base64 = None
        if photo_bytes:
             photo_base64 = base64.b64encode(photo_bytes).decode('utf-8')
        return jsonify({"first_name": me.first_name, "last_name": me.last_name, "username": me.username, "photo": photo_base64})
    except Exception as e:
        print(f"!!! GET_ME FAILED: {str(e)}", flush=True)
        traceback.print_exc(file=sys.stderr)
        return jsonify({"error": "Failed to retrieve profile info"}), 500
    finally:
        if client.is_connected():
            await client.disconnect()

@app.route('/api/thumbnail/<int:message_id>')
async def get_thumbnail(message_id):
    client = get_client()
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
             return "Not an image file", 404
        thumb_bytes = await client.download_media(message.media, thumb=-1, file=bytes)
        if not thumb_bytes:
            return "No thumbnail available", 404
        return send_file(io.BytesIO(thumb_bytes), mimetype='image/jpeg')
    except Exception as e:
        print(f"!!! GET_THUMBNAIL FAILED (ID: {message_id}): {str(e)}", flush=True)
        traceback.print_exc(file=sys.stderr)
        return "Error generating thumbnail", 500
    finally:
        if client.is_connected():
            await client.disconnect()

@app.route('/api/files')
async def get_files():
    client = get_client()
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return jsonify({"error": "Not logged in"}), 401
        files_images, files_documents, files_audio, files_video, files_compressed, files_other = [], [], [], [], [], []
        search_query = request.args.get('search', None)
        print(f"--- Fetching files (search: '{search_query if search_query else 'None'}') ---", flush=True)
        async for message in client.iter_messages('me', limit=200, search=search_query):
            if not message.media: continue
            file_info = {"id": message.id, "date": message.date.isoformat(), "name": f"file_{message.id}"}
            if hasattr(message.media, 'document'):
                doc = message.media.document
                if hasattr(doc, 'attributes'):
                    for attr in doc.attributes:
                        if hasattr(attr, 'file_name') and attr.file_name: file_info['name'] = attr.file_name; break
                mime_type = getattr(doc, 'mime_type', '').lower(); name_lower = file_info['name'].lower()
                if 'image' in mime_type: file_info['type'] = 'image'; files_images.append(file_info)
                elif 'audio' in mime_type or name_lower.endswith(('.mp3', '.wav', '.ogg', '.m4a', '.flac')): file_info['type'] = 'audio'; files_audio.append(file_info)
                elif 'video' in mime_type or name_lower.endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')): file_info['type'] = 'video'; files_video.append(file_info)
                elif 'zip' in mime_type or 'rar' in mime_type or name_lower.endswith(('.zip', '.rar', '.tar', '.gz', '.7z')): file_info['type'] = 'compressed'; files_compressed.append(file_info)
                elif 'pdf' in mime_type or 'text' in mime_type or 'csv' in mime_type or \
                     mime_type.startswith(('application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml',
                                           'application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml',
                                           'application/vnd.ms-powerpoint', 'application/vnd.openxmlformats-officedocument.presentationml')) or \
                     name_lower.endswith(('.pdf', '.txt', '.csv', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.md')):
                    file_info['type'] = 'document'; files_documents.append(file_info)
                else: file_info['type'] = 'other'; files_other.append(file_info)
            elif hasattr(message.media, 'photo'):
                file_info['name'] = f"photo_{message.id}.jpg"; file_info['type'] = 'image'; files_images.append(file_info)
        print(f"--- Found files - Images: {len(files_images)}, Docs: {len(files_documents)}, Audio: {len(files_audio)}, Video: {len(files_video)}, Compressed: {len(files_compressed)}, Other: {len(files_other)} ---", flush=True)
        return jsonify({"images": files_images, "documents": files_documents, "audio": files_audio, "video": files_video, "compressed": files_compressed, "other": files_other})
    except Exception as e:
        print(f"!!! GET_FILES FAILED: {str(e)}", flush=True)
        traceback.print_exc(file=sys.stderr)
        return jsonify({"error": "Failed to retrieve files"}), 500
    finally:
        if client.is_connected():
            await client.disconnect()

@app.route('/api/download/<int:message_id>')
async def download_file(message_id):
    client = get_client()
    try:
        await client.connect()
        if not await client.is_user_authorized(): return "Not authorized", 401
        message = await client.get_messages('me', ids=message_id)
        if not message or not message.media: return "File not found in message", 404
        file_buffer = io.BytesIO()
        await client.download_media(message.media, file=file_buffer)
        file_buffer.seek(0)
        filename = f"download_{message_id}"
        if hasattr(message.media, 'document') and hasattr(message.media.document, 'attributes'):
             for attr in message.media.document.attributes:
                 if hasattr(attr, 'file_name') and attr.file_name: filename = attr.file_name; break
        elif hasattr(message.media, 'photo'): filename = f"photo_{message_id}.jpg"
        print(f"--- Downloading file: {filename} (ID: {message_id}) ---", flush=True)
        return send_file(file_buffer, download_name=filename, as_attachment=True)
    except Exception as e:
        print(f"!!! DOWNLOAD FAILED (ID: {message_id}): {str(e)}", flush=True)
        traceback.print_exc(file=sys.stderr)
        return "Error processing download", 500
    finally:
        if client.is_connected():
            await client.disconnect()

@app.route('/api/upload', methods=['POST'])
async def upload_file():
    client = get_client()
    temp_path = ""
    try:
        await client.connect()
        if not await client.is_user_authorized(): return jsonify({"success": False, "message": "Not logged in"}), 401
        if 'file' not in request.files: return jsonify({"success": False, "message": "No file part in request"}), 400
        file = request.files['file']
        if not file or not file.filename: return jsonify({"success": False, "message": "No file selected"}), 400
        from werkzeug.utils import secure_filename
        safe_filename = secure_filename(file.filename)
        if not safe_filename: import time; safe_filename = f"upload_{int(time.time())}"
        temp_path = os.path.join('uploads', safe_filename)
        file.save(temp_path)
        print(f"--- Uploading file: {safe_filename} ---", flush=True)
        await client.send_file('me', temp_path, caption=safe_filename)
        print(f"--- File upload successful: {safe_filename} ---", flush=True)
        return jsonify({"success": True, "message": "File uploaded!"})
    except Exception as e:
        print(f"!!! UPLOAD FAILED: {str(e)}", flush=True)
        traceback.print_exc(file=sys.stderr)
        return jsonify({"success": False, "message": f"Upload failed: {str(e)}"}), 500
    finally:
        if temp_path and os.path.exists(temp_path):
            try: os.remove(temp_path); print(f"--- Cleaned up temp file: {temp_path} ---", flush=True)
            except Exception as e_clean: print(f"!!! Error cleaning up temp file {temp_path}: {str(e_clean)}", flush=True)
        if client and client.is_connected():
            await client.disconnect()

# Wrap the Flask app (WSGI) so it can be served by an ASGI server (like Uvicorn)
asgi_app = WsgiToAsgi(app)

# --- 5. RUN THE APP (for local development) ---
if __name__ == '__main__':
    # Use host='0.0.0.0' to make it accessible on your local network
    # debug=True automatically reloads on code changes (use False for production)
    app.run(host='0.0.0.0', debug=True, port=5000)
```

### Next Steps

1.  **Push this code to GitHub:**
    ```bash
    git add app.py
    git commit -m "Add MORE detailed logging to login"
    git push origin master
    

