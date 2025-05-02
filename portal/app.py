# ai-agent1/portal/app.py
import os
import logging
import requests
import sqlite3
from flask import (Flask, jsonify, render_template, request, redirect,
                   url_for, flash, session, abort)
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                       login_required, current_user)
from flask_bcrypt import Bcrypt
from datetime import timedelta
import json
import sys
from functools import wraps

# --- Logging, Flask App, Config, LoginManager, Bcrypt Setup ---
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

# --- User Database Functions ---
def get_be_db_connection():
    """Gets a connection to the Portal's user database."""
    try:
        db_dir = os.path.dirname(BE_DB_PATH)
        if db_dir and not os.path.exists(db_dir):
             os.makedirs(db_dir)
             logger.info(f"Created BE database directory: {db_dir}")
        conn = sqlite3.connect(BE_DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.error(f"BE User DB connection error: {e}", exc_info=True)
        return None

def init_be_db():
    """Initializes the Portal's user database schema."""
    conn = get_be_db_connection()
    if conn:
        try:
            with conn:
                cursor = conn.cursor()
                logger.info("Initializing BE User DB Schema...")
                # Ensure users table exists WITHOUT avatar_url
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        password TEXT NOT NULL,
                        role TEXT NOT NULL DEFAULT 'user',
                        fullname TEXT
                        -- REMOVED: avatar_url TEXT
                    )''')
                logger.info("Table 'users' ensured (without avatar_url).")

                # Check if columns exist before altering (still useful)
                table_info = cursor.execute("PRAGMA table_info(users)").fetchall()
                column_names = [col['name'] for col in table_info]

                if 'role' not in column_names:
                    cursor.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
                    logger.info("Added 'role' column to users table.")
                if 'fullname' not in column_names:
                    cursor.execute("ALTER TABLE users ADD COLUMN fullname TEXT")
                    logger.info("Added 'fullname' column to users table.")
                # REMOVED: Check and add avatar_url column

                # Create default admin user if not exists
                username_to_create = 'khalc'
                password_to_create = 'chaukha'
                cursor.execute("SELECT COUNT(*) FROM users WHERE username = ?", (username_to_create,))
                user_count = cursor.fetchone()[0]

                if user_count == 0:
                    try:
                        hashed_password = bcrypt.generate_password_hash(password_to_create).decode('utf-8')
                        # Insert WITHOUT avatar URL
                        cursor.execute("""
                            INSERT INTO users (username, password, role, fullname)
                            VALUES (?, ?, ?, ?)
                        """, (username_to_create, hashed_password, 'admin', 'Administrator'))
                        logger.info(f"Created default BE admin user '{username_to_create}'.")
                    except sqlite3.IntegrityError:
                        logger.warning(f"Default user '{username_to_create}' already exists (IntegrityError).")
                    except Exception as insert_err:
                        logger.error(f"Failed to insert default user '{username_to_create}': {insert_err}", exc_info=True)
                else:
                    # Ensure the default user is admin
                    cursor.execute("SELECT role FROM users WHERE username = ?", (username_to_create,))
                    current_role_row = cursor.fetchone()
                    if current_role_row and current_role_row['role'] != 'admin':
                        cursor.execute("UPDATE users SET role = 'admin' WHERE username = ?", (username_to_create,))
                        if cursor.rowcount > 0:
                            logger.info(f"Updated role for existing user '{username_to_create}' to 'admin'.")
                        else:
                            logger.debug(f"Could not update role for user '{username_to_create}'.")
                    elif current_role_row and current_role_row['role'] == 'admin':
                         logger.debug(f"User '{username_to_create}' already exists with admin role.")
                    else:
                         logger.warning(f"User '{username_to_create}' not found for role check/update.")


            logger.info("BE User DB initialized check complete.")
            return True
        except sqlite3.OperationalError as oe:
             if "duplicate column name" in str(oe):
                 logger.warning(f"BE User DB init: {oe}. This is expected if columns already exist.")
                 return True
             else:
                 logger.error(f"BE User DB operational error during init: {oe}", exc_info=True)
                 return False
        except Exception as e:
            logger.error(f"BE User DB init error: {e}", exc_info=True)
            return False
        finally:
            if conn: conn.close()
    return False

# Run DB initialization on startup
init_be_db()

# --- User Model ---
class User(UserMixin):
    # REMOVED avatar_url from __init__ parameters and self.avatar_url assignment
    def __init__(self, id, username, password_hash, role='user', fullname=None):
        self.id = id
        self.username = username
        self.password = password_hash
        self.role = role or 'user'
        self.fullname = fullname
        # No self.avatar_url assignment needed

    @staticmethod
    def get(user_id):
        conn = get_be_db_connection()
        user_data = None
        if conn:
            try:
                cursor = conn.cursor()
                # Select necessary columns (password hash for login check)
                # REMOVED avatar_url from SELECT
                cursor.execute("SELECT id, username, password, role, fullname FROM users WHERE id = ?", (user_id,))
                user_data = cursor.fetchone()
            except Exception as e:
                logger.error(f"Error getting BE user {user_id}: {e}")
            finally:
                conn.close()
        if user_data:
            # REMOVED avatar_url from User instantiation
            return User(
                id=user_data['id'],
                username=user_data['username'],
                password_hash=user_data['password'],
                role=user_data['role'],
                fullname=user_data['fullname']
            )
        return None

    @staticmethod
    def find_by_username(username):
        conn = get_be_db_connection()
        user_data = None
        if conn:
            try:
                cursor = conn.cursor()
                # Select necessary columns
                # REMOVED avatar_url from SELECT
                cursor.execute("SELECT id, username, password, role, fullname FROM users WHERE username = ?", (username,))
                user_data = cursor.fetchone()
            except Exception as e:
                logger.error(f"Error finding BE user {username}: {e}")
            finally:
                conn.close()
        if user_data:
            # REMOVED avatar_url from User instantiation
            return User(
                id=user_data['id'],
                username=user_data['username'],
                password_hash=user_data['password'],
                role=user_data['role'],
                fullname=user_data['fullname']
            )
        return None

    @staticmethod
    def get_all():
        """Fetches all users from the database."""
        conn = get_be_db_connection()
        users = []
        if conn:
            try:
                cursor = conn.cursor()
                # Select non-sensitive columns
                # REMOVED avatar_url from SELECT
                cursor.execute("SELECT id, username, role, fullname FROM users ORDER BY username")
                rows = cursor.fetchall()
                # REMOVED default_avatar logic
                for row in rows:
                    users.append({
                        "id": row['id'],
                        "username": row['username'],
                        "role": row['role'],
                        "fullname": row['fullname']
                        # REMOVED "avatar_url": default_avatar
                    })
            except Exception as e:
                logger.error(f"Error getting all BE users: {e}")
            finally:
                conn.close()
        return users

# --- Flask-Login Loader ---
@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

# --- Admin Required Decorator ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            logger.warning(f"Unauthorized access attempt by user '{current_user.username if current_user.is_authenticated else 'anonymous'}' to admin route {request.path}")
            if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                 return jsonify({"error": "Admin privileges required"}), 403
            else:
                flash("Bạn không có quyền truy cập vào trang này.", "danger")
                return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# --- Helper function to call ObsEngine API ---
def call_obs_engine_api(endpoint, method='GET', params=None, json_data=None):
    # (Keep existing call_obs_engine_api logic - no changes needed here)
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


# --- Main Routes ---
@app.route('/')
@login_required
def index():
    db_path_display = BE_DB_PATH if os.path.exists(BE_DB_PATH) else "DB Not Found"
    return render_template('index.html',
                           app_title="Obs Engine",
                           db_path=db_path_display,
                           current_user=current_user)

@app.route('/login', methods=['GET', 'POST'])
def login():
    # (Keep existing login logic)
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username'); password = request.form.get('password'); remember = bool(request.form.get('remember'))
        user = User.find_by_username(username)
        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user, remember=remember); next_page = request.args.get('next')
            if next_page and not next_page.startswith('/') and not next_page.startswith(request.host_url):
                next_page = url_for('index')
            logger.info(f"User '{username}' logged in successfully."); return redirect(next_page or url_for('index'))
        else: logger.warning(f"Failed login attempt for username: '{username}'"); flash('Sai tên đăng nhập hoặc mật khẩu.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    # (Keep existing logout logic)
    logger.info(f"User '{current_user.username}' logging out."); logout_user(); flash('Bạn đã đăng xuất.', 'info'); return redirect(url_for('login'))

# --- API Proxy Routes ---
# (Keep existing proxy routes - no changes needed here)
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
    logger.info("Forwarding request to ObsEngine: GET /api/namespaces"); data, status_code = call_obs_engine_api('/api/namespaces')
    if status_code == 200 and not isinstance(data, list): logger.warning(f"ObsEngine /api/namespaces did not return a list: {data}"); return jsonify([]), 200
    elif status_code != 200 and isinstance(data, dict) and 'error' in data: return jsonify(data), 502
    elif status_code != 200: return jsonify({"error": "Failed to fetch namespaces from backend"}), status_code
    return jsonify(data), status_code

# --- Global Config Proxy Route ---
# (Keep existing config proxy route - no changes needed here)
@app.route('/api/config/<config_type>', methods=['GET', 'POST'])
@login_required
def config_route(config_type):
    valid_get_types = ['all', 'ai', 'telegram']
    valid_post_types = ['ai', 'telegram']
    if request.method == 'GET':
        if config_type not in valid_get_types: return jsonify({"error": "Invalid config type requested for GET"}), 404
        all_config_data, status_code_all = call_obs_engine_api('/api/config/all')
        if status_code_all != 200: return jsonify(all_config_data), status_code_all
        if config_type == 'all': return jsonify(all_config_data), 200
        elif config_type == 'ai':
            keys_to_extract = ['enable_ai_analysis', 'ai_provider', 'ai_model_identifier']
            extracted_data = {k: all_config_data.get(k) for k in keys_to_extract if k in all_config_data}
            extracted_data['has_api_key'] = bool(all_config_data.get('ai_api_key'))
            return jsonify(extracted_data), 200
        elif config_type == 'telegram':
            keys_to_extract = ['enable_telegram_alerts', 'telegram_chat_id']
            extracted_data = {k: all_config_data.get(k) for k in keys_to_extract if k in all_config_data}
            extracted_data['has_token'] = bool(all_config_data.get('telegram_bot_token'))
            return jsonify(extracted_data), 200
        else: return jsonify({"error": "Invalid config type"}), 404
    elif request.method == 'POST':
        if current_user.role != 'admin': return jsonify({"error": "Admin privileges required"}), 403
        if config_type not in valid_post_types: return jsonify({"error": f"POST not allowed for config type '{config_type}'"}), 405
        if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
        json_data = request.get_json()
        if config_type == 'telegram' and 'telegram_bot_token' in json_data and not json_data['telegram_bot_token']: del json_data['telegram_bot_token']
        if config_type == 'ai' and 'ai_api_key' in json_data and not json_data['ai_api_key']: del json_data['ai_api_key']
        logger.info(f"Forwarding POST request to ObsEngine: /api/config/{config_type} | Data: {json.dumps({k: (v if k not in ['telegram_bot_token', 'ai_api_key'] else '********') for k, v in json_data.items()})}")
        data, status_code = call_obs_engine_api(f'/api/config/{config_type}', method='POST', json_data=json_data)
        return jsonify(data), status_code
    else: return jsonify({"error": "Method not allowed"}), 405

# --- Agent-Specific Config Proxy Routes ---
# (Keep existing agent config proxy routes - no changes needed here)
@app.route('/api/agents/<agent_id>/config', methods=['GET'])
@login_required
def get_agent_config_proxy(agent_id):
    if not agent_id: return jsonify({"error": "Agent ID is required"}), 400
    endpoint = f'/api/agents/{agent_id}/config'
    logger.info(f"Forwarding request to ObsEngine: GET {endpoint}")
    data, status_code = call_obs_engine_api(endpoint, method='GET')
    return jsonify(data), status_code

@app.route('/api/agents/<agent_id>/config/general', methods=['POST'])
@login_required
@admin_required
def save_agent_general_config_proxy(agent_id):
    if not agent_id: return jsonify({"error": "Agent ID is required"}), 400
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    json_data = request.get_json()
    endpoint = f'/api/agents/{agent_id}/config/general'
    logger.info(f"Forwarding request to ObsEngine: POST {endpoint} | Data: {json.dumps(json_data)}")
    data, status_code = call_obs_engine_api(endpoint, method='POST', json_data=json_data)
    return jsonify(data), status_code

@app.route('/api/agents/<agent_id>/config/namespaces', methods=['POST'])
@login_required
@admin_required
def save_agent_namespaces_proxy(agent_id):
    if not agent_id: return jsonify({"error": "Agent ID is required"}), 400
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    json_data = request.get_json()
    endpoint = f'/api/agents/{agent_id}/config/namespaces'
    logger.info(f"Forwarding request to ObsEngine: POST {endpoint} | Data: {json.dumps(json_data)}")
    data, status_code = call_obs_engine_api(endpoint, method='POST', json_data=json_data)
    return jsonify(data), status_code


# --- User Management API Routes ---
@app.route('/api/users', methods=['GET'])
@login_required
@admin_required
def get_users():
    """API endpoint to get a list of all users."""
    users = User.get_all()
    return jsonify(users), 200

# --- create_user route (No Avatar) ---
@app.route('/api/users', methods=['POST'])
@login_required
@admin_required
def create_user():
    """API endpoint to create a new user (JSON only, no avatar)."""
    logger.debug(f"Create user request received (JSON). Content-Type: {request.content_type}")

    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    fullname = data.get('fullname')
    role = data.get('role')

    # Basic Validation
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400
    if len(password) < 6:
         return jsonify({"error": "Password must be at least 6 characters long"}), 400
    if role not in ['admin', 'user']:
        return jsonify({"error": "Invalid role specified"}), 400
    if User.find_by_username(username):
        return jsonify({"error": f"Username '{username}' already exists"}), 409 # Conflict

    # Hash password
    hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')

    # Save to DB (without avatar_url)
    conn = get_be_db_connection()
    if conn:
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO users (username, password, role, fullname)
                    VALUES (?, ?, ?, ?)
                """, (username, hashed_password, role, fullname)) # REMOVED avatar_url
                new_user_id = cursor.lastrowid
            logger.info(f"Admin '{current_user.username}' created new user '{username}' (ID: {new_user_id}) with role '{role}'.")

            new_user_data = User.get(new_user_id)
            if new_user_data:
                 return jsonify({
                     "message": "User created successfully",
                     "user": {
                         "id": new_user_data.id,
                         "username": new_user_data.username,
                         "fullname": new_user_data.fullname,
                         "role": new_user_data.role
                         # REMOVED "avatar_url": ...
                     }
                 }), 201
            else:
                 return jsonify({"message": "User created, but failed to retrieve details."}), 201
        except sqlite3.Error as e:
            logger.error(f"Database error creating user '{username}': {e}", exc_info=True)
            return jsonify({"error": "Database error creating user"}), 500
        except Exception as e:
            logger.error(f"Unexpected error creating user '{username}': {e}", exc_info=True)
            return jsonify({"error": "Unexpected error creating user"}), 500
        finally:
            if conn: conn.close()
    else:
        return jsonify({"error": "Database connection failed"}), 500
# --- END create_user ---

# --- NEW: update_user route ---
@app.route('/api/users/<int:user_id>', methods=['PUT'])
@login_required
@admin_required
def update_user(user_id):
    """API endpoint to update user's fullname and role (Admin only)."""
    logger.debug(f"Update user request received for user ID: {user_id}. Content-Type: {request.content_type}")

    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    fullname = data.get('fullname')
    role = data.get('role')

    # Validation
    if fullname is None and role is None: # Check if at least one field is provided
        return jsonify({"error": "At least fullname or role must be provided for update"}), 400
    if role is not None and role not in ['admin', 'user']:
        return jsonify({"error": "Invalid role specified"}), 400

    # Prevent admin from accidentally demoting the last admin? (Optional but recommended)
    # This logic might need refinement based on how you identify the 'last' admin.
    # For simplicity, we'll skip this check for now, but it's important in production.
    # if role == 'user':
    #     target_user = User.get(user_id)
    #     if target_user and target_user.role == 'admin':
    #         # Check if this is the only admin left
    #         conn_check = get_be_db_connection()
    #         if conn_check:
    #             try:
    #                 cursor = conn_check.cursor()
    #                 cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
    #                 admin_count = cursor.fetchone()[0]
    #                 if admin_count <= 1:
    #                     return jsonify({"error": "Cannot remove the last admin role"}), 400
    #             except Exception as e:
    #                 logger.error(f"Error checking admin count: {e}")
    #             finally:
    #                 conn_check.close()

    # Update in DB
    conn = get_be_db_connection()
    if conn:
        try:
            with conn:
                cursor = conn.cursor()
                update_fields = []
                params = []
                if fullname is not None:
                    update_fields.append("fullname = ?")
                    params.append(fullname)
                if role is not None:
                    update_fields.append("role = ?")
                    params.append(role)

                if not update_fields: # Should not happen due to earlier check, but good practice
                     return jsonify({"error": "No fields provided for update"}), 400

                params.append(user_id)
                sql = f"UPDATE users SET {', '.join(update_fields)} WHERE id = ?"
                cursor.execute(sql, tuple(params))

                if cursor.rowcount == 0:
                    return jsonify({"error": f"User with ID {user_id} not found"}), 404

            logger.info(f"Admin '{current_user.username}' updated user ID {user_id}. Fields updated: {', '.join(update_fields)}")

            updated_user_data = User.get(user_id) # Fetch updated data
            if updated_user_data:
                 return jsonify({
                     "message": "User updated successfully",
                     "user": {
                         "id": updated_user_data.id,
                         "username": updated_user_data.username,
                         "fullname": updated_user_data.fullname,
                         "role": updated_user_data.role
                     }
                 }), 200
            else:
                 # Should not happen if rowcount > 0, but handle defensively
                 return jsonify({"message": "User updated, but failed to retrieve updated details."}), 200

        except sqlite3.Error as e:
            logger.error(f"Database error updating user {user_id}: {e}", exc_info=True)
            return jsonify({"error": "Database error updating user"}), 500
        except Exception as e:
            logger.error(f"Unexpected error updating user {user_id}: {e}", exc_info=True)
            return jsonify({"error": "Unexpected error updating user"}), 500
        finally:
            if conn: conn.close()
    else:
        return jsonify({"error": "Database connection failed"}), 500
# --- END NEW update_user ---


# --- Main Execution Guard ---
if __name__ == '__main__':
    flask_port = int(os.environ.get("FLASK_PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    logger.info(f"Starting Obs-Engine-BE (Portal) Flask server on 0.0.0.0:{flask_port} | Debug: {debug_mode}")
    app.run(host='0.0.0.0', port=flask_port, debug=debug_mode, use_reloader=debug_mode)
