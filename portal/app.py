import os
import sqlite3
import logging
from flask import Flask, jsonify, render_template, request
from datetime import datetime, timedelta, timezone

# --- Cấu hình Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Cấu hình Flask và DB ---
app = Flask(__name__)
DB_PATH = os.environ.get("DB_PATH", "/data/agent_stats.db")
try:
    from zoneinfo import ZoneInfo
    DISPLAY_TZ_STR = os.environ.get("TZ", "UTC")
    DISPLAY_TZ = ZoneInfo(DISPLAY_TZ_STR)
    logging.info(f"Portal display timezone set to: {DISPLAY_TZ_STR}")
except Exception as e:
    logging.warning(f"Could not load timezone '{os.environ.get('TZ', 'UTC')}': {e}. Defaulting display to UTC.")
    DISPLAY_TZ = timezone.utc


def get_db_connection():
    """Tạo kết nối đến SQLite database."""
    if not os.path.exists(DB_PATH):
        logging.error(f"Database file not found at {DB_PATH}. Agent might not have run yet or PV is not mounted correctly.")
        return None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logging.error(f"Database connection error: {e}")
        return None

@app.route('/')
def index():
    """Render trang HTML chính."""
    return render_template('index.html', db_path=DB_PATH)

@app.route('/api/incidents')
def get_incidents():
    """API endpoint để lấy danh sách các sự cố gần đây."""
    limit = request.args.get('limit', 100, type=int)
    offset = request.args.get('offset', 0, type=int)
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed or file not found."}), 500
    incidents = []
    try:
        cursor = conn.cursor()
        # Lấy thêm cột timestamp gốc để xử lý ngày cho Pie Chart
        cursor.execute('''
            SELECT id, timestamp, pod_key, severity, summary, initial_reasons
            FROM incidents
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        ''', (limit, offset))
        rows = cursor.fetchall()
        conn.close()
        for row in rows:
            # Trả về timestamp dạng ISO 8601 UTC để JS xử lý
            incidents.append({
                "id": row['id'],
                "timestamp": row['timestamp'], # Trả về ISO string
                "pod_key": row['pod_key'],
                "severity": row['severity'],
                "summary": row['summary'],
                "initial_reasons": row['initial_reasons']
            })
        return jsonify(incidents)
    except sqlite3.Error as e:
        logging.error(f"Database error fetching incidents: {e}")
        if conn: conn.close()
        return jsonify({"error": "Failed to fetch incidents from database."}), 500
    except Exception as e:
            logging.error(f"Unexpected error fetching incidents: {e}", exc_info=True)
            if conn: conn.close()
            return jsonify({"error": "An unexpected error occurred."}), 500


@app.route('/api/stats')
def get_stats():
    """API endpoint để lấy số liệu thống kê tổng hợp."""
    days = request.args.get('days', 7, type=int)
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days-1)
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed or file not found."}), 500

    stats = {}
    try:
        cursor = conn.cursor()
        # --- BẮT ĐẦU SỬA LỖI: Đổi tên cột ---
        # Đổi gemini_calls thành model_calls trong câu SELECT
        cursor.execute('''
            SELECT date, model_calls, telegram_alerts, incident_count
            FROM daily_stats
            WHERE date >= ? AND date <= ?
            ORDER BY date DESC
        ''', (start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
        # --- KẾT THÚC SỬA LỖI ---
        rows = cursor.fetchall()
        conn.close()

        total_model_calls = 0 # Đổi tên biến tổng
        total_telegram_alerts = 0
        total_incidents = 0
        daily_data = []

        for row in rows:
            # --- BẮT ĐẦU SỬA LỖI: Dùng đúng tên cột khi đọc ---
            daily_item = {
                "date": row["date"],
                "model_calls": row["model_calls"], # Đổi tên key
                "telegram_alerts": row["telegram_alerts"],
                "incident_count": row["incident_count"]
            }
            daily_data.append(daily_item)
            total_model_calls += row['model_calls'] # Đổi tên cột
            # --- KẾT THÚC SỬA LỖI ---
            total_telegram_alerts += row['telegram_alerts']
            total_incidents += row['incident_count']

        stats = {
            "total_gemini_calls": total_model_calls, # Giữ key cũ để JS không lỗi hoặc đổi cả ở JS
            "total_telegram_alerts": total_telegram_alerts,
            "total_incidents": total_incidents,
            "daily_stats": daily_data
        }
        return jsonify(stats)

    except sqlite3.Error as e:
        logging.error(f"Database error fetching stats: {e}")
        if conn: conn.close()
        return jsonify({"error": f"Failed to fetch stats from database: {e}"}), 500 # Trả về lỗi cụ thể hơn
    except Exception as e:
            logging.error(f"Unexpected error fetching stats: {e}", exc_info=True)
            if conn: conn.close()
            return jsonify({"error": "An unexpected error occurred."}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
