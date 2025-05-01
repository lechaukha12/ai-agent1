import os
import sqlite3
import logging
from flask import Flask, jsonify, render_template, request, redirect, url_for, flash
from datetime import datetime, timedelta, timezone
import math
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
try:
    from zoneinfo import ZoneInfo
except ImportError:
    logging.error("zoneinfo module not found. Please use Python 3.9+ or install pytz.")
    exit(1)
import json

# --- Cấu hình Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Cấu hình Flask và DB ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'your-very-secret-and-random-key-change-me!')
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=7)

DB_PATH = os.environ.get("DB_PATH", "/data/agent_stats.db")
try:
    DISPLAY_TZ_STR = os.environ.get("TZ", "UTC")
    DISPLAY_TZ = ZoneInfo(DISPLAY_TZ_STR)
    logging.info(f"Portal display timezone set to: {DISPLAY_TZ_STR}")
except Exception as e:
    logging.warning(f"Could not load timezone '{os.environ.get('TZ', 'UTC')}': {e}. Defaulting display to UTC.")
    DISPLAY_TZ = timezone.utc

# --- Khởi tạo Flask-Login và Bcrypt ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Vui lòng đăng nhập để truy cập trang này."
login_manager.login_message_category = "info"
bcrypt = Bcrypt(app)

# --- Định nghĩa User Model ---
class User(UserMixin):
    def __init__(self, id, username, password_hash):
        self.id = id
        self.username = username
        self.password = password_hash

    @staticmethod
    def get(user_id):
        conn = get_db_connection()
        if conn is None: return None
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, password FROM users WHERE id = ?", (user_id,))
            user_data = cursor.fetchone()
            conn.close()
            if user_data:
                return User(id=user_data['id'], username=user_data['username'], password_hash=user_data['password'])
            return None
        except sqlite3.Error as e:
            logging.error(f"Database error getting user {user_id}: {e}")
            if conn: conn.close()
            return None
        except Exception as e:
             logging.error(f"Unexpected error getting user {user_id}: {e}", exc_info=True)
             if conn: conn.close()
             return None


@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)


# --- Hàm Kết nối DB ---
def get_db_connection():
    if not os.path.isfile(DB_PATH):
        logging.error(f"Database file not found or is not a file at {DB_PATH}.")
        db_dir = os.path.dirname(DB_PATH)
        if not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir)
                logging.info(f"Created directory for database: {db_dir}")
            except OSError as e:
                logging.error(f"Could not create directory {db_dir}: {e}")
                return None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logging.error(f"Database connection error to {DB_PATH}: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error connecting to database {DB_PATH}: {e}", exc_info=True)
        return None

# --- Hàm khởi tạo DB ---
def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if not os.path.exists(db_dir):
        try: os.makedirs(db_dir); logging.info(f"Created DB directory: {db_dir}")
        except OSError as e: logging.error(f"Could not create DB directory {db_dir}: {e}"); return False

    conn = get_db_connection()
    if conn is None:
        logging.error("Failed to get DB connection during portal initialization.")
        return False

    try:
        with conn:
            cursor = conn.cursor()
            logging.info("Ensuring database tables exist for Portal...")

            # Create tables if they don't exist (no changes needed here)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, pod_key TEXT NOT NULL, severity TEXT NOT NULL,
                    summary TEXT, initial_reasons TEXT, k8s_context TEXT, sample_logs TEXT,
                    input_prompt TEXT, raw_ai_response TEXT, root_cause TEXT, troubleshooting_steps TEXT
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY, model_calls INTEGER DEFAULT 0,
                    telegram_alerts INTEGER DEFAULT 0, incident_count INTEGER DEFAULT 0
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS available_namespaces ( name TEXT PRIMARY KEY, last_seen TEXT NOT NULL )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS agent_config ( key TEXT PRIMARY KEY, value TEXT )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL
                )
            ''')
            cursor.execute('''
                 CREATE TABLE IF NOT EXISTS alert_cooldown ( pod_key TEXT PRIMARY KEY, cooldown_until TEXT NOT NULL )
            ''')
            logging.info("Portal tables ensured.")

            # Create default user if not exists
            username_to_create = 'khalc'
            password_to_create = 'chaukha'
            cursor.execute("SELECT COUNT(*) FROM users WHERE username = ?", (username_to_create,))
            user_exists = cursor.fetchone()[0]
            if user_exists == 0:
                logging.info(f"User '{username_to_create}' not found, attempting to create...")
                try:
                    hashed_password = bcrypt.generate_password_hash(password_to_create).decode('utf-8')
                    cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username_to_create, hashed_password))
                    logging.info(f"Successfully created user '{username_to_create}'.")
                except sqlite3.IntegrityError:
                    logging.info(f"User '{username_to_create}' already exists (likely created concurrently).")
                except sqlite3.Error as e_insert:
                    logging.error(f"Failed to create user '{username_to_create}': {e_insert}")
            else:
                logging.info(f"User '{username_to_create}' already exists.")

            # Initialize default agent config values if they don't exist in DB
            default_agent_configs = {
                'enable_ai_analysis': 'true',
                'ai_provider': 'gemini',
                'ai_model_identifier': 'gemini-1.5-flash',
                'ai_api_key': '',
                'monitored_namespaces': '["kube-system", "default"]',
                'loki_scan_min_level': 'INFO',
                'scan_interval_seconds': '30',
                'restart_count_threshold': '5',
                'alert_severity_levels': 'WARNING,ERROR,CRITICAL',
                'alert_cooldown_minutes': '30',
                # --- ADDED TELEGRAM TOGGLE DEFAULT ---
                'enable_telegram_alerts': 'false', # Default to disabled
                # --------------------------------------
                'telegram_bot_token': '',
                'telegram_chat_id': ''
            }
            for key, value in default_agent_configs.items():
                cursor.execute("INSERT OR IGNORE INTO agent_config (key, value) VALUES (?, ?)", (key, value))
            logging.info("Default agent config values ensured in DB.")

        logging.info(f"Database initialization/check complete at {DB_PATH}")
        return True
    except sqlite3.Error as e:
        logging.error(f"Database error during initialization: {e}", exc_info=True)
        return False
    except Exception as e:
        logging.error(f"Unexpected error during DB initialization: {e}", exc_info=True)
        return False
    finally:
        if conn: conn.close()

# Call init_db on startup
if not init_db():
    logging.critical("DATABASE INITIALIZATION FAILED! Portal might not work correctly.")

# --- Routes ---
@app.route('/')
@login_required
def index():
    return render_template('index.html', db_path=DB_PATH, app_title="Bug Tracker")

# --- Login/Logout Routes ---
# ... (Login/Logout routes remain unchanged) ...
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username'); password = request.form.get('password'); remember = True if request.form.get('remember') else False
        if not username or not password:
             flash('Vui lòng nhập tên đăng nhập và mật khẩu.', 'warning')
             return render_template('login.html')
        conn = get_db_connection(); user_data = None
        if conn:
            try:
                cursor = conn.cursor(); cursor.execute("SELECT id, username, password FROM users WHERE username = ?", (username,)); user_data = cursor.fetchone(); conn.close()
            except sqlite3.Error as e: logging.error(f"DB error during login for user {username}: {e}"); flash("Lỗi cơ sở dữ liệu khi đăng nhập.", "danger"); return render_template('login.html')
            except Exception as e: logging.error(f"Unexpected error during login for user {username}: {e}", exc_info=True); flash("Lỗi không xác định khi đăng nhập.", "danger"); return render_template('login.html')
        else: flash("Không thể kết nối đến cơ sở dữ liệu.", "danger"); return render_template('login.html')

        if user_data and bcrypt.check_password_hash(user_data['password'], password):
            user_obj = User(id=user_data['id'], username=user_data['username'], password_hash=user_data['password']); login_user(user_obj, remember=remember); flash('Đăng nhập thành công!', 'success'); next_page = request.args.get('next');
            if next_page and not next_page.startswith('/'): next_page = url_for('index') # Basic redirect protection
            return redirect(next_page or url_for('index'))
        else: flash('Sai tên đăng nhập hoặc mật khẩu.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user(); flash('Bạn đã đăng xuất.', 'info'); return redirect(url_for('login'))

# --- API Routes ---

# --- API Lấy Danh sách Sự cố ---
# ... (get_incidents route remains unchanged) ...
@app.route('/api/incidents')
@login_required
def get_incidents():
    pod_filter = request.args.get('pod', default="", type=str).strip(); severity_filter = request.args.get('severity', default="", type=str).upper().strip(); start_date_str = request.args.get('start_date', default=None, type=str); end_date_str = request.args.get('end_date', default=None, type=str); page = request.args.get('page', 1, type=int); per_page = request.args.get('limit', 20, type=int);
    offset = (page - 1) * per_page
    conn = get_db_connection();
    if conn is None: return jsonify({"error": "Database connection failed."}), 500
    incidents = []; total_count = 0
    try:
        cursor = conn.cursor(); base_query = "FROM incidents WHERE 1=1"; params = []
        if pod_filter: base_query += " AND pod_key LIKE ?"; params.append(f"%{pod_filter}%")
        if severity_filter: base_query += " AND severity = ?"; params.append(severity_filter)
        if start_date_str:
             try: start_dt_utc = datetime.fromisoformat(start_date_str.replace('Z', '+00:00')); base_query += " AND timestamp >= ?"; params.append(start_dt_utc.isoformat())
             except ValueError: logging.warning(f"Invalid start_date format: {start_date_str}. Ignoring filter.")
        if end_date_str:
             try: end_dt_utc = datetime.fromisoformat(end_date_str.replace('Z', '+00:00')); base_query += " AND timestamp <= ?"; params.append(end_dt_utc.isoformat())
             except ValueError: logging.warning(f"Invalid end_date format: {end_date_str}. Ignoring filter.")

        count_query = f"SELECT COUNT(*) as count {base_query}"; cursor.execute(count_query, tuple(params)); count_result = cursor.fetchone(); total_count = count_result['count'] if count_result else 0

        data_query = f"""
            SELECT id, timestamp, pod_key, severity, summary, initial_reasons,
                   k8s_context, sample_logs, input_prompt, raw_ai_response,
                   root_cause, troubleshooting_steps
            {base_query}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """;
        params_data = params + [per_page, offset]; cursor.execute(data_query, tuple(params_data)); rows = cursor.fetchall(); conn.close()
        incidents = [dict(row) for row in rows]
        total_pages = math.ceil(total_count / per_page) if per_page > 0 else 0
        return jsonify({"incidents": incidents, "pagination": {"page": page, "per_page": per_page, "total_items": total_count, "total_pages": total_pages}})
    except sqlite3.Error as e:
        logging.error(f"DB error fetching incidents: {e}")
        if conn: conn.close()
        return jsonify({"error": f"Failed to fetch incidents: {e}"}), 500
    except Exception as e:
        logging.error(f"Unexpected error fetching incidents: {e}", exc_info=True)
        if conn: conn.close()
        return jsonify({"error": "Unexpected error fetching incidents."}), 500

# --- API Lấy Thống kê ---
# ... (get_stats route remains unchanged) ...
@app.route('/api/stats')
@login_required
def get_stats():
    days = request.args.get('days', 1, type=int)
    if days not in [1, 7, 30]: days = 1
    end_date_utc = datetime.now(timezone.utc); start_date_utc = end_date_utc - timedelta(days=days); chart_days = max(days, 7); chart_start_date_utc = end_date_utc - timedelta(days=chart_days)
    conn = get_db_connection();
    if conn is None: return jsonify({"error": "Database connection failed."}), 500
    stats = {}
    try:
        cursor = conn.cursor()
        cursor.execute(''' SELECT date, model_calls, telegram_alerts, incident_count FROM daily_stats WHERE date >= ? AND date <= ? ORDER BY date ASC ''', (chart_start_date_utc.strftime('%Y-%m-%d'), end_date_utc.strftime('%Y-%m-%d')))
        daily_stats_rows = cursor.fetchall(); daily_data_for_chart = [dict(row) for row in daily_stats_rows]

        cursor.execute(''' SELECT SUM(model_calls) as total_model_calls, SUM(telegram_alerts) as total_telegram_alerts, SUM(incident_count) as total_incidents FROM daily_stats WHERE date >= ? AND date <= ? ''', (start_date_utc.strftime('%Y-%m-%d'), end_date_utc.strftime('%Y-%m-%d')))
        totals_row = cursor.fetchone()
        totals = { "model_calls": totals_row['total_model_calls'] if totals_row and totals_row['total_model_calls'] else 0, "telegram_alerts": totals_row['total_telegram_alerts'] if totals_row and totals_row['total_telegram_alerts'] else 0, "incidents": totals_row['total_incidents'] if totals_row and totals_row['total_incidents'] else 0, }

        today_start_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0); today_end_utc = today_start_utc + timedelta(days=1) - timedelta(microseconds=1)
        cursor.execute(''' SELECT severity, COUNT(*) as count FROM incidents WHERE timestamp >= ? AND timestamp <= ? GROUP BY severity ''', (today_start_utc.isoformat(), today_end_utc.isoformat()))
        severity_rows_today = cursor.fetchall(); severity_distribution_today = {row['severity']: row['count'] for row in severity_rows_today if row['severity']}

        cursor.execute(''' SELECT pod_key, COUNT(*) as count FROM incidents WHERE timestamp >= ? AND timestamp <= ? GROUP BY pod_key ORDER BY count DESC LIMIT 5 ''', (start_date_utc.isoformat(), end_date_utc.isoformat()))
        top_pods_rows = cursor.fetchall(); top_problematic_pods = {row['pod_key']: row['count'] for row in top_pods_rows}

        cursor.execute(''' SELECT SUBSTR(pod_key, 1, INSTR(pod_key, '/') - 1) as namespace, COUNT(*) as count FROM incidents WHERE timestamp >= ? AND timestamp <= ? AND INSTR(pod_key, '/') > 0 GROUP BY namespace ORDER BY count DESC ''', (start_date_utc.isoformat(), end_date_utc.isoformat()))
        namespace_rows = cursor.fetchall(); namespace_distribution = {row['namespace']: row['count'] for row in namespace_rows if row['namespace']}

        conn.close()
        stats = {"totals": totals, "daily_stats_for_chart": daily_data_for_chart, "severity_distribution_today": severity_distribution_today, "top_problematic_pods": top_problematic_pods, "namespace_distribution": namespace_distribution}
        return jsonify(stats)
    except sqlite3.Error as e:
        logging.error(f"DB error fetching stats: {e}")
        if conn: conn.close()
        return jsonify({"error": f"Failed to fetch stats: {e}"}), 500
    except Exception as e:
        logging.error(f"Unexpected error fetching stats: {e}", exc_info=True)
        if conn: conn.close()
        return jsonify({"error": "Unexpected error fetching stats."}), 500

# --- API Namespace ---
# ... (get_available_namespaces route remains unchanged) ...
@app.route('/api/namespaces')
@login_required
def get_available_namespaces():
    conn = get_db_connection()
    if conn is None: return jsonify({"error": "Database connection failed."}), 500
    namespaces = []
    try:
        cursor = conn.cursor(); cursor.execute("SELECT name FROM available_namespaces ORDER BY name ASC"); rows = cursor.fetchall(); conn.close()
        namespaces = [row['name'] for row in rows]; return jsonify(namespaces)
    except sqlite3.Error as e:
        logging.error(f"DB error fetching available namespaces: {e}")
        if conn: conn.close()
        return jsonify({"error": f"Failed to fetch namespaces: {e}"}), 500
    except Exception as e:
        logging.error(f"Unexpected error fetching available namespaces: {e}", exc_info=True)
        if conn: conn.close()
        return jsonify({"error": "Unexpected error fetching namespaces."}), 500

# --- API Monitored Namespaces Config ---
# ... (manage_monitored_namespaces route remains unchanged) ...
@app.route('/api/config/monitored_namespaces', methods=['GET', 'POST'])
@login_required
def manage_monitored_namespaces():
    conn = get_db_connection()
    if conn is None: return jsonify({"error": "Database connection failed."}), 500
    if request.method == 'GET':
        try:
            cursor = conn.cursor(); cursor.execute("SELECT value FROM agent_config WHERE key = 'monitored_namespaces'"); result = cursor.fetchone(); conn.close()
            monitored = []
            if result and result['value']:
                try:
                    loaded_value = json.loads(result['value'])
                    if isinstance(loaded_value, list): monitored = loaded_value
                    else: logging.warning("monitored_namespaces value in DB is not a JSON list.")
                except json.JSONDecodeError:
                    monitored = [ns.strip() for ns in result['value'].split(',') if ns.strip()]
                    logging.info("Parsed monitored_namespaces from DB using CSV fallback.")
            return jsonify(monitored)
        except sqlite3.Error as e:
            logging.error(f"DB error reading monitored_namespaces: {e}")
            if conn: conn.close()
            return jsonify({"error": f"Failed to read config: {e}"}), 500
        except Exception as e:
            logging.error(f"Unexpected error reading monitored_namespaces: {e}", exc_info=True)
            if conn: conn.close()
            return jsonify({"error": "Unexpected error reading config."}), 500
    elif request.method == 'POST':
        if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
        data = request.get_json(); namespaces_to_monitor = data.get('namespaces')
        if not isinstance(namespaces_to_monitor, list): return jsonify({"error": "'namespaces' must be a list"}), 400
        cleaned_namespaces = [ns for ns in namespaces_to_monitor if isinstance(ns, str) and ns.strip()]
        value_to_save = json.dumps(cleaned_namespaces)
        try:
            with sqlite3.connect(DB_PATH, timeout=10) as conn_save:
                cursor = conn_save.cursor(); cursor.execute("INSERT OR REPLACE INTO agent_config (key, value) VALUES (?, ?)", ('monitored_namespaces', value_to_save))
            logging.info(f"Updated monitored_namespaces config in DB: {value_to_save}");
            return jsonify({"message": "Configuration saved successfully."}), 200
        except sqlite3.Error as e:
            logging.error(f"DB error saving monitored_namespaces: {e}")
            return jsonify({"error": f"Failed to save config: {e}"}), 500
        except Exception as e:
            logging.error(f"Unexpected error saving monitored_namespaces: {e}", exc_info=True)
            return jsonify({"error": "Unexpected error saving config."}), 500

# --- API AI Config ---
# ... (manage_ai_config route remains unchanged) ...
@app.route('/api/config/ai', methods=['GET', 'POST'])
@login_required
def manage_ai_config():
    conn = get_db_connection()
    if conn is None: return jsonify({"error": "Database connection failed."}), 500

    if request.method == 'GET':
        try:
            cursor = conn.cursor()
            keys_to_fetch = ('enable_ai_analysis', 'ai_provider', 'ai_model_identifier')
            placeholders = ','.join('?' * len(keys_to_fetch))
            cursor.execute(f"SELECT key, value FROM agent_config WHERE key IN ({placeholders})", keys_to_fetch)
            results = cursor.fetchall()
            conn.close()

            ai_config = {row['key']: row['value'] for row in results}
            enable_ai_str = ai_config.get('enable_ai_analysis', 'true').lower()
            ai_config['enable_ai_analysis'] = enable_ai_str == 'true'
            ai_config.setdefault('ai_provider', 'gemini')
            ai_config.setdefault('ai_model_identifier', 'gemini-1.5-flash')

            if 'ai_api_key' in ai_config: del ai_config['ai_api_key']

            return jsonify(ai_config)

        except sqlite3.Error as e:
            logging.error(f"DB error reading AI config: {e}")
            if conn: conn.close()
            return jsonify({"error": f"Failed to read AI config: {e}"}), 500
        except Exception as e:
            logging.error(f"Unexpected error reading AI config: {e}", exc_info=True)
            if conn: conn.close()
            return jsonify({"error": "Unexpected error reading AI config."}), 500

    elif request.method == 'POST':
        if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
        data = request.get_json()

        enable_ai = data.get('enable_ai_analysis')
        provider = data.get('ai_provider')
        model_id = data.get('ai_model_identifier', '')
        api_key = data.get('ai_api_key')

        if not isinstance(enable_ai, bool): return jsonify({"error": "'enable_ai_analysis' must be a boolean"}), 400
        if provider not in ['gemini', 'local', 'openai', 'groq', 'deepseek', 'none']: return jsonify({"error": f"Invalid 'ai_provider': {provider}"}), 400

        try:
            with sqlite3.connect(DB_PATH, timeout=10) as conn_save:
                cursor = conn_save.cursor()
                cursor.execute("INSERT OR REPLACE INTO agent_config (key, value) VALUES (?, ?)", ('enable_ai_analysis', str(enable_ai).lower()))
                cursor.execute("INSERT OR REPLACE INTO agent_config (key, value) VALUES (?, ?)", ('ai_provider', provider))
                cursor.execute("INSERT OR REPLACE INTO agent_config (key, value) VALUES (?, ?)", ('ai_model_identifier', model_id))
                if api_key is not None and api_key != "":
                    cursor.execute("INSERT OR REPLACE INTO agent_config (key, value) VALUES (?, ?)", ('ai_api_key', api_key))
                    logging.info("Updated ai_api_key in database.")
                else:
                    logging.info("No new ai_api_key provided, existing key (if any) remains unchanged.")

            logging.info(f"Updated AI configuration in DB: enable={enable_ai}, provider={provider}, model={model_id}")
            return jsonify({"message": "AI configuration saved successfully."}), 200
        except sqlite3.Error as e:
            logging.error(f"DB error saving AI config: {e}")
            return jsonify({"error": f"Failed to save AI config: {e}"}), 500
        except Exception as e:
            logging.error(f"Unexpected error saving AI config: {e}", exc_info=True)
            return jsonify({"error": "Unexpected error saving AI config."}), 500

# --- API General Config ---
# ... (manage_general_config route remains unchanged) ...
@app.route('/api/config/general', methods=['GET', 'POST'])
@login_required
def manage_general_config():
    conn = get_db_connection()
    if conn is None: return jsonify({"error": "Database connection failed."}), 500

    general_keys = [
        'loki_scan_min_level', 'scan_interval_seconds', 'restart_count_threshold',
        'alert_severity_levels', 'alert_cooldown_minutes'
    ]

    if request.method == 'GET':
        try:
            cursor = conn.cursor()
            placeholders = ','.join('?' * len(general_keys))
            cursor.execute(f"SELECT key, value FROM agent_config WHERE key IN ({placeholders})", tuple(general_keys))
            results = cursor.fetchall()
            conn.close()

            general_config = {row['key']: row['value'] for row in results}

            general_config.setdefault('loki_scan_min_level', 'INFO')
            general_config.setdefault('scan_interval_seconds', '30')
            general_config.setdefault('restart_count_threshold', '5')
            general_config.setdefault('alert_severity_levels', 'WARNING,ERROR,CRITICAL')
            general_config.setdefault('alert_cooldown_minutes', '30')

            return jsonify(general_config)

        except sqlite3.Error as e:
            logging.error(f"DB error reading general config: {e}")
            if conn: conn.close()
            return jsonify({"error": f"Failed to read general config: {e}"}), 500
        except Exception as e:
            logging.error(f"Unexpected error reading general config: {e}", exc_info=True)
            if conn: conn.close()
            return jsonify({"error": "Unexpected error reading general config."}), 500

    elif request.method == 'POST':
        if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
        data = request.get_json()

        updates = []
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        valid_severity_levels = ["INFO", "WARNING", "ERROR", "CRITICAL"]

        try:
            scan_interval = int(data.get('scan_interval_seconds', 30))
            if scan_interval < 10: raise ValueError("Scan interval must be >= 10")
            updates.append(('scan_interval_seconds', str(scan_interval)))

            restart_threshold = int(data.get('restart_count_threshold', 5))
            if restart_threshold < 1: raise ValueError("Restart threshold must be >= 1")
            updates.append(('restart_count_threshold', str(restart_threshold)))

            scan_level = data.get('loki_scan_min_level', 'INFO').upper()
            if scan_level not in valid_log_levels: raise ValueError(f"Invalid Loki scan level: {scan_level}")
            updates.append(('loki_scan_min_level', scan_level))

            cooldown = int(data.get('alert_cooldown_minutes', 30))
            if cooldown < 1: raise ValueError("Alert cooldown must be >= 1")
            updates.append(('alert_cooldown_minutes', str(cooldown)))

            alert_levels_str = data.get('alert_severity_levels', 'WARNING,ERROR,CRITICAL')
            alert_levels_list = [lvl.strip().upper() for lvl in alert_levels_str.split(',') if lvl.strip()]
            if not alert_levels_list: raise ValueError("Alert severity levels cannot be empty")
            for lvl in alert_levels_list:
                if lvl not in valid_severity_levels: raise ValueError(f"Invalid alert severity level: {lvl}")
            updates.append(('alert_severity_levels', ','.join(alert_levels_list)))

        except (ValueError, TypeError) as e:
            return jsonify({"error": f"Invalid input data: {e}"}), 400

        try:
            with sqlite3.connect(DB_PATH, timeout=10) as conn_save:
                cursor = conn_save.cursor()
                for key, value in updates:
                    cursor.execute("INSERT OR REPLACE INTO agent_config (key, value) VALUES (?, ?)", (key, value))
            logging.info(f"Updated general agent configuration in DB: {updates}")
            return jsonify({"message": "General configuration saved successfully."}), 200
        except sqlite3.Error as e:
            logging.error(f"DB error saving general config: {e}")
            return jsonify({"error": f"Failed to save general config: {e}"}), 500
        except Exception as e:
            logging.error(f"Unexpected error saving general config: {e}", exc_info=True)
            return jsonify({"error": "Unexpected error saving general config."}), 500

# --- UPDATED API Telegram Config ---
@app.route('/api/config/telegram', methods=['GET', 'POST'])
@login_required
def manage_telegram_config():
    conn = get_db_connection()
    if conn is None: return jsonify({"error": "Database connection failed."}), 500

    if request.method == 'GET':
        try:
            cursor = conn.cursor()
            # Fetch token, chat_id, and the new toggle status
            keys_to_fetch = ('telegram_bot_token', 'telegram_chat_id', 'enable_telegram_alerts')
            placeholders = ','.join('?' * len(keys_to_fetch))
            cursor.execute(f"SELECT key, value FROM agent_config WHERE key IN ({placeholders})", keys_to_fetch)
            results = cursor.fetchall()
            conn.close()

            telegram_config = {row['key']: row['value'] for row in results}

            # Check if token exists without revealing it
            has_token = bool(telegram_config.get('telegram_bot_token'))
            # Get toggle status, default to false if missing
            enable_alerts_str = telegram_config.get('enable_telegram_alerts', 'false').lower()
            enable_alerts = enable_alerts_str == 'true'

            return jsonify({
                "telegram_chat_id": telegram_config.get('telegram_chat_id', ''),
                "has_token": has_token,
                "enable_telegram_alerts": enable_alerts # Return boolean
            })

        except sqlite3.Error as e:
            logging.error(f"DB error reading Telegram config: {e}")
            if conn: conn.close()
            return jsonify({"error": f"Failed to read Telegram config: {e}"}), 500
        except Exception as e:
            logging.error(f"Unexpected error reading Telegram config: {e}", exc_info=True)
            if conn: conn.close()
            return jsonify({"error": "Unexpected error reading Telegram config."}), 500

    elif request.method == 'POST':
        if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
        data = request.get_json()

        token = data.get('telegram_bot_token') # Can be empty if not updating
        chat_id = data.get('telegram_chat_id', '').strip()
        enable_alerts = data.get('enable_telegram_alerts') # Expect boolean from frontend

        if not chat_id:
             return jsonify({"error": "Telegram Chat ID cannot be empty"}), 400
        if not isinstance(enable_alerts, bool):
            return jsonify({"error": "'enable_telegram_alerts' must be a boolean"}), 400

        try:
            with sqlite3.connect(DB_PATH, timeout=10) as conn_save:
                cursor = conn_save.cursor()
                # Update Chat ID
                cursor.execute("INSERT OR REPLACE INTO agent_config (key, value) VALUES (?, ?)",
                               ('telegram_chat_id', chat_id))
                # Update Enable Status (store as string)
                cursor.execute("INSERT OR REPLACE INTO agent_config (key, value) VALUES (?, ?)",
                               ('enable_telegram_alerts', str(enable_alerts).lower()))
                # Update Token ONLY if a new value was provided
                if token is not None and token != "":
                    cursor.execute("INSERT OR REPLACE INTO agent_config (key, value) VALUES (?, ?)",
                                   ('telegram_bot_token', token))
                    logging.info("Updated telegram_bot_token in database.")
                else:
                    logging.info("No new telegram_bot_token provided, existing token (if any) remains unchanged.")

            logging.info(f"Updated Telegram configuration in DB: chat_id={chat_id}, enabled={enable_alerts}")
            return jsonify({"message": "Telegram configuration saved successfully."}), 200
        except sqlite3.Error as e:
            logging.error(f"DB error saving Telegram config: {e}")
            return jsonify({"error": f"Failed to save Telegram config: {e}"}), 500
        except Exception as e:
            logging.error(f"Unexpected error saving Telegram config: {e}", exc_info=True)
            return jsonify({"error": "Unexpected error saving Telegram config."}), 500

# --- Main Execution ---
if __name__ == '__main__':
    # Use Gunicorn in production, this is for local dev only
    app.run(host='0.0.0.0', port=5000, debug=False) # Set debug=False for production-like env

