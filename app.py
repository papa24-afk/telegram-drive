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
# Note: On Render's free tier, this directory is temporary
os.makedirs('uploads', exist_ok=True)

# --- 2. WEB PAGE ROUTE ---
@app.route('/')
def home():
    # Renders the index.html file from the 'templates' folder
    return render_template('index.html')

# --- 3. HELPER FUNCTION ---
def get_client():
    """Creates a Telethon client from the user's browser session cookie."""
    session_string = session.get('telethon_session')
    # If session_string is None, StringSession handles it gracefully
    client = TelegramClient(StringSession(session_string), API_ID, API_HASH, loop=None)
    return client

# --- 4. API ROUTES ---
@app.route('/api/is_logged_in', methods=['GET'])
async def is_logged_in():
    """Checks if a valid session string exists in the user's cookie."""
    # Check if the specific key exists in the session dictionary
    return jsonify({"logged_in": 'telethon_session' in session})

@app.route('/api/send_code', methods=['POST'])
async def send_code():
    """Starts the login process by sending a code and storing temporary info in the cookie."""
    # Create a brand new, empty client for this specific request
    client = TelegramClient(StringSession(), API_ID, API_HASH, loop=None)
    try:
        await client.connect()
        phone = request.json['phone']
        print(f"--- Sending code to {phone} ---", flush=True)
        # Request the code from Telegram
        result = await client.send_code_request(phone)
        # Store necessary temporary info (hash and phone) in the session cookie
        session['temp_session_hash'] = result.phone_code_hash
        session['phone_number'] = phone
        print(f"--- Stored temp_session_hash: {result.phone_code_hash} ---", flush=True)
        return jsonify({"success": True, "message": "Code sent!"})
    except Exception as e:
        # Log any errors encountered during the process
        print(f"!!! SEND_CODE FAILED: {str(e)}", flush=True)
        traceback.print_exc(file=sys.stderr) # Print full traceback to stderr
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        # Ensure the client is always disconnected
        if client.is_connected():
            await client.disconnect()
            print("--- Send code client disconnected ---", flush=True)

@app.route('/api/login', methods=['POST'])
async def login():
    """Completes the login using the code (and password if needed) and saves the permanent session."""
    client = None # Define client outside try block for finally clause
    print("\n--- Attempting Login ---", flush=True)
    try:
        # Retrieve temporary data stored during send_code step
        phone_code_hash = session.get('temp_session_hash')
        phone_number = session.get('phone_number')

        # --- DETAILED LOGGING ---
        print(f"Retrieved from session - Phone Number: {phone_number}", flush=True)
        print(f"Retrieved from session - Phone Code Hash: {phone_code_hash}", flush=True)
        # --------------------------

        # If data is missing, the session might have expired or wasn't set correctly
        if not phone_code_hash or not phone_number:
            print("!!! LOGIN FAILED: Session data (phone_code_hash or phone_number) missing.", flush=True)
            return jsonify({"success": False, "message": "Session expired or invalid. Please request code again."}), 400

        # Create a new client instance for this login attempt
        client = TelegramClient(StringSession(), API_ID, API_HASH, loop=None)
        print("Connecting client for login...", flush=True)
        await client.connect()
        print("Client connected.", flush=True)

        # Get code and password (if provided) from the request
        code = request.json['code']
        password = request.json.get('password') # Optional password
        print(f"Code entered: {code}", flush=True)
        print(f"Password provided: {'Yes' if password else 'No'}", flush=True)

        # Attempt to sign in using the retrieved details
        print("Attempting initial sign in...", flush=True)
        await client.sign_in(
            phone=phone_number,
            code=code,
            phone_code_hash=phone_code_hash
        )
        print("Initial sign in successful (2FA not needed). Saving session...", flush=True)

        # If sign_in succeeds without SessionPasswordNeededError, 2FA was not required
        # Save the authenticated session string to the user's cookie
        session['telethon_session'] = client.session.save()
        # Remove temporary data now that login is complete
        session.pop('temp_session_hash', None)
        session.pop('phone_number', None)
        print("Session saved, temporary data cleared.", flush=True)
        return jsonify({"success": True, "message": "Login successful!"})

    except SessionPasswordNeededError:
        # This exception means Telegram requires a 2FA password
        print("--- 2FA Password needed ---", flush=True)
        password = request.json.get('password') # Check if password was sent in this request
        if not password:
            # If no password was sent, inform the frontend
            print("Password not provided in request, returning 2FA_REQUIRED.", flush=True)
            return jsonify({"success": False, "message": "2FA_REQUIRED"})

        # If password *was* provided, attempt to sign in again using it
        try:
            print("Attempting 2FA sign in with provided password...", flush=True)
            await client.sign_in(password=password)
            print("2FA sign in successful. Saving session...", flush=True)

            # On successful 2FA login, save the authenticated session
            session['telethon_session'] = client.session.save()
            # Clean up temporary data
            session.pop('temp_session_hash', None)
            session.pop('phone_number', None)
            print("Session saved after 2FA, temporary data cleared.", flush=True)
            return jsonify({"success": True, "message": "Login successful!"})

        except Exception as e_2fa:
            # Handle errors during the 2FA sign-in attempt (e.g., wrong password)
            print(f"!!! 2FA LOGIN FAILED: {str(e_2fa)}", flush=True)
            traceback.print_exc(file=sys.stderr)
            return jsonify({"success": False, "message": f"Login failed (2FA): {str(e_2fa)}"}), 500

    except Exception as e_main:
        # Handle any other unexpected errors during the initial sign-in attempt
        # Most likely: PhoneCodeExpiredError or PhoneCodeInvalidError
        print(f"!!! MAIN LOGIN FAILED: {str(e_main)}", flush=True)
        traceback.print_exc(file=sys.stderr)
        return jsonify({"success": False, "message": f"Login failed: {str(e_main)}"}), 500

    finally:
        # Crucial: Ensure the client is always disconnected after the request
        if client and client.is_connected():
            print("Disconnecting login client.", flush=True)
            await client.disconnect()
            print("Login client disconnected.", flush=True)


@app.route('/api/logout', methods=['POST'])
async def logout():
    """Logs the user out by clearing all relevant session data from their cookie."""
    try:
        print("--- Attempting Logout ---", flush=True)
        # Remove all session keys related to login state
        session.pop('telethon_session', None)
        session.pop('temp_session_hash', None)
        session.pop('phone_number', None)
        print("Session data cleared.", flush=True)
        return jsonify({"success": True, "message": "Logged out."})
    except Exception as e:
        print(f"!!! LOGOUT FAILED: {str(e)}", flush=True)
        return jsonify({"success": False, "message": str(e)}), 500

# --- (Rest of the API routes: /api/me, /api/thumbnail, /api/files, /api/download, /api/upload) ---
# --- Added basic logging and ensured disconnection in finally blocks ---

@app.route('/api/me')
async def get_me():
    """Gets profile info using the client session from the user's cookie."""
    client = get_client()
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return jsonify({"error": "Not logged in"}), 401 # Unauthorized

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
    """Gets a thumbnail for an image message."""
    client = get_client()
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return "Not authorized", 401

        message = await client.get_messages('me', ids=message_id)
        is_image = False
        if message and message.media:
            # Check if it's a photo or a document recognized as an image
            if hasattr(message.media, 'photo'): is_image = True
            elif hasattr(message.media, 'document') and message.media.document.mime_type and 'image' in message.media.document.mime_type: is_image = True
        if not is_image:
             return "Not an image file", 404 # Not Found

        # Download the smallest thumbnail available
        thumb_bytes = await client.download_media(message.media, thumb=-1, file=bytes)
        if not thumb_bytes:
            return "No thumbnail available", 404 # Not Found

        # Return the image bytes directly
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
    """Retrieves and categorizes files from the user's 'Saved Messages'."""
    client = get_client()
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return jsonify({"error": "Not logged in"}), 401

        # Initialize lists for different file categories
        files_images = []
        files_documents = []
        files_audio = []
        files_video = []
        files_compressed = []
        files_other = []

        search_query = request.args.get('search', None) # Get optional search term
        print(f"--- Fetching files (search: '{search_query if search_query else 'None'}') ---", flush=True)

        # Iterate through messages in 'Saved Messages' ('me')
        async for message in client.iter_messages('me', limit=200, search=search_query):
            if not message.media: continue # Skip messages without media

            # Basic file info structure
            file_info = {"id": message.id, "date": message.date.isoformat(), "name": f"file_{message.id}"}

            if hasattr(message.media, 'document'):
                doc = message.media.document
                # Try to get the actual filename
                if hasattr(doc, 'attributes'):
                    for attr in doc.attributes:
                        if hasattr(attr, 'file_name') and attr.file_name:
                            file_info['name'] = attr.file_name
                            break
                # Determine file type based on MIME type or extension
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
                     mime_type.startswith('application/msword') or \
                     mime_type.startswith('application/vnd.openxmlformats-officedocument.wordprocessingml') or \
                     mime_type.startswith('application/vnd.ms-excel') or \
                     mime_type.startswith('application/vnd.openxmlformats-officedocument.spreadsheetml') or \
                     mime_type.startswith('application/vnd.ms-powerpoint') or \
                     mime_type.startswith('application/vnd.openxmlformats-officedocument.presentationml') or \
                     name_lower.endswith(('.pdf', '.txt', '.csv', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.md')):
                    file_info['type'] = 'document'; files_documents.append(file_info)
                else:
                    file_info['type'] = 'other'; files_other.append(file_info)
            elif hasattr(message.media, 'photo'):
                # Treat photos as images
                file_info['name'] = f"photo_{message.id}.jpg"
                file_info['type'] = 'image'; files_images.append(file_info)

        print(f"--- Found files - Images: {len(files_images)}, Docs: {len(files_documents)}, Audio: {len(files_audio)}, Video: {len(files_video)}, Compressed: {len(files_compressed)}, Other: {len(files_other)} ---", flush=True)
        # Return categorized lists
        return jsonify({
            "images": files_images, "documents": files_documents, "audio": files_audio,
            "video": files_video, "compressed": files_compressed, "other": files_other
        })
    except Exception as e:
        print(f"!!! GET_FILES FAILED: {str(e)}", flush=True)
        traceback.print_exc(file=sys.stderr)
        return jsonify({"error": "Failed to retrieve files"}), 500
    finally:
        if client.is_connected():
            await client.disconnect()

@app.route('/api/download/<int:message_id>')
async def download_file(message_id):
    """Downloads the file associated with a specific message ID."""
    client = get_client()
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return "Not authorized", 401

        message = await client.get_messages('me', ids=message_id)
        if not message or not message.media:
            return "File not found in message", 404

        # Download the media into memory
        file_buffer = io.BytesIO()
        await client.download_media(message.media, file=file_buffer)
        file_buffer.seek(0) # Reset buffer position to the beginning

        # Determine a suitable filename for the download
        filename = f"download_{message_id}"
        if hasattr(message.media, 'document') and hasattr(message.media.document, 'attributes'):
             for attr in message.media.document.attributes:
                 if hasattr(attr, 'file_name') and attr.file_name:
                     filename = attr.file_name
                     break
        elif hasattr(message.media, 'photo'):
            filename = f"photo_{message_id}.jpg"

        print(f"--- Downloading file: {filename} (ID: {message_id}) ---", flush=True)
        # Send the file buffer as an attachment
        return send_file(
            file_buffer,
            download_name=filename, # Suggests the filename to the browser
            as_attachment=True      # Forces download instead of displaying in browser
        )
    except Exception as e:
        print(f"!!! DOWNLOAD FAILED (ID: {message_id}): {str(e)}", flush=True)
        traceback.print_exc(file=sys.stderr)
        return "Error processing download", 500
    finally:
        if client.is_connected():
            await client.disconnect()

@app.route('/api/upload', methods=['POST'])
async def upload_file():
    """Handles file uploads to the user's 'Saved Messages'."""
    client = get_client()
    temp_path = "" # Define outside try block for cleanup in finally
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return jsonify({"success": False, "message": "Not logged in"}), 401

        if 'file' not in request.files:
            return jsonify({"success": False, "message": "No file part in request"}), 400

        file = request.files['file']
        if not file or not file.filename:
            return jsonify({"success": False, "message": "No file selected"}), 400

        # Secure the filename and create a temporary path
        from werkzeug.utils import secure_filename
        safe_filename = secure_filename(file.filename)
        if not safe_filename: # Handle cases with no filename
            import time
            safe_filename = f"upload_{int(time.time())}"

        temp_path = os.path.join('uploads', safe_filename)
        # Save the uploaded file temporarily on the server
        file.save(temp_path)
        print(f"--- Uploading file: {safe_filename} ---", flush=True)
        # Send the temporarily saved file to Telegram 'Saved Messages'
        await client.send_file('me', temp_path, caption=safe_filename)
        print(f"--- File upload successful: {safe_filename} ---", flush=True)

        return jsonify({"success": True, "message": "File uploaded!"})
    except Exception as e:
        print(f"!!! UPLOAD FAILED: {str(e)}", flush=True)
        traceback.print_exc(file=sys.stderr)
        return jsonify({"success": False, "message": f"Upload failed: {str(e)}"}), 500
    finally:
        # Crucial: Clean up the temporarily saved file from the server
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
                print(f"--- Cleaned up temp file: {temp_path} ---", flush=True)
            except Exception as e_clean:
                # Log error if cleanup fails, but don't crash the request
                print(f"!!! Error cleaning up temp file {temp_path}: {str(e_clean)}", flush=True)
        # Ensure client disconnects
        if client and client.is_connected():
            await client.disconnect()

# Wrap the Flask app (WSGI) so it can be served by an ASGI server (like Uvicorn)
asgi_app = WsgiToAsgi(app)

# --- 5. RUN THE APP (for local development) ---
if __name__ == '__main__':
    # Use host='0.0.0.0' to make it accessible on your local network
    # debug=True automatically reloads on code changes (use False for production)
    # The port Render uses is often different, but 5000 is standard for local Flask dev
    app.run(host='0.0.0.0', debug=True, port=5000)
```

### Next Steps

1.  **Push this code to GitHub:**
    ```bash
    git add app.py
    git commit -m "Add MORE detailed logging to login"
    git push origin master
    

