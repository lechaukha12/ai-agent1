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
    logging.error("zoneinfo module not found. Please use Python 3.9+ or install pytz and uncomment the fallback.")
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
        self.password = password_hash # Store the hash directly

    @staticmethod
    def get(user_id):
        conn = get_db_connection()
        if conn is None: return None
        try:
            cursor = conn.cursor()
            # Select the password hash column
            cursor.execute("SELECT id, username, password FROM users WHERE id = ?", (user_id,))
            user_data = cursor.fetchone()
            conn.close()
            if user_data:
                # Pass the hash to the constructor
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
    """Establishes a connection to the SQLite database."""
    # Check if DB file exists before connecting
    if not os.path.isfile(DB_PATH):
        logging.error(f"Database file not found or is not a file at {DB_PATH}.")
        # Attempt to create directory if it doesn't exist (useful for first run)
        db_dir = os.path.dirname(DB_PATH)
        if not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir)
                logging.info(f"Created directory for database: {db_dir}")
                # Even after creating dir, the file itself doesn't exist yet,
                # but sqlite3.connect will create it.
            except OSError as e:
                logging.error(f"Could not create directory {db_dir}: {e}")
                return None # Cannot proceed if directory creation fails
        # If the directory exists but the file doesn't, sqlite3.connect will create it.
        # If the path exists but is not a file (e.g., a directory), connect will fail.

    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row # Use Row factory for dict-like access
        return conn
    except sqlite3.Error as e:
        logging.error(f"Database connection error to {DB_PATH}: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error connecting to database {DB_PATH}: {e}", exc_info=True)
        return None

# --- Hàm khởi tạo DB (Đảm bảo các bảng cần thiết tồn tại) ---
def init_db():
    """Initializes the database and ensures all necessary tables exist."""
    db_dir = os.path.dirname(DB_PATH)
    if not os.path.exists(db_dir):
        try: os.makedirs(db_dir); logging.info(f"Created DB directory: {db_dir}")
        except OSError as e: logging.error(f"Could not create DB directory {db_dir}: {e}"); return False

    conn = get_db_connection()
    if conn is None:
        logging.error("Failed to get DB connection during portal initialization.")
        return False

    try:
        with conn: # Use 'with' statement for automatic commit/rollback
            cursor = conn.cursor()
            logging.info("Ensuring database tables exist for Portal...")

            # *** FIX: Use correct CREATE TABLE statements from Agent ***
            # Define incidents table structure (matching agent's definition)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    pod_key TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    summary TEXT,
                    initial_reasons TEXT,
                    k8s_context TEXT,
                    sample_logs TEXT,
                    input_prompt TEXT,
                    raw_ai_response TEXT
                )
            ''')
            # Define daily_stats table structure (matching agent's definition)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY,
                    model_calls INTEGER DEFAULT 0,
                    telegram_alerts INTEGER DEFAULT 0,
                    incident_count INTEGER DEFAULT 0
                )
            ''')
            # Define available_namespaces table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS available_namespaces (
                    name TEXT PRIMARY KEY,
                    last_seen TEXT NOT NULL
                )
            ''')
            # Define agent_config table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS agent_config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            # Define users table for login
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL
                )
            ''')
            logging.info("Portal tables ensured.")

            # --- Create default user 'khalc' if not exists ---
            username_to_create = 'khalc'
            password_to_create = 'chaukha' # Consider moving this to env var or config
            cursor.execute("SELECT COUNT(*) FROM users WHERE username = ?", (username_to_create,))
            user_exists = cursor.fetchone()[0]

            if user_exists == 0:
                logging.info(f"User '{username_to_create}' not found, attempting to create...")
                try:
                    # Hash the password before storing
                    hashed_password = bcrypt.generate_password_hash(password_to_create).decode('utf-8')
                    cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username_to_create, hashed_password))
                    # Commit is handled by 'with conn:' context manager
                    logging.info(f"Successfully created user '{username_to_create}'.")
                except sqlite3.IntegrityError:
                    # This might happen in rare race conditions if another process creates it
                    logging.info(f"User '{username_to_create}' already exists (likely created concurrently).")
                except sqlite3.Error as e_insert:
                    logging.error(f"Failed to create user '{username_to_create}': {e_insert}")
                    # Rollback is handled by 'with conn:' context manager on exception
            else:
                logging.info(f"User '{username_to_create}' already exists.")

        logging.info(f"Database initialization/check complete at {DB_PATH}")
        return True
    except sqlite3.Error as e:
        logging.error(f"Database error during initialization: {e}", exc_info=True)
        return False
    except Exception as e:
        logging.error(f"Unexpected error during DB initialization: {e}", exc_info=True)
        return False
    finally:
        if conn: conn.close() # Ensure connection is closed


# Gọi init_db khi ứng dụng khởi động
if not init_db():
    logging.critical("DATABASE INITIALIZATION FAILED! Portal might not work correctly.")


# --- Route Chính ---
@app.route('/')
@login_required
def index():
    """Renders the main dashboard page."""
    return render_template('index.html', db_path=DB_PATH)

# --- Route Đăng nhập / Đăng xuất ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handles user login."""
    if current_user.is_authenticated:
        return redirect(url_for('index')) # Redirect if already logged in

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = True if request.form.get('remember') else False

        if not username or not password:
             flash('Vui lòng nhập tên đăng nhập và mật khẩu.', 'warning')
             return render_template('login.html')

        conn = get_db_connection()
        user_data = None
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT id, username, password FROM users WHERE username = ?", (username,))
                user_data = cursor.fetchone()
                conn.close()
            except sqlite3.Error as e:
                logging.error(f"DB error during login for user {username}: {e}")
                flash("Lỗi cơ sở dữ liệu khi đăng nhập.", "danger")
                return render_template('login.html')
            except Exception as e:
                 logging.error(f"Unexpected error during login for user {username}: {e}", exc_info=True)
                 flash("Lỗi không xác định khi đăng nhập.", "danger")
                 return render_template('login.html')

        else: # conn is None
            flash("Không thể kết nối đến cơ sở dữ liệu.", "danger")
            return render_template('login.html')

        # Check if user exists and password is correct
        if user_data and bcrypt.check_password_hash(user_data['password'], password):
            user_obj = User(id=user_data['id'], username=user_data['username'], password_hash=user_data['password'])
            login_user(user_obj, remember=remember)
            flash('Đăng nhập thành công!', 'success')
            next_page = request.args.get('next')
            # Prevent open redirect vulnerability
            if next_page and not next_page.startswith('/'):
                 next_page = url_for('index')
            return redirect(next_page or url_for('index'))
        else:
            flash('Sai tên đăng nhập hoặc mật khẩu.', 'danger')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """Logs the current user out."""
    logout_user()
    flash('Bạn đã đăng xuất.', 'info')
    return redirect(url_for('login'))


# --- API Lấy Danh sách Sự cố ---
@app.route('/api/incidents')
@login_required
def get_incidents():
    """API endpoint to fetch incidents with filtering and pagination."""
    # Get query parameters
    pod_filter = request.args.get('pod', default="", type=str).strip()
    severity_filter = request.args.get('severity', default="", type=str).upper().strip()
    start_date_str = request.args.get('start_date', default=None, type=str)
    end_date_str = request.args.get('end_date', default=None, type=str)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    offset = (page - 1) * per_page

    conn = get_db_connection();
    if conn is None: return jsonify({"error": "Database connection failed."}), 500

    incidents = []; total_count = 0
    try:
        cursor = conn.cursor()
        # Build query dynamically based on filters
        base_query = "FROM incidents WHERE 1=1" # Start with a base condition
        params = [] # Parameters for the SQL query

        if pod_filter:
            base_query += " AND pod_key LIKE ?"
            params.append(f"%{pod_filter}%")
        if severity_filter:
            base_query += " AND severity = ?"
            params.append(severity_filter)
        if start_date_str:
             try:
                 # Ensure correct ISO format parsing (handle Z timezone)
                 start_dt_utc = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
                 base_query += " AND timestamp >= ?"
                 params.append(start_dt_utc.isoformat()) # Store as ISO string
             except ValueError:
                 logging.warning(f"Invalid start_date format: {start_date_str}. Ignoring filter.")
        if end_date_str:
             try:
                 end_dt_utc = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                 base_query += " AND timestamp <= ?"
                 params.append(end_dt_utc.isoformat())
             except ValueError:
                 logging.warning(f"Invalid end_date format: {end_date_str}. Ignoring filter.")

        # Get total count matching filters
        count_query = f"SELECT COUNT(*) as count {base_query}";
        cursor.execute(count_query, tuple(params));
        count_result = cursor.fetchone();
        total_count = count_result['count'] if count_result else 0

        # Get paginated data
        # Select all relevant columns
        data_query = f"""
            SELECT id, timestamp, pod_key, severity, summary, initial_reasons, k8s_context, sample_logs, input_prompt, raw_ai_response
            {base_query}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """;
        params_data = params + [per_page, offset];
        cursor.execute(data_query, tuple(params_data));
        rows = cursor.fetchall();
        conn.close() # Close connection after fetching data

        # Convert rows to dictionaries
        incidents = [dict(row) for row in rows]

        total_pages = math.ceil(total_count / per_page) if per_page > 0 else 0
        return jsonify({
            "incidents": incidents,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total_items": total_count,
                "total_pages": total_pages
            }
        })
    except sqlite3.Error as e:
        logging.error(f"DB error fetching incidents: {e}")
        if conn: conn.close()
        return jsonify({"error": f"Failed to fetch incidents: {e}"}), 500
    except Exception as e:
        logging.error(f"Unexpected error fetching incidents: {e}", exc_info=True)
        if conn: conn.close()
        return jsonify({"error": "Unexpected error fetching incidents."}), 500


# --- API Lấy Thống kê ---
@app.route('/api/stats')
@login_required
def get_stats():
    """API endpoint to fetch aggregated statistics."""
    days = request.args.get('days', 1, type=int)
    # Validate days parameter
    if days not in [1, 7, 30]: days = 1

    end_date_utc = datetime.now(timezone.utc)
    start_date_utc = end_date_utc - timedelta(days=days)
    # For chart data, ensure at least 7 days if possible
    chart_days = max(days, 7)
    chart_start_date_utc = end_date_utc - timedelta(days=chart_days)

    conn = get_db_connection();
    if conn is None: return jsonify({"error": "Database connection failed."}), 500

    stats = {}
    try:
        cursor = conn.cursor()

        # --- Fetch Daily Stats for Line Chart ---
        # Use the potentially longer range for the chart
        cursor.execute('''
            SELECT date, model_calls, telegram_alerts, incident_count
            FROM daily_stats
            WHERE date >= ? AND date <= ?
            ORDER BY date ASC
        ''', (chart_start_date_utc.strftime('%Y-%m-%d'), end_date_utc.strftime('%Y-%m-%d')))
        daily_stats_rows = cursor.fetchall();
        daily_data_for_chart = [dict(row) for row in daily_stats_rows]

        # --- Fetch Totals for Stat Cards ---
        # Use the user-selected range (days) for totals
        cursor.execute('''
            SELECT SUM(model_calls) as total_model_calls,
                   SUM(telegram_alerts) as total_telegram_alerts,
                   SUM(incident_count) as total_incidents
            FROM daily_stats
            WHERE date >= ? AND date <= ?
        ''', (start_date_utc.strftime('%Y-%m-%d'), end_date_utc.strftime('%Y-%m-%d')))
        totals_row = cursor.fetchone()
        totals = {
            "model_calls": totals_row['total_model_calls'] if totals_row and totals_row['total_model_calls'] else 0,
            "telegram_alerts": totals_row['total_telegram_alerts'] if totals_row and totals_row['total_telegram_alerts'] else 0,
            "incidents": totals_row['total_incidents'] if totals_row and totals_row['total_incidents'] else 0,
        }


        # --- Fetch Severity Distribution for Today (for Pie Chart) ---
        today_start_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0);
        today_end_utc = today_start_utc + timedelta(days=1) - timedelta(microseconds=1)
        cursor.execute('''
            SELECT severity, COUNT(*) as count
            FROM incidents
            WHERE timestamp >= ? AND timestamp <= ?
            GROUP BY severity
        ''', (today_start_utc.isoformat(), today_end_utc.isoformat()))
        severity_rows_today = cursor.fetchall();
        severity_distribution_today = {row['severity']: row['count'] for row in severity_rows_today}


        # --- Fetch Top Problematic Pods (for Bar Chart) ---
        # Use the user-selected range (days)
        cursor.execute('''
            SELECT pod_key, COUNT(*) as count
            FROM incidents
            WHERE timestamp >= ? AND timestamp <= ?
            GROUP BY pod_key
            ORDER BY count DESC
            LIMIT 5
        ''', (start_date_utc.isoformat(), end_date_utc.isoformat()))
        top_pods_rows = cursor.fetchall();
        top_problematic_pods = {row['pod_key']: row['count'] for row in top_pods_rows}

        # --- Fetch Namespace Distribution (for Pie Chart) ---
        # Use the user-selected range (days)
        cursor.execute('''
            SELECT SUBSTR(pod_key, 1, INSTR(pod_key, '/') - 1) as namespace, COUNT(*) as count
            FROM incidents
            WHERE timestamp >= ? AND timestamp <= ? AND INSTR(pod_key, '/') > 0
            GROUP BY namespace
            ORDER BY count DESC
        ''', (start_date_utc.isoformat(), end_date_utc.isoformat()))
        namespace_rows = cursor.fetchall();
        namespace_distribution = {row['namespace']: row['count'] for row in namespace_rows if row['namespace']} # Filter out potential empty namespaces

        conn.close() # Close connection

        # Assemble the final stats dictionary
        stats = {
            "totals": totals,
            "daily_stats_for_chart": daily_data_for_chart,
            "severity_distribution_today": severity_distribution_today,
            # "source_distribution_today" is removed as it wasn't used in the template
            "top_problematic_pods": top_problematic_pods,
            "namespace_distribution": namespace_distribution
        }
        return jsonify(stats)

    except sqlite3.Error as e:
        logging.error(f"DB error fetching stats: {e}")
        if conn: conn.close()
        return jsonify({"error": f"Failed to fetch stats: {e}"}), 500
    except Exception as e:
        logging.error(f"Unexpected error fetching stats: {e}", exc_info=True)
        if conn: conn.close()
        return jsonify({"error": "Unexpected error fetching stats."}), 500

# --- API mới cho Namespace ---
@app.route('/api/namespaces')
@login_required
def get_available_namespaces():
    """API endpoint to get the list of available namespaces from the DB."""
    conn = get_db_connection()
    if conn is None: return jsonify({"error": "Database connection failed."}), 500
    namespaces = []
    try:
        cursor = conn.cursor()
        # Fetch names from the dedicated table
        cursor.execute("SELECT name FROM available_namespaces ORDER BY name ASC")
        rows = cursor.fetchall()
        conn.close()
        namespaces = [row['name'] for row in rows]
        return jsonify(namespaces)
    except sqlite3.Error as e:
        logging.error(f"DB error fetching available namespaces: {e}")
        if conn: conn.close()
        return jsonify({"error": f"Failed to fetch namespaces: {e}"}), 500
    except Exception as e:
        logging.error(f"Unexpected error fetching available namespaces: {e}", exc_info=True)
        if conn: conn.close()
        return jsonify({"error": "Unexpected error fetching namespaces."}), 500

@app.route('/api/config/monitored_namespaces', methods=['GET', 'POST'])
@login_required
def manage_monitored_namespaces():
    """API endpoint to get or set the list of monitored namespaces."""
    conn = get_db_connection()
    if conn is None: return jsonify({"error": "Database connection failed."}), 500

    if request.method == 'GET':
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM agent_config WHERE key = 'monitored_namespaces'")
            result = cursor.fetchone()
            conn.close()
            monitored = []
            if result and result['value']:
                try:
                    # Try parsing as JSON first
                    loaded_value = json.loads(result['value'])
                    if isinstance(loaded_value, list):
                        monitored = loaded_value
                    else: # If not a list, treat as invalid config
                         logging.warning("monitored_namespaces value in DB is not a JSON list.")
                except json.JSONDecodeError:
                    # Fallback to CSV parsing for backward compatibility
                    monitored = [ns.strip() for ns in result['value'].split(',') if ns.strip()]
                    logging.info("Parsed monitored_namespaces from DB using CSV fallback.")
            return jsonify(monitored) # Return empty list if no config or invalid format
        except sqlite3.Error as e:
            logging.error(f"DB error reading monitored_namespaces: {e}")
            if conn: conn.close()
            return jsonify({"error": f"Failed to read config: {e}"}), 500
        except Exception as e:
            logging.error(f"Unexpected error reading monitored_namespaces: {e}", exc_info=True)
            if conn: conn.close()
            return jsonify({"error": "Unexpected error reading config."}), 500

    elif request.method == 'POST':
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400
        data = request.get_json()
        namespaces_to_monitor = data.get('namespaces')

        # Validate input
        if not isinstance(namespaces_to_monitor, list):
            return jsonify({"error": "'namespaces' must be a list"}), 400
        # Optional: Add more validation (e.g., check if namespaces are strings)

        # Save as JSON string
        value_to_save = json.dumps(namespaces_to_monitor)
        try:
            # Use 'with' statement for saving
            with sqlite3.connect(DB_PATH, timeout=10) as conn_save: # Separate connection for write
                cursor = conn_save.cursor()
                cursor.execute("INSERT OR REPLACE INTO agent_config (key, value) VALUES (?, ?)", ('monitored_namespaces', value_to_save))
            logging.info(f"Updated monitored_namespaces config in DB: {value_to_save}")
            return jsonify({"message": "Configuration saved successfully."}), 200
        except sqlite3.Error as e:
            logging.error(f"DB error saving monitored_namespaces: {e}")
            return jsonify({"error": f"Failed to save config: {e}"}), 500
        except Exception as e:
            logging.error(f"Unexpected error saving monitored_namespaces: {e}", exc_info=True)
            return jsonify({"error": "Unexpected error saving config."}), 500


if __name__ == '__main__':
    # Use a production-ready WSGI server like Gunicorn or Waitress in production
    # For development:
    # Set debug=True for easier debugging, but NEVER use debug mode in production
    app.run(host='0.0.0.0', port=5000, debug=False)
