# ai-agent1/portal/app.py

import os
import logging
import requests
import sqlite3
from flask import (Flask, jsonify, render_template, request, redirect,
                   url_for, flash, session)
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                       login_required, current_user)
from flask_bcrypt import Bcrypt
from datetime import timedelta
import json
import sys

# --- Cấu hình Logging ---
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
                    stream=sys.stdout)
logger = logging.getLogger("obs_engine_be") # Đổi tên logger cho rõ ràng

# --- Cấu hình Flask ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'obs-engine-be-secret-key-change-me!')
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=7)

# --- Get ObsEngine API URL from Environment Variable ---
OBS_ENGINE_API_URL = os.environ.get("OBS_ENGINE_API_URL")
if not OBS_ENGINE_API_URL:
    logger.critical("OBS_ENGINE_API_URL environment variable is not set! Backend cannot function.")
    # Consider exiting or providing a default for local testing if applicable
    # sys.exit("Missing OBS_ENGINE_API_URL")

# --- Khởi tạo Flask-Login và Bcrypt ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Vui lòng đăng nhập để truy cập trang này."
login_manager.login_message_category = "info"
bcrypt = Bcrypt(app)

# --- User Management (Using Local DB) ---
BE_DB_PATH = os.environ.get("BE_DB_PATH", "/auth-data/be_users.db")
logger.info(f"Using BE User DB Path: {BE_DB_PATH}")

def get_be_db_connection():
    """Establishes connection to the BE user database."""
    try:
        db_dir = os.path.dirname(BE_DB_PATH)
        if not os.path.exists(db_dir):
             os.makedirs(db_dir)
             logger.info(f"Created directory for BE user database: {db_dir}")
        conn = sqlite3.connect(BE_DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row # Return rows as dictionary-like objects
        return conn
    except Exception as e:
        logger.error(f"BE User DB connection error: {e}", exc_info=True)
        return None

def init_be_db():
    """Initializes the BE user database and creates the default user if needed."""
    conn = get_be_db_connection()
    if conn:
        try:
            with conn: # Use context manager for automatic commit/rollback
                cursor = conn.cursor()
                # Create users table if it doesn't exist
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        password TEXT NOT NULL
                    )
                ''')
                # Check and create default user 'khalc'
                username_to_create = 'khalc'
                password_to_create = 'chaukha' # Consider making this configurable or more secure
                cursor.execute("SELECT COUNT(*) FROM users WHERE username = ?", (username_to_create,))
                user_count = cursor.fetchone()[0]

                if user_count == 0:
                    logger.info(f"Default user '{username_to_create}' not found. Attempting to create...")
                    try:
                        hashed_password = bcrypt.generate_password_hash(password_to_create).decode('utf-8')
                        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username_to_create, hashed_password))
                        logger.info(f"Created default BE user '{username_to_create}'.")
                    except sqlite3.IntegrityError:
                        # Handle race condition if another worker created the user
                        logger.warning(f"Default user '{username_to_create}' already exists (likely created by another worker).")
                    except Exception as insert_err:
                         logger.error(f"Failed to insert default user '{username_to_create}': {insert_err}", exc_info=True)
                else:
                     logger.info(f"Default user '{username_to_create}' already exists.")

            logger.info("BE User DB initialized check complete.")
            return True
        except Exception as e:
            logger.error(f"BE User DB init error: {e}", exc_info=True)
            return False
        finally:
             if conn: conn.close() # Ensure connection is closed
    return False

# Initialize the database on startup
init_be_db()

class User(UserMixin):
    """User class for Flask-Login."""
    def __init__(self, id, username, password_hash):
        self.id = id
        self.username = username
        self.password = password_hash # Store the hash

    @staticmethod
    def get(user_id):
        """Gets a user by their ID."""
        conn = get_be_db_connection()
        user_data = None
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT id, username, password FROM users WHERE id = ?", (user_id,))
                user_data = cursor.fetchone()
            except Exception as e:
                 logger.error(f"Error getting BE user {user_id}: {e}")
            finally:
                if conn: conn.close()
        if user_data:
            # Create User object with the stored password hash
            return User(id=user_data['id'], username=user_data['username'], password_hash=user_data['password'])
        return None

    @staticmethod
    def find_by_username(username):
        """Finds a user by their username."""
        conn = get_be_db_connection()
        user_data = None
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT id, username, password FROM users WHERE username = ?", (username,))
                user_data = cursor.fetchone()
            except Exception as e:
                 logger.error(f"Error finding BE user {username}: {e}")
            finally:
                 if conn: conn.close()
        if user_data:
            # Create User object with the stored password hash
            return User(id=user_data['id'], username=user_data['username'], password_hash=user_data['password'])
        return None


@login_manager.user_loader
def load_user(user_id):
    """Flask-Login user loader callback."""
    return User.get(user_id)

def call_obs_engine_api(endpoint, method='GET', params=None, json_data=None):
    """Helper function to call the ObsEngine API."""
    if not OBS_ENGINE_API_URL:
        logger.error("ObsEngine API URL is not configured.")
        return None, 503 # Service Unavailable

    url = f"{OBS_ENGINE_API_URL}{endpoint}"
    headers = {'Accept': 'application/json'}
    if method == 'POST' and json_data:
        headers['Content-Type'] = 'application/json'

    logger.debug(f"Calling ObsEngine API: {method} {url} | Params: {params} | JSON: {json.dumps(json_data) if json_data else 'None'}") # Log JSON safely

    try:
        response = requests.request(
            method, url, params=params, json=json_data, headers=headers, timeout=45
        )
        # Check for specific error codes first
        if response.status_code == 401:
             logger.warning(f"Unauthorized call to ObsEngine API: {method} {url}")
             return {"error": "Unauthorized access to backend service"}, 401
        # Raise exceptions for other bad responses (4xx, 5xx)
        response.raise_for_status()

        # Handle successful responses
        if response.status_code != 204 and response.content: # 204 No Content
            try:
                return response.json(), response.status_code
            except json.JSONDecodeError as json_err:
                 logger.error(f"Failed to decode JSON response from {method} {url}: {json_err}. Response text: {response.text[:500]}")
                 return {"error": "Invalid JSON response from ObsEngine"}, 500
        else:
            # Success but no content (e.g., 204) or empty content
            return {}, response.status_code
    except requests.exceptions.Timeout:
        logger.error(f"Timeout calling ObsEngine API: {method} {url}")
        return {"error": "ObsEngine API request timed out"}, 504 # Gateway Timeout
    except requests.exceptions.RequestException as e:
        status_code = e.response.status_code if e.response is not None else 503 # Default to Service Unavailable
        error_detail = str(e)
        # Try to get more specific error from response body
        if e.response is not None:
            try:
                err_json = e.response.json()
                error_detail = err_json.get("error", str(e))
            except (json.JSONDecodeError, ValueError): # Handle cases where response is not JSON
                error_detail = e.response.text[:200] + ('...' if len(e.response.text) > 200 else '')
        logger.error(f"Error calling ObsEngine API: {method} {url} - Status: {status_code} - Error: {error_detail}")
        return {"error": f"Failed to communicate with ObsEngine: {error_detail}"}, status_code
    except Exception as e:
         # Catch any other unexpected errors
         logger.error(f"Unexpected error calling ObsEngine API: {method} {url} - Error: {e}", exc_info=True)
         return {"error": "Unexpected internal error"}, 500


# --- Main Routes ---

@app.route('/')
@login_required
def index():
    """Serves the main index page."""
    # Pass DB path to template (optional, for display only)
    db_path_display = BE_DB_PATH if os.path.exists(BE_DB_PATH) else "DB Not Found"
    return render_template('index.html', app_title="Bug Tracker", db_path=db_path_display)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handles user login."""
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = True if request.form.get('remember') else False

        user = User.find_by_username(username)

        # Check if user exists and password is correct
        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user, remember=remember)
            next_page = request.args.get('next')
            # Basic security check for redirect
            if next_page and not next_page.startswith('/'):
                next_page = url_for('index')
            logger.info(f"User '{username}' logged in successfully.")
            return redirect(next_page or url_for('index'))
        else:
            logger.warning(f"Failed login attempt for username: '{username}'")
            flash('Sai tên đăng nhập hoặc mật khẩu.', 'danger') # Use 'danger' category for errors

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """Handles user logout."""
    logger.info(f"User '{current_user.username}' logging out.")
    logout_user()
    flash('Bạn đã đăng xuất.', 'info')
    return redirect(url_for('login'))

# --- API Proxy Routes ---

@app.route('/api/incidents')
@login_required
def get_incidents():
    """Proxies the request to ObsEngine's /api/incidents."""
    params = request.args.to_dict()
    logger.info(f"Forwarding request to ObsEngine: GET /api/incidents | Params: {params}")
    data, status_code = call_obs_engine_api('/api/incidents', params=params)
    return jsonify(data), status_code

@app.route('/api/stats')
@login_required
def get_stats():
    """Proxies the request to ObsEngine's /api/stats."""
    params = request.args.to_dict()
    logger.info(f"Forwarding request to ObsEngine: GET /api/stats | Params: {params}")
    data, status_code = call_obs_engine_api('/api/stats', params=params)
    return jsonify(data), status_code

@app.route('/api/agents/status')
@login_required
def get_agents_status():
    """Proxies the request to ObsEngine's /api/agents/status."""
    logger.info("Forwarding request to ObsEngine: GET /api/agents/status")
    data, status_code = call_obs_engine_api('/api/agents/status')
    return jsonify(data), status_code

@app.route('/api/namespaces')
@login_required
def get_available_namespaces():
    """Proxies the request to ObsEngine's /api/namespaces."""
    logger.info("Forwarding request to ObsEngine: GET /api/namespaces")
    data, status_code = call_obs_engine_api('/api/namespaces')
    # Ensure the response is a list, even if ObsEngine returns something else on error
    if status_code == 200 and not isinstance(data, list):
        logger.warning(f"ObsEngine /api/namespaces did not return a list: {data}")
        return jsonify([]), 200 # Return empty list instead of original data
    elif status_code != 200 and 'error' in data:
         # If there was an error calling obs-engine, return the error but with a 502 status
         return jsonify(data), 502 # Bad Gateway
    return jsonify(data), status_code

# --- UPDATED CONFIG ROUTE ---
@app.route('/api/config/<config_type>', methods=['GET', 'POST']) # Allow GET and POST
@login_required
def config_route(config_type):
    """Handles GET (fetch) and POST (save) for configuration sections."""
    valid_get_types = ['all', 'general', 'ai', 'telegram', 'monitored_namespaces']
    valid_post_types = ['general', 'ai', 'telegram', 'monitored_namespaces']

    if request.method == 'GET':
        if config_type not in valid_get_types:
            return jsonify({"error": "Invalid config type requested for GET"}), 404

        logger.info(f"Forwarding GET request to ObsEngine: /api/config/{config_type}")
        # Always fetch 'all' config from obsengine for GET requests
        all_config_data, status_code = call_obs_engine_api('/api/config/all')

        if status_code != 200:
            # Pass through the error from obsengine
            return jsonify(all_config_data), status_code

        # If the request was specifically for 'all', return everything
        if config_type == 'all':
            return jsonify(all_config_data), 200

        # Extract specific sections for other GET requests
        extracted_data = {}
        if config_type == 'general':
            keys_to_extract = ['scan_interval_seconds', 'restart_count_threshold',
                               'loki_scan_min_level', 'alert_cooldown_minutes',
                               'alert_severity_levels', 'alert_severity_levels_str']
            for key in keys_to_extract:
                if key in all_config_data:
                    extracted_data[key] = all_config_data[key]
        elif config_type == 'ai':
            keys_to_extract = ['enable_ai_analysis', 'ai_provider', 'ai_model_identifier']
            for key in keys_to_extract:
                 if key in all_config_data:
                    extracted_data[key] = all_config_data[key]
            # Note: API key is never returned by /all
        elif config_type == 'telegram':
            keys_to_extract = ['enable_telegram_alerts', 'telegram_chat_id']
            for key in keys_to_extract:
                 if key in all_config_data:
                    extracted_data[key] = all_config_data[key]
            # Add 'has_token' based on whether the field exists (even if empty) in the /all response
            # Obsengine /all doesn't return the token itself, so we infer based on chat_id presence maybe?
            # A better approach would be for obsengine /api/config/all to return a boolean like 'has_telegram_token'
            # For now, let's assume if chat_id is there, token might be configured.
            extracted_data['has_token'] = bool(all_config_data.get('telegram_chat_id')) # Simplified check
        elif config_type == 'monitored_namespaces':
             # Obsengine /api/config/all returns 'monitored_namespaces' as JSON string
             ns_json_string = all_config_data.get('monitored_namespaces')
             if ns_json_string:
                 try:
                     extracted_data = json.loads(ns_json_string)
                     if not isinstance(extracted_data, list):
                          extracted_data = [] # Ensure it's a list
                 except json.JSONDecodeError:
                      logger.warning(f"Could not parse monitored_namespaces JSON from obsengine: {ns_json_string}")
                      extracted_data = []
             else:
                 extracted_data = [] # Return empty list if key missing

        return jsonify(extracted_data), 200

    elif request.method == 'POST':
        if config_type not in valid_post_types:
            return jsonify({"error": "Invalid config type requested for POST"}), 404

        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400
        json_data = request.get_json()

        # Forward the POST request directly to the corresponding obsengine endpoint
        logger.info(f"Forwarding POST request to ObsEngine: /api/config/{config_type} | Data: {json.dumps(json_data)}")
        data, status_code = call_obs_engine_api(f'/api/config/{config_type}', method='POST', json_data=json_data)
        return jsonify(data), status_code

    else:
        # Should not happen with the methods=['GET', 'POST'] definition
        return jsonify({"error": "Method not allowed"}), 405


# --- Main Execution Guard ---
if __name__ == '__main__':
    flask_port = int(os.environ.get("FLASK_PORT", 5000)) # Default to 5000 for portal
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    logger.info(f"Starting Obs-Engine-BE (Portal) Flask server on 0.0.0.0:{flask_port} | Debug: {debug_mode}")
    # Use use_reloader=False when running with Gunicorn or in production
    app.run(host='0.0.0.0', port=flask_port, debug=debug_mode, use_reloader=debug_mode)
