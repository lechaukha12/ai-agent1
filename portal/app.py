import os
import sqlite3
import logging
from flask import Flask, jsonify, render_template, request
from datetime import datetime, timedelta, timezone

# --- Cấu hình Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Cấu hình Flask và DB ---
app = Flask(__name__)
# Lấy đường dẫn DB từ biến môi trường, giống như agent
DB_PATH = os.environ.get("DB_PATH", "/data/agent_stats.db")
# Múi giờ để hiển thị (lấy từ TZ nếu có, fallback về UTC)
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
    # Kiểm tra file DB tồn tại không
    if not os.path.exists(DB_PATH):
        logging.error(f"Database file not found at {DB_PATH}. Agent might not have run yet or PV is not mounted correctly.")
        return None # Trả về None nếu file không tồn tại

    try:
        # Kết nối ở chế độ chỉ đọc nếu có thể (an toàn hơn)
        # conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5)
        # Tuy nhiên, chế độ 'ro' có thể không hoạt động với mọi phiên bản/cài đặt SQLite
        # Dùng chế độ mặc định (rw) cho đơn giản trước
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row # Trả về kết quả dạng dictionary-like
        return conn
    except sqlite3.Error as e:
        logging.error(f"Database connection error: {e}")
        return None

@app.route('/')
def index():
    """Render trang HTML chính."""
    # Truyền tên file DB vào template để hiển thị (cho mục đích debug)
    return render_template('index.html', db_path=DB_PATH)

@app.route('/api/incidents')
def get_incidents():
    """API endpoint để lấy danh sách các sự cố gần đây."""
    limit = request.args.get('limit', 100, type=int) # Lấy tối đa 100 sự cố mặc định
    offset = request.args.get('offset', 0, type=int)

    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed or file not found."}), 500

    incidents = []
    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, timestamp, pod_key, severity, summary, initial_reasons
            FROM incidents
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        ''', (limit, offset))
        rows = cursor.fetchall()
        conn.close()

        for row in rows:
            # Chuyển đổi timestamp UTC sang múi giờ hiển thị
            ts_utc = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
            ts_display = ts_utc.astimezone(DISPLAY_TZ)
            incidents.append({
                "id": row['id'],
                # Định dạng lại thời gian cho dễ đọc
                "timestamp": ts_display.strftime('%Y-%m-%d %H:%M:%S %Z'),
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
    # Lấy thống kê cho 7 ngày gần nhất chẳng hạn
    days = request.args.get('days', 7, type=int)
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days-1)

    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed or file not found."}), 500

    stats = {}
    try:
        cursor = conn.cursor()
        # Lấy dữ liệu trong khoảng ngày mong muốn
        cursor.execute('''
            SELECT date, gemini_calls, telegram_alerts, incident_count
            FROM daily_stats
            WHERE date >= ? AND date <= ?
            ORDER BY date DESC
        ''', (start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
        rows = cursor.fetchall()
        conn.close()

        # Tính tổng và chuẩn bị dữ liệu trả về
        total_gemini_calls = 0
        total_telegram_alerts = 0
        total_incidents = 0
        daily_data = []

        for row in rows:
            daily_data.append(dict(row))
            total_gemini_calls += row['gemini_calls']
            total_telegram_alerts += row['telegram_alerts']
            total_incidents += row['incident_count']

        stats = {
            "total_gemini_calls": total_gemini_calls,
            "total_telegram_alerts": total_telegram_alerts,
            "total_incidents": total_incidents,
            "daily_stats": daily_data
        }
        return jsonify(stats)

    except sqlite3.Error as e:
        logging.error(f"Database error fetching stats: {e}")
        if conn: conn.close()
        return jsonify({"error": "Failed to fetch stats from database."}), 500
    except Exception as e:
            logging.error(f"Unexpected error fetching stats: {e}", exc_info=True)
            if conn: conn.close()
            return jsonify({"error": "An unexpected error occurred."}), 500

if __name__ == '__main__':
    # Chạy Flask app, host='0.0.0.0' để truy cập được từ bên ngoài container
    # debug=False khi chạy production
    app.run(host='0.0.0.0', port=5000, debug=False)
