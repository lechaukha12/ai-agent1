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
        logging.error(f"Database connection error: {e}")
        return None

# --- Hàm khởi tạo DB (Sửa lỗi tạo bảng users & Thêm user) ---
def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if not os.path.exists(db_dir):
        try:
            os.makedirs(db_dir)
            logging.info(f"Created directory for database: {db_dir}")
        except OSError as e:
            logging.error(f"Could not create directory {db_dir}: {e}")
            return False

    conn = None
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            cursor = conn.cursor()
            logging.info("Ensuring database tables exist...")
            # Tạo bảng incidents (giữ nguyên)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, pod_key TEXT NOT NULL,
                    severity TEXT NOT NULL, summary TEXT, initial_reasons TEXT, k8s_context TEXT, sample_logs TEXT,
                    input_prompt TEXT, raw_ai_response TEXT
                )
            ''')
            # Tạo bảng daily_stats (giữ nguyên)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY, model_calls INTEGER DEFAULT 0,
                    telegram_alerts INTEGER DEFAULT 0, incident_count INTEGER DEFAULT 0
                )
            ''')
            # Tạo bảng users (đảm bảo chạy đúng)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL
                )
            ''')
            conn.commit()
            logging.info("Tables ensured.")

            # === BẮT ĐẦU SỬA LỖI: Sửa tên user ===
            username_to_create = 'khalc' # <<< Sửa lại thành khalc
            # === KẾT THÚC SỬA LỖI ===
            password_to_create = 'chaukha'
            cursor.execute("SELECT COUNT(*) FROM users WHERE username = ?", (username_to_create,))
            user_exists = cursor.fetchone()[0]

            if user_exists == 0:
                logging.info(f"User '{username_to_create}' not found, attempting to create...")
                hashed_password = bcrypt.generate_password_hash(password_to_create).decode('utf-8')
                try:
                    cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username_to_create, hashed_password))
                    conn.commit()
                    logging.info(f"Successfully created user '{username_to_create}'.")
                except sqlite3.IntegrityError:
                    logging.info(f"User '{username_to_create}' already exists (likely created by another process/thread).")
                except sqlite3.Error as e:
                    logging.error(f"Failed to create user '{username_to_create}': {e}")
                    conn.rollback()
            else:
                logging.info(f"User '{username_to_create}' already exists.")

        logging.info(f"Database initialization/check complete at {DB_PATH}")
        return True

    except sqlite3.Error as e:
        logging.error(f"Database error during initialization: {e}", exc_info=True)
        if conn: conn.rollback()
        return False
    except Exception as e:
        logging.error(f"Unexpected error during DB initialization: {e}", exc_info=True)
        if conn: conn.rollback()
        return False
    # finally: # Không cần finally vì đã dùng with
    #     if conn: conn.close()


# Gọi init_db khi ứng dụng khởi động
if not init_db():
    logging.critical("DATABASE INITIALIZATION FAILED! Portal might not work correctly.")


# --- Route Chính ---
@app.route('/')
@login_required
def index():
    return render_template('index.html', db_path=DB_PATH)

# --- Route Đăng nhập ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = True if request.form.get('remember') else False

        conn = get_db_connection()
        user_data = None
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT id, username, password FROM users WHERE username = ?", (username,))
                user_data = cursor.fetchone()
                conn.close()
            except sqlite3.Error as e:
                logging.error(f"DB error during login query for user '{username}': {e}")
                flash("Lỗi truy vấn cơ sở dữ liệu, vui lòng thử lại.", "danger")
                if conn: conn.close()
                return render_template('login.html')
        else:
             flash("Không thể kết nối đến cơ sở dữ liệu.", "danger")
             return render_template('login.html')


        if user_data and bcrypt.check_password_hash(user_data['password'], password):
            user_obj = User(id=user_data['id'], username=user_data['username'], password_hash=user_data['password'])
            login_user(user_obj, remember=remember)
            flash('Đăng nhập thành công!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        else:
            if not user_data:
                 logging.warning(f"Login failed: User '{username}' not found.")
            else:
                 logging.warning(f"Login failed: Incorrect password for user '{username}'.")
            flash('Đăng nhập không thành công. Vui lòng kiểm tra lại tên đăng nhập và mật khẩu.', 'danger')

    return render_template('login.html')

# --- Route Đăng xuất ---
@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Bạn đã đăng xuất.', 'info')
    return redirect(url_for('login'))


# --- API Lấy Danh sách Sự cố ---
@app.route('/api/incidents')
@login_required
def get_incidents():
    # ... (Giữ nguyên logic API này) ...
    pod_filter = request.args.get('pod', default="", type=str).strip()
    severity_filter = request.args.get('severity', default="", type=str).upper().strip()
    start_date_str = request.args.get('start_date', default=None, type=str)
    end_date_str = request.args.get('end_date', default=None, type=str)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    offset = (page - 1) * per_page
    conn = get_db_connection()
    if conn is None: return jsonify({"error": "Database connection failed or file not found."}), 500
    incidents = []; total_count = 0
    try:
        cursor = conn.cursor(); base_query = "FROM incidents WHERE 1=1"; params = []
        if pod_filter: base_query += " AND pod_key LIKE ?"; params.append(f"%{pod_filter}%")
        if severity_filter: base_query += " AND severity = ?"; params.append(severity_filter)
        if start_date_str:
             try: start_dt_utc = datetime.fromisoformat(start_date_str.replace('Z', '+00:00')); base_query += " AND timestamp >= ?"; params.append(start_dt_utc.isoformat())
             except ValueError: logging.warning(f"Invalid start_date: {start_date_str}")
        if end_date_str:
             try: end_dt_utc = datetime.fromisoformat(end_date_str.replace('Z', '+00:00')); base_query += " AND timestamp <= ?"; params.append(end_dt_utc.isoformat())
             except ValueError: logging.warning(f"Invalid end_date: {end_date_str}")
        count_query = f"SELECT COUNT(*) as count {base_query}"; cursor.execute(count_query, tuple(params)); count_result = cursor.fetchone(); total_count = count_result['count'] if count_result else 0
        data_query = f"SELECT id, timestamp, pod_key, severity, summary, initial_reasons {base_query} ORDER BY timestamp DESC LIMIT ? OFFSET ?"; params_data = params + [per_page, offset]; cursor.execute(data_query, tuple(params_data)); rows = cursor.fetchall(); conn.close()
        for row in rows: incidents.append({"id": row['id'], "timestamp": row['timestamp'], "pod_key": row['pod_key'], "severity": row['severity'], "summary": row['summary'], "initial_reasons": row['initial_reasons']})
        total_pages = math.ceil(total_count / per_page)
        return jsonify({"incidents": incidents, "pagination": {"page": page, "per_page": per_page, "total_items": total_count, "total_pages": total_pages}})
    except sqlite3.Error as e: logging.error(f"DB error incidents: {e}"); return jsonify({"error": f"Failed to fetch incidents: {e}"}), 500
    except Exception as e: logging.error(f"Unexpected error incidents: {e}", exc_info=True); return jsonify({"error": "Unexpected error."}), 500


# --- API Lấy Thống kê ---
@app.route('/api/stats')
@login_required
def get_stats():
    # ... (Giữ nguyên logic API này) ...
    days = request.args.get('days', 1, type=int)
    if days not in [1, 7, 30]: days = 1
    end_date = datetime.now(timezone.utc); start_date = end_date - timedelta(days=days)
    conn = get_db_connection();
    if conn is None: return jsonify({"error": "Database connection failed or file not found."}), 500
    stats = {}
    try:
        cursor = conn.cursor()
        chart_days = max(days, 7); chart_start_date = end_date - timedelta(days=chart_days)
        cursor.execute('SELECT date, model_calls, telegram_alerts, incident_count FROM daily_stats WHERE date >= ? AND date <= ? ORDER BY date ASC', (chart_start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
        daily_stats_rows = cursor.fetchall(); daily_data_for_chart = [dict(row) for row in daily_stats_rows]
        cursor.execute('SELECT SUM(model_calls) as total_model_calls, SUM(telegram_alerts) as total_telegram_alerts, SUM(incident_count) as total_incidents FROM daily_stats WHERE date >= ? AND date <= ?', (start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
        totals = cursor.fetchone()
        today_start_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0); today_end_utc = today_start_utc + timedelta(days=1) - timedelta(microseconds=1)
        cursor.execute('SELECT severity, COUNT(*) as count FROM incidents WHERE timestamp >= ? AND timestamp <= ? GROUP BY severity', (today_start_utc.isoformat(), today_end_utc.isoformat()))
        severity_rows_today = cursor.fetchall(); severity_distribution_today = {row['severity']: row['count'] for row in severity_rows_today}
        cursor.execute('SELECT initial_reasons, COUNT(*) as count FROM incidents WHERE timestamp >= ? AND timestamp <= ? GROUP BY initial_reasons', (today_start_utc.isoformat(), today_end_utc.isoformat()))
        reason_rows_today = cursor.fetchall(); source_distribution_today = {'Hạ tầng (K8s)': 0, 'Ứng dụng (Loki)': 0, 'Cả hai': 0, 'Khác': 0}
        for row in reason_rows_today:
            reasons = row['initial_reasons'] or ''; is_k8s = 'K8s:' in reasons; is_loki = 'Loki:' in reasons
            if is_k8s and is_loki: source_distribution_today['Cả hai'] += row['count']
            elif is_k8s: source_distribution_today['Hạ tầng (K8s)'] += row['count']
            elif is_loki: source_distribution_today['Ứng dụng (Loki)'] += row['count']
            else: source_distribution_today['Khác'] += row['count']
        source_distribution_today = {k: v for k, v in source_distribution_today.items() if v > 0}
        cursor.execute('SELECT pod_key, COUNT(*) as count FROM incidents WHERE timestamp >= ? AND timestamp <= ? GROUP BY pod_key ORDER BY count DESC LIMIT 5', (start_date.isoformat(), end_date.isoformat()))
        top_pods_rows = cursor.fetchall(); top_problematic_pods = {row['pod_key']: row['count'] for row in top_pods_rows}
        cursor.execute("SELECT SUBSTR(pod_key, 1, INSTR(pod_key, '/') - 1) as namespace, COUNT(*) as count FROM incidents WHERE timestamp >= ? AND timestamp <= ? AND INSTR(pod_key, '/') > 0 GROUP BY namespace ORDER BY count DESC", (start_date.isoformat(), end_date.isoformat()))
        namespace_rows = cursor.fetchall(); namespace_distribution = {row['namespace']: row['count'] for row in namespace_rows}
        conn.close()
        stats = {"totals": {"model_calls": totals['total_model_calls'] if totals else 0, "telegram_alerts": totals['total_telegram_alerts'] if totals else 0, "incidents": totals['total_incidents'] if totals else 0,},"daily_stats_for_chart": daily_data_for_chart,"severity_distribution_today": severity_distribution_today,"source_distribution_today": source_distribution_today,"top_problematic_pods": top_problematic_pods,"namespace_distribution": namespace_distribution}
        return jsonify(stats)
    except sqlite3.Error as e: logging.error(f"DB error stats: {e}"); return jsonify({"error": f"Failed to fetch stats: {e}"}), 500
    except Exception as e: logging.error(f"Unexpected error stats: {e}", exc_info=True); return jsonify({"error": "Unexpected error."}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
