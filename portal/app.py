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

# --- Logging, Flask App, Config, LoginManager, Bcrypt Setup ---
# (Keep the existing setup code for logging, Flask, OBS_ENGINE_API_URL, Login, Bcrypt)
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
                    stream=sys.stdout)
logger = logging.getLogger("obs_engine_be")
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'obs-engine-be-secret-key-change-me!')
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=7)
OBS_ENGINE_API_URL = os.environ.get("OBS_ENGINE_API_URL")
if not OBS_ENGINE_API_URL: logger.critical("OBS_ENGINE_API_URL environment variable is not set!")
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Vui lòng đăng nhập để truy cập trang này."
login_manager.login_message_category = "info"
bcrypt = Bcrypt(app)
BE_DB_PATH = os.environ.get("BE_DB_PATH", "/auth-data/be_users.db")
# (Keep User DB functions: get_be_db_connection, init_be_db, User class, load_user)
# ... (User DB functions from previous response) ...
def get_be_db_connection():
    try:
        db_dir = os.path.dirname(BE_DB_PATH)
        if not os.path.exists(db_dir): os.makedirs(db_dir)
        conn = sqlite3.connect(BE_DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e: logger.error(f"BE User DB connection error: {e}", exc_info=True); return None

def init_be_db():
    conn = get_be_db_connection()
    if conn:
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL)')
                username_to_create = 'khalc'; password_to_create = 'chaukha'
                cursor.execute("SELECT COUNT(*) FROM users WHERE username = ?", (username_to_create,)); user_count = cursor.fetchone()[0]
                if user_count == 0:
                    try: hashed_password = bcrypt.generate_password_hash(password_to_create).decode('utf-8'); cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username_to_create, hashed_password)); logger.info(f"Created default BE user '{username_to_create}'.")
                    except sqlite3.IntegrityError: logger.warning(f"Default user '{username_to_create}' already exists.")
                    except Exception as insert_err: logger.error(f"Failed to insert default user '{username_to_create}': {insert_err}", exc_info=True)
                else: logger.info(f"Default user '{username_to_create}' already exists.")
            logger.info("BE User DB initialized check complete."); return True
        except Exception as e: logger.error(f"BE User DB init error: {e}", exc_info=True); return False
        finally:
             if conn: conn.close()
    return False
init_be_db()
class User(UserMixin):
    def __init__(self, id, username, password_hash): self.id = id; self.username = username; self.password = password_hash
    @staticmethod
    def get(user_id):
        conn = get_be_db_connection(); user_data = None
        if conn:
            try: cursor = conn.cursor(); cursor.execute("SELECT id, username, password FROM users WHERE id = ?", (user_id,)); user_data = cursor.fetchone()
            except Exception as e: logger.error(f"Error getting BE user {user_id}: {e}")
            finally: conn.close()
        if user_data: return User(id=user_data['id'], username=user_data['username'], password_hash=user_data['password'])
        return None
    @staticmethod
    def find_by_username(username):
        conn = get_be_db_connection(); user_data = None
        if conn:
            try: cursor = conn.cursor(); cursor.execute("SELECT id, username, password FROM users WHERE username = ?", (username,)); user_data = cursor.fetchone()
            except Exception as e: logger.error(f"Error finding BE user {username}: {e}")
            finally: conn.close()
        if user_data: return User(id=user_data['id'], username=user_data['username'], password_hash=user_data['password'])
        return None
@login_manager.user_loader
def load_user(user_id): return User.get(user_id)
# --- End User DB functions ---


# --- Helper function to call ObsEngine API (Keep as is) ---
def call_obs_engine_api(endpoint, method='GET', params=None, json_data=None):
    # (Keep existing call_obs_engine_api logic from previous response)
    if not OBS_ENGINE_API_URL: logger.error("ObsEngine API URL is not configured."); return None, 503
    url = f"{OBS_ENGINE_API_URL}{endpoint}"; headers = {'Accept': 'application/json'}
    if method == 'POST' and json_data: headers['Content-Type'] = 'application/json'
    logger.debug(f"Calling ObsEngine API: {method} {url} | Params: {params} | JSON: {json.dumps(json_data) if json_data else 'None'}")
    try:
        response = requests.request(method, url, params=params, json=json_data, headers=headers, timeout=45)
        if response.status_code == 401: logger.warning(f"Unauthorized call to ObsEngine API: {method} {url}"); return {"error": "Unauthorized access to backend service"}, 401
        response.raise_for_status()
        if response.status_code != 204 and response.content:
            try: return response.json(), response.status_code
            except json.JSONDecodeError as json_err: logger.error(f"Failed to decode JSON response from {method} {url}: {json_err}. Response text: {response.text[:500]}"); return {"error": "Invalid JSON response from ObsEngine"}, 500
        else: return {}, response.status_code
    except requests.exceptions.Timeout: logger.error(f"Timeout calling ObsEngine API: {method} {url}"); return {"error": "ObsEngine API request timed out"}, 504
    except requests.exceptions.RequestException as e:
        status_code = e.response.status_code if e.response is not None else 503; error_detail = str(e)
        if e.response is not None:
            try: err_json = e.response.json(); error_detail = err_json.get("error", str(e))
            except (json.JSONDecodeError, ValueError): error_detail = e.response.text[:200] + ('...' if len(e.response.text) > 200 else '')
        logger.error(f"Error calling ObsEngine API: {method} {url} - Status: {status_code} - Error: {error_detail}")
        return {"error": f"Failed to communicate with ObsEngine: {error_detail}"}, status_code
    except Exception as e: logger.error(f"Unexpected error calling ObsEngine API: {method} {url} - Error: {e}", exc_info=True); return {"error": "Unexpected internal error"}, 500


# --- Main Routes (Keep index, login, logout as is) ---
@app.route('/')
@login_required
def index():
    db_path_display = BE_DB_PATH if os.path.exists(BE_DB_PATH) else "DB Not Found"
    return render_template('index.html', app_title="Obs Engine", db_path=db_path_display)

@app.route('/login', methods=['GET', 'POST'])
def login():
    # (Keep existing login logic)
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username'); password = request.form.get('password'); remember = bool(request.form.get('remember'))
        user = User.find_by_username(username)
        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user, remember=remember); next_page = request.args.get('next')
            if next_page and not next_page.startswith('/'): next_page = url_for('index')
            logger.info(f"User '{username}' logged in successfully."); return redirect(next_page or url_for('index'))
        else: logger.warning(f"Failed login attempt for username: '{username}'"); flash('Sai tên đăng nhập hoặc mật khẩu.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    # (Keep existing logout logic)
    logger.info(f"User '{current_user.username}' logging out."); logout_user(); flash('Bạn đã đăng xuất.', 'info'); return redirect(url_for('login'))

# --- API Proxy Routes (Keep Incidents, Stats, Agent Status, Namespaces as is) ---
@app.route('/api/incidents')
@login_required
def get_incidents():
    params = request.args.to_dict(); logger.info(f"Forwarding request to ObsEngine: GET /api/incidents | Params: {params}"); data, status_code = call_obs_engine_api('/api/incidents', params=params); return jsonify(data), status_code

@app.route('/api/stats')
@login_required
def get_stats():
    params = request.args.to_dict(); logger.info(f"Forwarding request to ObsEngine: GET /api/stats | Params: {params}"); data, status_code = call_obs_engine_api('/api/stats', params=params); return jsonify(data), status_code

@app.route('/api/agents/status')
@login_required
def get_agents_status():
    logger.info("Forwarding request to ObsEngine: GET /api/agents/status"); data, status_code = call_obs_engine_api('/api/agents/status'); return jsonify(data), status_code

@app.route('/api/namespaces')
@login_required
def get_available_namespaces():
    # (Keep existing namespaces proxy logic)
    logger.info("Forwarding request to ObsEngine: GET /api/namespaces"); data, status_code = call_obs_engine_api('/api/namespaces')
    if status_code == 200 and not isinstance(data, list): logger.warning(f"ObsEngine /api/namespaces did not return a list: {data}"); return jsonify([]), 200
    elif status_code != 200 and 'error' in data: return jsonify(data), 502 # Bad Gateway
    return jsonify(data), status_code

# --- Global Config Proxy Route (Keep as is, handles GET/POST for global AI/Telegram) ---
@app.route('/api/config/<config_type>', methods=['GET', 'POST'])
@login_required
def config_route(config_type):
    # (Keep existing global config proxy logic from previous response)
    valid_get_types = ['all', 'general', 'ai', 'telegram', 'monitored_namespaces'] # 'general' and 'monitored_namespaces' are now synthesized from 'all'
    valid_post_types = ['ai', 'telegram'] # Only allow POST for global AI and Telegram

    if request.method == 'GET':
        if config_type not in valid_get_types: return jsonify({"error": "Invalid config type requested for GET"}), 404
        logger.info(f"Forwarding GET request to ObsEngine: /api/config/all (to synthesize {config_type})")
        all_config_data, status_code = call_obs_engine_api('/api/config/all')
        if status_code != 200: return jsonify(all_config_data), status_code
        if config_type == 'all': return jsonify(all_config_data), 200
        extracted_data = {}
        if config_type == 'general': # Synthesize 'general' - these are now agent-specific, return empty or defaults? Let's return empty.
             extracted_data = {} # No longer global
        elif config_type == 'ai':
            keys_to_extract = ['enable_ai_analysis', 'ai_provider', 'ai_model_identifier']; extracted_data = {k: all_config_data.get(k) for k in keys_to_extract if k in all_config_data}
        elif config_type == 'telegram':
            keys_to_extract = ['enable_telegram_alerts', 'telegram_chat_id']; extracted_data = {k: all_config_data.get(k) for k in keys_to_extract if k in all_config_data}
            extracted_data['has_token'] = bool(all_config_data.get('telegram_chat_id')) # Simplified check
        elif config_type == 'monitored_namespaces': # Synthesize 'monitored_namespaces' - no longer global
            extracted_data = [] # No longer global
        return jsonify(extracted_data), 200

    elif request.method == 'POST':
        if config_type not in valid_post_types: return jsonify({"error": f"POST not allowed for config type '{config_type}'"}), 405 # Method Not Allowed
        if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
        json_data = request.get_json()
        logger.info(f"Forwarding POST request to ObsEngine: /api/config/{config_type} | Data: {json.dumps(json_data)}")
        data, status_code = call_obs_engine_api(f'/api/config/{config_type}', method='POST', json_data=json_data)
        return jsonify(data), status_code
    else: return jsonify({"error": "Method not allowed"}), 405


# --- NEW Agent-Specific Config Proxy Routes ---

@app.route('/api/agents/<agent_id>/config', methods=['GET'])
@login_required
def get_agent_config_proxy(agent_id):
    """Proxies GET request for a specific agent's config to ObsEngine."""
    if not agent_id: return jsonify({"error": "Agent ID is required"}), 400
    endpoint = f'/api/agents/{agent_id}/config'
    logger.info(f"Forwarding request to ObsEngine: GET {endpoint}")
    data, status_code = call_obs_engine_api(endpoint, method='GET')
    return jsonify(data), status_code

@app.route('/api/agents/<agent_id>/config/general', methods=['POST'])
@login_required
def save_agent_general_config_proxy(agent_id):
    """Proxies POST request to save general config for a specific agent."""
    if not agent_id: return jsonify({"error": "Agent ID is required"}), 400
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    json_data = request.get_json()
    endpoint = f'/api/agents/{agent_id}/config/general'
    logger.info(f"Forwarding request to ObsEngine: POST {endpoint} | Data: {json.dumps(json_data)}")
    data, status_code = call_obs_engine_api(endpoint, method='POST', json_data=json_data)
    return jsonify(data), status_code

@app.route('/api/agents/<agent_id>/config/namespaces', methods=['POST'])
@login_required
def save_agent_namespaces_proxy(agent_id):
    """Proxies POST request to save monitored namespaces for a specific agent."""
    if not agent_id: return jsonify({"error": "Agent ID is required"}), 400
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    json_data = request.get_json()
    endpoint = f'/api/agents/{agent_id}/config/namespaces'
    logger.info(f"Forwarding request to ObsEngine: POST {endpoint} | Data: {json.dumps(json_data)}")
    data, status_code = call_obs_engine_api(endpoint, method='POST', json_data=json_data)
    return jsonify(data), status_code


# --- Main Execution Guard ---
if __name__ == '__main__':
    flask_port = int(os.environ.get("FLASK_PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    logger.info(f"Starting Obs-Engine-BE (Portal) Flask server on 0.0.0.0:{flask_port} | Debug: {debug_mode}")
    app.run(host='0.0.0.0', port=flask_port, debug=debug_mode, use_reloader=debug_mode)
