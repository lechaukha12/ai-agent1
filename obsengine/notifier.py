# ai-agent1/obsengine/notifier.py
import requests
import logging
import threading
from datetime import datetime, timezone
import os

try:
    from zoneinfo import ZoneInfo
except ImportError:
    logging.error("zoneinfo module not found. Please use Python 3.9+ or install pytz.")
    # Fallback to UTC if zoneinfo is not available
    ZoneInfo = lambda tz_str: timezone.utc

telegram_alerts_counter = 0
notifier_lock = threading.Lock()

try:
    # Cố gắng lấy múi giờ từ biến môi trường, mặc định là Asia/Ho_Chi_Minh
    HCM_TZ = ZoneInfo(os.environ.get("TZ", "Asia/Ho_Chi_Minh"))
except Exception as e:
    logging.warning(f"[Notifier] Could not load timezone '{os.environ.get('TZ', 'Asia/Ho_Chi_Minh')}': {e}. Defaulting display to UTC.")
    HCM_TZ = timezone.utc

# --- Cập nhật hàm gửi cảnh báo Telegram ---
def send_telegram_alert(bot_token, chat_id, alert_data, ai_enabled):
    """
    Gửi cảnh báo chi tiết đến Telegram.

    Args:
        bot_token (str): Token của Telegram Bot.
        chat_id (str): ID của chat hoặc group nhận cảnh báo.
        alert_data (dict): Dictionary chứa thông tin chi tiết về sự cố.
                           Kỳ vọng các keys: 'environment_name', 'resource_name',
                           'environment_type', 'resource_type', 'agent_id', 'severity',
                           'summary', 'root_cause', 'troubleshooting_steps',
                           'initial_reasons', 'alert_time', 'sample_logs'.
        ai_enabled (bool): Cho biết phân tích AI có được bật hay không.

    Returns:
        bool: True nếu gửi thành công, False nếu thất bại.
    """
    global telegram_alerts_counter

    if not bot_token or not chat_id:
        logging.warning("[Notifier] Telegram Bot Token or Chat ID not provided. Skipping alert.")
        return False

    with notifier_lock:
        telegram_alerts_counter += 1
        current_alert_count = telegram_alerts_counter

    # --- Lấy thông tin từ alert_data với cấu trúc mới ---
    environment_name = alert_data.get('environment_name', 'N/A')
    resource_name = alert_data.get('resource_name', 'N/A')
    environment_type = alert_data.get('environment_type', 'N/A')
    resource_type = alert_data.get('resource_type', 'N/A')
    agent_id = alert_data.get('agent_id', 'N/A')
    resource_identifier = alert_data.get('resource_identifier', f"{environment_name}/{resource_name}") # Sử dụng ID tổng hợp nếu có
    # ----------------------------------------------------

    logging.info(f"[Notifier] Attempting to send Telegram alert #{current_alert_count} for {resource_identifier} (Agent: {agent_id}, EnvType: {environment_type})")
    telegram_api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    # --- Điều chỉnh tóm tắt mặc định nếu AI tắt ---
    summary = alert_data.get('summary', 'N/A')
    if not ai_enabled and "Phân tích AI thất bại" not in summary and "Phân tích AI bị tắt" not in summary:
        severity = alert_data.get('severity', 'UNKNOWN')
        initial_reasons = alert_data.get('initial_reasons', 'Không có')
        # Cập nhật summary mặc định
        summary = f"Phát hiện sự cố {severity} cho '{resource_name}' tại '{environment_name}'. Lý do: {initial_reasons}. (AI tắt)"
    # --------------------------------------------

    # --- Xây dựng nội dung tin nhắn với các trường mới ---
    message_lines = [
        f"🚨 *Cảnh báo ObsEngine (Agent: `{agent_id}`)* 🚨",
        f"*Môi trường:* `{environment_name}` (Loại: `{environment_type}`)", # Hiển thị tên và loại môi trường
        f"*Tài nguyên:* `{resource_name}` (Loại: `{resource_type}`)", # Hiển thị tên và loại tài nguyên
        f"*Mức độ:* `{alert_data.get('severity', 'UNKNOWN')}`",
        f"*Tóm tắt:* {summary}"
    ]

    # Thêm chi tiết AI nếu có và hợp lệ
    if ai_enabled:
        root_cause = alert_data.get('root_cause', '')
        steps = alert_data.get('troubleshooting_steps', '')
        default_root_cause = "Không có phân tích AI."
        default_steps = "Kiểm tra ngữ cảnh môi trường và log chi tiết thủ công."
        # Kiểm tra xem AI có cung cấp thông tin hữu ích không
        ai_meaningful = root_cause not in ["N/A", default_root_cause] or steps not in ["N/A", default_steps]

        if ai_meaningful:
             if root_cause and root_cause != "N/A":
                 message_lines.append(f"*Nguyên nhân gốc có thể:*\n{root_cause}")
             if steps and steps != "N/A":
                 message_lines.append(f"*Đề xuất khắc phục:*\n{steps}")

    # Thêm các thông tin còn lại
    sample_logs_str = alert_data.get('sample_logs', '-')
    # Sanitize log messages to avoid Markdown issues
    sanitized_logs = sample_logs_str.replace('`', "'").replace('*', '').replace('_', '')

    message_lines.extend([
        f"*Lý do phát hiện ban đầu:* {alert_data.get('initial_reasons', 'N/A')}",
        f"*Thời gian phát hiện:* `{alert_data.get('alert_time', 'N/A')}`",
        f"*Log mẫu (nếu có):*\n```\n{sanitized_logs}\n```", # Giữ log mẫu
        "\n_Vui lòng kiểm tra chi tiết trên dashboard._" # Sửa lời nhắn cuối
    ])
    # ----------------------------------------------------

    message = "\n".join(message_lines)

    # --- Logic cắt bớt tin nhắn và gửi (giữ nguyên) ---
    max_len = 4096
    truncated_message = message
    if len(message.encode('utf-8')) > max_len:
        # Try to truncate at the last newline before the limit
        safe_truncate_pos = message.rfind('\n', 0, max_len - 100)
        if safe_truncate_pos == -1: safe_truncate_pos = max_len - 100 # Fallback if no newline found
        truncated_message = message[:safe_truncate_pos] + "\n\n_[... message truncated ...]_"
        logging.warning(f"[Notifier] Alert message for {resource_identifier} (Agent: {agent_id}) exceeded Telegram limit and was truncated.")

    payload = {
        'chat_id': chat_id,
        'text': truncated_message,
        'parse_mode': 'Markdown'
    }
    alert_sent_successfully = False
    try:
        response = requests.post(telegram_api_url, json=payload, timeout=20)
        response.raise_for_status() # Will raise HTTPError for bad responses (4xx or 5xx)
        response_data = response.json()
        if response_data.get('ok'):
            logging.info(f"[Notifier] Sent alert to Telegram for {resource_identifier} (Agent: {agent_id}). Response OK.")
            alert_sent_successfully = True
        else:
            # Log Telegram API specific error description
            logging.error(f"[Notifier] Telegram API returned error for {resource_identifier} (Agent: {agent_id}): {response_data.get('description')}")
            # Decrement counter if Telegram explicitly returned an error
            with notifier_lock:
                if telegram_alerts_counter > 0: telegram_alerts_counter -= 1
    except requests.exceptions.RequestException as e:
        # Handle network errors, timeouts, etc.
        logging.error(f"[Notifier] Error sending Telegram alert for {resource_identifier} (Agent: {agent_id}): {e}")
        # Decrement counter on request failure
        with notifier_lock:
            if telegram_alerts_counter > 0: telegram_alerts_counter -= 1
    except Exception as e:
        # Handle unexpected errors during the process
        logging.error(f"[Notifier] An unexpected error occurred during Telegram send for {resource_identifier} (Agent: {agent_id}): {e}", exc_info=True)
        # Decrement counter on unexpected errors
        with notifier_lock:
            if telegram_alerts_counter > 0: telegram_alerts_counter -= 1

    return alert_sent_successfully
# ---------------------------------------------

# --- get_and_reset_telegram_alerts giữ nguyên ---
def get_and_reset_telegram_alerts():
    """Lấy và reset bộ đếm số lượng cảnh báo Telegram đã gửi."""
    global telegram_alerts_counter
    with notifier_lock:
        alerts = telegram_alerts_counter
        telegram_alerts_counter = 0
        return alerts
