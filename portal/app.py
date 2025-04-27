import os
import sqlite3
import logging
from flask import Flask, jsonify, render_template, request
from datetime import datetime, timedelta, timezone
import math
try:
    from zoneinfo import ZoneInfo
except ImportError:
    logging.error("zoneinfo module not found. Please use Python 3.9+ or install pytz and uncomment the fallback.")
    exit(1)

# --- Cấu hình Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Cấu hình Flask và DB ---
app = Flask(__name__)
DB_PATH = os.environ.get("DB_PATH", "/data/agent_stats.db")
try:
    DISPLAY_TZ_STR = os.environ.get("TZ", "UTC")
    DISPLAY_TZ = ZoneInfo(DISPLAY_TZ_STR)
    logging.info(f"Portal display timezone set to: {DISPLAY_TZ_STR}")
except Exception as e:
    logging.warning(f"Could not load timezone '{os.environ.get('TZ', 'UTC')}': {e}. Defaulting display to UTC.")
    DISPLAY_TZ = timezone.utc


def get_db_connection():
    if not os.path.exists(DB_PATH):
        logging.error(f"Database file not found at {DB_PATH}.")
        return None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logging.error(f"Database connection error: {e}")
        return None

@app.route('/')
def index():
    return render_template('index.html', db_path=DB_PATH)

# --- API Lấy Danh sách Sự cố (Thêm lọc theo ngày) ---
@app.route('/api/incidents')
def get_incidents():
    # Tham số lọc
    pod_filter = request.args.get('pod', default="", type=str).strip()
    severity_filter = request.args.get('severity', default="", type=str).upper().strip()
    # === THAY ĐỔI: Thêm tham số lọc ngày (ISO 8601 UTC) ===
    start_date_str = request.args.get('start_date', default=None, type=str)
    end_date_str = request.args.get('end_date', default=None, type=str)
    # === KẾT THÚC THAY ĐỔI ===
    # Tham số phân trang
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    offset = (page - 1) * per_page

    conn = get_db_connection()
    if conn is None: return jsonify({"error": "Database connection failed or file not found."}), 500

    incidents = []
    total_count = 0
    try:
        cursor = conn.cursor()
        # Xây dựng câu query với điều kiện WHERE động
        base_query = "FROM incidents WHERE 1=1"
        params = []

        if pod_filter:
            base_query += " AND pod_key LIKE ?"
            params.append(f"%{pod_filter}%")
        if severity_filter:
            base_query += " AND severity = ?"
            params.append(severity_filter)
        # === THAY ĐỔI: Thêm điều kiện lọc ngày ===
        if start_date_str:
                try:
                    # Chuyển đổi sang datetime để đảm bảo định dạng đúng
                    start_dt_utc = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
                    base_query += " AND timestamp >= ?"
                    params.append(start_dt_utc.isoformat()) # Dùng ISO string cho query
                except ValueError:
                    logging.warning(f"Invalid start_date format received: {start_date_str}")
                    # Có thể trả lỗi hoặc bỏ qua filter ngày
        if end_date_str:
                try:
                    end_dt_utc = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                    base_query += " AND timestamp <= ?"
                    params.append(end_dt_utc.isoformat())
                except ValueError:
                    logging.warning(f"Invalid end_date format received: {end_date_str}")
        # === KẾT THÚC THAY ĐỔI ===


        # Đếm tổng số lượng trước khi phân trang
        count_query = f"SELECT COUNT(*) as count {base_query}"
        cursor.execute(count_query, tuple(params))
        count_result = cursor.fetchone()
        total_count = count_result['count'] if count_result else 0

        # Lấy dữ liệu đã lọc và phân trang
        data_query = f"""
            SELECT id, timestamp, pod_key, severity, summary, initial_reasons
            {base_query}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """
        params_data = params + [per_page, offset] # Tạo list params mới cho data query
        cursor.execute(data_query, tuple(params_data))
        rows = cursor.fetchall()
        conn.close()

        for row in rows:
            incidents.append({
                "id": row['id'],
                "timestamp": row['timestamp'], # Giữ ISO UTC
                "pod_key": row['pod_key'],
                "severity": row['severity'],
                "summary": row['summary'],
                "initial_reasons": row['initial_reasons']
            })

        total_pages = math.ceil(total_count / per_page)

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
        logging.error(f"Database error fetching incidents: {e}")
        if conn: conn.close()
        return jsonify({"error": f"Failed to fetch incidents: {e}"}), 500
    except Exception as e:
            logging.error(f"Unexpected error fetching incidents: {e}", exc_info=True)
            if conn: conn.close()
            return jsonify({"error": "An unexpected error occurred."}), 500

# --- API Lấy Chi tiết Sự cố (Giữ nguyên) ---
@app.route('/api/incidents/<int:incident_id>')
def get_incident_details(incident_id):
    conn = get_db_connection()
    if conn is None: return jsonify({"error": "Database connection failed or file not found."}), 500
    try:
        cursor = conn.cursor(); cursor.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)); row = cursor.fetchone(); conn.close()
        if row: return jsonify(dict(row))
        else: return jsonify({"error": "Incident not found"}), 404
    except sqlite3.Error as e: logging.error(f"DB error incident details {incident_id}: {e}"); return jsonify({"error": f"Failed to fetch details: {e}"}), 500
    except Exception as e: logging.error(f"Unexpected error incident details: {e}", exc_info=True); return jsonify({"error": "Unexpected error."}), 500


# --- API Lấy Thống kê (Giữ nguyên) ---
@app.route('/api/stats')
def get_stats():
    days = request.args.get('days', 1, type=int)
    if days not in [1, 7, 30]: days = 1
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    conn = get_db_connection()
    if conn is None: return jsonify({"error": "Database connection failed or file not found."}), 500
    stats = {}
    try:
        cursor = conn.cursor()
        # 1. Lấy Thống kê Hàng ngày cho Line Chart
        chart_days = max(days, 7); chart_start_date = end_date - timedelta(days=chart_days)
        cursor.execute('SELECT date, model_calls, telegram_alerts, incident_count FROM daily_stats WHERE date >= ? AND date <= ? ORDER BY date ASC', (chart_start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
        daily_stats_rows = cursor.fetchall(); daily_data_for_chart = [dict(row) for row in daily_stats_rows]
        # 2. Tính Tổng quan
        cursor.execute('SELECT SUM(model_calls) as total_model_calls, SUM(telegram_alerts) as total_telegram_alerts, SUM(incident_count) as total_incidents FROM daily_stats WHERE date >= ? AND date <= ?', (start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
        totals = cursor.fetchone()
        # 3. Tính Phân loại Severity cho Hôm Nay
        today_start_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_end_utc = today_start_utc + timedelta(days=1) - timedelta(microseconds=1)
        cursor.execute('SELECT severity, COUNT(*) as count FROM incidents WHERE timestamp >= ? AND timestamp <= ? GROUP BY severity', (today_start_utc.isoformat(), today_end_utc.isoformat()))
        severity_rows_today = cursor.fetchall(); severity_distribution_today = {row['severity']: row['count'] for row in severity_rows_today}
        # 4. Tính Phân loại Nguồn gốc cho Hôm Nay
        cursor.execute('SELECT initial_reasons, COUNT(*) as count FROM incidents WHERE timestamp >= ? AND timestamp <= ? GROUP BY initial_reasons', (today_start_utc.isoformat(), today_end_utc.isoformat()))
        reason_rows_today = cursor.fetchall(); source_distribution_today = {'Hạ tầng (K8s)': 0, 'Ứng dụng (Loki)': 0, 'Cả hai': 0, 'Khác': 0}
        for row in reason_rows_today:
            reasons = row['initial_reasons'] or ''; is_k8s = 'K8s:' in reasons; is_loki = 'Loki:' in reasons
            if is_k8s and is_loki: source_distribution_today['Cả hai'] += row['count']
            elif is_k8s: source_distribution_today['Hạ tầng (K8s)'] += row['count']
            elif is_loki: source_distribution_today['Ứng dụng (Loki)'] += row['count']
            else: source_distribution_today['Khác'] += row['count']
        source_distribution_today = {k: v for k, v in source_distribution_today.items() if v > 0}
        # 5. Tính Top Pods
        cursor.execute('SELECT pod_key, COUNT(*) as count FROM incidents WHERE timestamp >= ? AND timestamp <= ? GROUP BY pod_key ORDER BY count DESC LIMIT 5', (start_date.isoformat(), end_date.isoformat()))
        top_pods_rows = cursor.fetchall(); top_problematic_pods = {row['pod_key']: row['count'] for row in top_pods_rows}
        # 6. Tính Phân bố Namespace
        cursor.execute("SELECT SUBSTR(pod_key, 1, INSTR(pod_key, '/') - 1) as namespace, COUNT(*) as count FROM incidents WHERE timestamp >= ? AND timestamp <= ? AND INSTR(pod_key, '/') > 0 GROUP BY namespace ORDER BY count DESC", (start_date.isoformat(), end_date.isoformat()))
        namespace_rows = cursor.fetchall(); namespace_distribution = {row['namespace']: row['count'] for row in namespace_rows}
        conn.close()
        stats = {"totals": {"model_calls": totals['total_model_calls'] if totals else 0, "telegram_alerts": totals['total_telegram_alerts'] if totals else 0, "incidents": totals['total_incidents'] if totals else 0,},"daily_stats_for_chart": daily_data_for_chart,"severity_distribution_today": severity_distribution_today,"source_distribution_today": source_distribution_today,"top_problematic_pods": top_problematic_pods,"namespace_distribution": namespace_distribution}
        return jsonify(stats)
    except sqlite3.Error as e: logging.error(f"Database error fetching stats: {e}"); return jsonify({"error": f"Failed to fetch stats: {e}"}), 500
    except Exception as e: logging.error(f"Unexpected error fetching stats: {e}", exc_info=True); return jsonify({"error": "An unexpected error occurred."}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
