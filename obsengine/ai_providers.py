# ai-agent1/obsengine/ai_providers.py
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, InternalServerError, ServiceUnavailable
import requests
import json
import re
import logging
import threading
import os
from datetime import datetime, timezone, MINYEAR
import time

# --- Giữ nguyên các hằng số và thiết lập ban đầu ---
gemini_model_instance = None
last_used_gemini_api_key = None
last_used_gemini_model_id = None
model_calls_counter = 0
ai_counter_lock = threading.Lock()

# Các quy tắc severity hiện tại vẫn tập trung vào K8s/Log
K8S_CRITICAL_REASONS = ["OOMKILLED", "FAILED", "NODEUNREACHABLE", "READINESS PROBE FAILED"]
K8S_ERROR_REASONS = ["ERROR", "CRASHLOOPBACKOFF", "CONTAINERCANNOTRUN", "DEADLINEEXCEEDED"]
K8S_WARNING_REASONS = ["UNSCHEDULABLE", "IMAGEPULLBACKOFF", "ERRIMAGEPULL", "BACKOFF", "WAITING", "NODE PRESSURE", "EVICTED"]
LOG_CRITICAL_KEYWORDS = ["CRITICAL", "ALERT", "EMERGENCY", "PANIC", "FATAL"]
LOG_ERROR_KEYWORDS = ["ERROR", "EXCEPTION", "DENIED", "REFUSED", "UNAUTHORIZED", "FAILURE"]
LOG_WARNING_KEYWORDS = ["WARNING", "WARN", "TIMEOUT", "UNABLE", "SLOW", "DEPRECATED"]
LOG_LEVELS = ["INFO", "WARNING", "ERROR", "CRITICAL"]

# --- Cập nhật hàm xác định severity dựa trên quy tắc ---
# Hiện tại vẫn dựa trên K8s/Log, cần mở rộng trong tương lai cho các loại khác
def determine_severity_from_rules(initial_reasons, log_batch, environment_type="unknown", resource_type="unknown"):
    severity = "INFO"
    reasons_upper_list = [r.upper() for r in initial_reasons if isinstance(r, str)] if initial_reasons else []
    combined_reasons_str = "; ".join(reasons_upper_list)

    # Áp dụng quy tắc K8s nếu là môi trường Kubernetes
    if environment_type == "kubernetes":
        if any(crit in combined_reasons_str for crit in K8S_CRITICAL_REASONS):
            severity = "CRITICAL"
        elif any(err in combined_reasons_str for err in K8S_ERROR_REASONS):
            severity = "ERROR"
        elif any(warn in combined_reasons_str for warn in K8S_WARNING_REASONS):
            severity = "WARNING"

    # Quy tắc dựa trên log (áp dụng cho mọi loại môi trường)
    if severity in ["INFO", "WARNING"]: # Chỉ nâng cấp nếu severity hiện tại thấp
        log_severity = "INFO"
        for entry in log_batch[:30]: # Giới hạn số log kiểm tra
            log_message = entry.get('message')
            if not isinstance(log_message, str):
                continue
            log_upper = log_message.upper()
            # Kiểm tra từ khóa theo mức độ giảm dần
            if any(kw in log_upper for kw in LOG_CRITICAL_KEYWORDS):
                log_severity = "CRITICAL"; break # Ưu tiên CRITICAL cao nhất
            if any(kw in log_upper for kw in LOG_ERROR_KEYWORDS):
                log_severity = "ERROR"; # Tiếp theo là ERROR
            # Chỉ đặt WARNING nếu chưa phải ERROR/CRITICAL
            if log_severity == "INFO" and any(kw in log_upper for kw in LOG_WARNING_KEYWORDS):
                 log_severity = "WARNING"

        # So sánh và chọn mức độ cao hơn
        current_severity_index = LOG_LEVELS.index(severity) if severity in LOG_LEVELS else 0
        log_severity_index = LOG_LEVELS.index(log_severity) if log_severity in LOG_LEVELS else 0

        if log_severity_index > current_severity_index:
            severity = log_severity

    logging.info(f"[Rule Based] Initial Reasons: '{combined_reasons_str}'. Severity determined: {severity} (EnvType: {environment_type})")
    return severity

# --- Cập nhật hàm lấy phân tích mặc định ---
def get_default_analysis(severity, initial_reasons_list, environment_name, resource_name):
     reasons_str = "; ".join(initial_reasons_list) if initial_reasons_list else 'Không có'
     summary = f"Phát hiện sự cố tiềm ẩn cho tài nguyên '{resource_name}' trong môi trường '{environment_name}'. Lý do ban đầu: {reasons_str}. Mức độ ước tính (Rule-based): {severity}."
     if severity != "INFO":
        summary += " (Phân tích AI bị tắt hoặc thất bại)."
     root_cause = "Không có phân tích AI."
     troubleshooting_steps = "Kiểm tra ngữ cảnh môi trường và log chi tiết thủ công."
     return {"severity": severity, "summary": summary, "root_cause": root_cause, "troubleshooting_steps": troubleshooting_steps}

# --- Các hàm gọi API (_call_gemini_api_with_retry, _call_local_api_with_retry) giữ nguyên ---
def _call_gemini_api_with_retry(api_key, model_identifier, prompt, max_retries=2, initial_delay=5):
    global gemini_model_instance, last_used_gemini_api_key, last_used_gemini_model_id
    retries = 0
    delay = initial_delay
    while retries <= max_retries:
        try:
            needs_reinit = (
                not gemini_model_instance or
                last_used_gemini_api_key != api_key or
                last_used_gemini_model_id != model_identifier
            )

            if needs_reinit:
                logging.info(f"[AI Provider] Initializing/Re-initializing Gemini client for model {model_identifier}")
                if not api_key: raise ValueError("Cannot initialize Gemini client: API key is missing.")
                genai.configure(api_key=api_key)
                gemini_model_instance = genai.GenerativeModel(model_identifier)
                last_used_gemini_api_key = api_key
                last_used_gemini_model_id = model_identifier
                logging.info("[AI Provider] Gemini client initialized/re-initialized successfully.")

            logging.debug(f"[AI Provider] Generating content with model '{model_identifier}' (Attempt {retries + 1})...")
            response = gemini_model_instance.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(temperature=0.2, max_output_tokens=800),
                request_options={'timeout': 120}
            )
            logging.debug("[AI Provider] Content generation successful.")
            return response

        except (ResourceExhausted, InternalServerError, ServiceUnavailable) as e:
            retries += 1
            logging.warning(f"[AI Provider] Gemini API error ({type(e).__name__}) for model '{model_identifier}'. Retrying ({retries}/{max_retries}) in {delay}s... Error: {getattr(e, 'message', str(e))}")
            if retries > max_retries:
                logging.error(f"[AI Provider] Gemini API error failed after {max_retries} retries.")
                gemini_model_instance = None
                last_used_gemini_api_key = None
                last_used_gemini_model_id = None
                raise
            time.sleep(delay)
            delay *= 2

        except Exception as e:
            logging.error(f"[AI Provider] Unexpected error calling Gemini API: {e}", exc_info=True)
            gemini_model_instance = None
            last_used_gemini_api_key = None
            last_used_gemini_model_id = None
            raise

    raise Exception("Gemini API call failed after maximum retries.")

def _call_local_api_with_retry(endpoint_url, prompt, max_retries=2, initial_delay=5):
    retries = 0
    delay = initial_delay
    while retries <= max_retries:
        try:
            logging.debug(f"[AI Provider] Calling local AI endpoint {endpoint_url} (Attempt {retries + 1})...")
            response = requests.post(endpoint_url, json={"prompt": prompt}, timeout=180)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            retries += 1
            logging.warning(f"[AI Provider] Error calling local AI endpoint {endpoint_url}. Retrying ({retries}/{max_retries}) in {delay}s... Error: {e}")
            if retries > max_retries:
                logging.error(f"[AI Provider] Local AI endpoint call failed after {max_retries} retries.")
                raise
            time.sleep(delay)
            delay *= 2
    raise Exception("Local AI API call failed after maximum retries.")

# --- Hàm parse JSON (_parse_ai_json_response) giữ nguyên ---
def _parse_ai_json_response(raw_response_text):
    if not raw_response_text:
        return None, "No content in response"
    text_to_parse = raw_response_text.strip()
    if text_to_parse.startswith("```json"):
        text_to_parse = text_to_parse[len("```json"):].strip()
    if text_to_parse.endswith("```"):
        text_to_parse = text_to_parse[:-len("```")].strip()
    if text_to_parse.startswith("```"):
        text_to_parse = text_to_parse[len("```"):].strip()

    match = re.search(r'\{.*\}', text_to_parse, re.DOTALL)
    if match:
        json_string = match.group(0)
    else:
        json_string = text_to_parse
        logging.warning("[AI Parser] No JSON object found using regex, attempting to parse the whole response.")

    try:
        analysis_result = json.loads(json_string)
        if not isinstance(analysis_result, dict):
            raise ValueError("Parsed response is not a dictionary.")
        required_keys = ["severity", "summary", "root_cause", "troubleshooting_steps"]
        if not all(key in analysis_result for key in required_keys):
             logging.warning(f"[AI Parser] Parsed JSON is missing required keys. Found: {list(analysis_result.keys())}")
        return analysis_result, None
    except json.JSONDecodeError as json_err:
        logging.warning(f"[AI Parser] Failed to decode JSON: {json_err}. String attempted: '{json_string[:200]}...'")
        return None, f"JSON Decode Error: {json_err}"
    except ValueError as val_err:
        logging.warning(f"[AI Parser] Invalid JSON structure: {val_err}. String attempted: '{json_string[:200]}...'")
        return None, f"Invalid JSON structure: {val_err}"
    except Exception as e:
         logging.error(f"[AI Parser] Unexpected error parsing JSON: {e}. String attempted: '{json_string[:200]}...'", exc_info=True)
         return None, f"Unexpected parsing error: {e}"

# --- Hàm thực thi phân tích (_execute_ai_analysis) giữ nguyên ---
def _execute_ai_analysis(provider, api_key, model_id, local_endpoint_url, prompt):
    global model_calls_counter
    analysis_result = None
    raw_response_text = None
    analysis_failed = False
    error_message = None
    call_attempted = False

    try:
        if provider == "gemini":
            call_attempted = True
            if not api_key: raise ValueError("Gemini provider selected but API key is missing.")
            if not model_id: raise ValueError("Gemini provider selected but model identifier is missing.")
            with ai_counter_lock: model_calls_counter += 1
            logging.info(f"[AI Provider] Calling Gemini API (Model: {model_id}) - Call count: {model_calls_counter}")
            gemini_response = _call_gemini_api_with_retry(api_key, model_id, prompt)

            if not gemini_response or not gemini_response.candidates:
                 logging.warning(f"[AI Provider] Gemini response is invalid or has no candidates. Raw response obj: {gemini_response}")
                 raw_response_text = str(gemini_response) if gemini_response else "No response object"
                 analysis_failed = True
                 error_message = "Gemini không trả về ứng viên hợp lệ."
            elif not gemini_response.parts:
                 logging.warning(f"[AI Provider] Gemini response has no parts (content blocked?). Raw response obj: {gemini_response}")
                 raw_response_text = str(gemini_response)
                 analysis_failed = True
                 try:
                     candidate = gemini_response.candidates[0]
                     finish_reason = candidate.finish_reason if candidate else "UNKNOWN"
                     safety_ratings = candidate.safety_ratings if candidate else []
                     logging.warning(f"[AI Provider] Gemini Finish Reason: {finish_reason}, Safety Ratings: {safety_ratings}")
                     if finish_reason.name == 'SAFETY': error_message = "Phản hồi bị chặn bởi bộ lọc an toàn Gemini."
                     elif finish_reason.name == 'MAX_TOKENS': error_message = "Phản hồi Gemini bị cắt do đạt giới hạn token."
                     elif finish_reason.name == 'RECITATION': error_message = "Phản hồi bị chặn do trích dẫn nguồn."
                     else: error_message = f"Gemini không trả về nội dung (Lý do: {finish_reason.name})."
                 except Exception as inner_e:
                     logging.error(f"Error extracting finish reason: {inner_e}")
                     error_message = "Gemini không trả về nội dung (Không rõ lý do)."
            else:
                raw_response_text = gemini_response.text.strip()
                analysis_result, parse_error = _parse_ai_json_response(raw_response_text)
                if parse_error:
                    analysis_failed = True
                    error_message = f"Lỗi phân tích phản hồi Gemini: {parse_error}"

        elif provider == "local":
            call_attempted = True
            if not local_endpoint_url: raise ValueError("Local provider selected but endpoint URL is not configured.")
            with ai_counter_lock: model_calls_counter += 1
            logging.info(f"[AI Provider] Calling local AI endpoint ({local_endpoint_url}) - Call count: {model_calls_counter}")
            local_response = _call_local_api_with_retry(local_endpoint_url, prompt)
            raw_response_text = local_response.text
            analysis_result, parse_error = _parse_ai_json_response(raw_response_text)
            if parse_error:
                 analysis_failed = True
                 error_message = f"Lỗi phân tích phản hồi Local AI: {parse_error}"

        else:
             logging.warning(f"[AI Provider] Unsupported AI provider '{provider}' or provider is 'none'. Skipping AI call.")
             analysis_failed = True
             error_message = "Provider AI không được hỗ trợ hoặc bị tắt."

    except Exception as e:
        logging.error(f"[AI Provider] Error during AI analysis call (Provider: {provider}): {e}", exc_info=True)
        analysis_failed = True
        error_message = f"Lỗi khi gọi API AI: {e}"
        if call_attempted:
            with ai_counter_lock:
                if model_calls_counter > 0: model_calls_counter -= 1
                logging.warning(f"[AI Provider] Decremented call counter due to error. Current count: {model_calls_counter}")

    return analysis_result, raw_response_text, analysis_failed, error_message

# --- Cập nhật hàm perform_analysis ---
def perform_analysis(
    logs, # Đổi tên từ log_batch
    environment_context, # Thay k8s_context
    initial_reasons_list,
    config,
    prompt_template,
    environment_name, # Thêm
    resource_name,    # Thêm
    environment_type, # Thêm
    resource_type     # Thêm
    ):
    analysis_result = None
    final_prompt = None
    raw_response_text = None
    analysis_failed = False
    ai_error_message = None
    resource_identifier = f"{environment_name}/{resource_name}" # ID để log

    if not config.get('enable_ai_analysis', False):
        logging.info(f"[AI Analysis - {resource_identifier}] AI analysis is disabled by configuration.")
        analysis_failed = True
        ai_error_message = "AI analysis disabled"
    else:
        # --- Xác định namespace/pod_name nếu là K8s để tương thích prompt cũ ---
        namespace = "N/A"
        pod_name = "N/A" # Đổi tên biến này cho rõ ràng hơn
        if environment_type == "kubernetes" and resource_type == "pod":
            try:
                # Giả định resource_name có dạng "namespace/podname"
                parts = resource_name.split('/', 1)
                if len(parts) == 2:
                    namespace, pod_name = parts
                else:
                    logging.warning(f"[AI Analysis - {resource_identifier}] Could not parse namespace/pod from resource_name '{resource_name}'.")
                    pod_name = resource_name # Sử dụng toàn bộ resource_name nếu không parse được
            except Exception as parse_err:
                logging.error(f"[AI Analysis - {resource_identifier}] Error parsing namespace/pod: {parse_err}")
                pod_name = resource_name
        elif environment_type != "kubernetes":
             # Nếu không phải K8s, sử dụng tên resource và environment trực tiếp
             namespace = environment_name # Hoặc để là N/A tùy ý
             pod_name = resource_name
        # -----------------------------------------------------------------

        log_text = "N/A"
        if logs and isinstance(logs, list):
             limited_logs = []
             total_log_length = 0
             max_log_length = 25000
             # Sắp xếp log theo timestamp giảm dần (mới nhất trước)
             try:
                 logs.sort(key=lambda x: x.get('timestamp', datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
             except Exception as sort_err:
                  logging.warning(f"Error sorting logs for {resource_identifier}: {sort_err}. Proceeding without sorting.")

             for entry in logs:
                 if isinstance(entry, dict) and 'timestamp' in entry and 'message' in entry:
                     ts = entry['timestamp']
                     # Chuyển đổi timestamp nếu nó là datetime object
                     ts_str = ts.isoformat() if isinstance(ts, datetime) else str(ts)
                     msg = str(entry['message'])
                     log_line = f"[{ts_str}] {msg}"
                     if total_log_length + len(log_line) < max_log_length:
                         limited_logs.append(log_line)
                         total_log_length += len(log_line) + 1 # +1 for newline
                     else:
                         logging.warning(f"Log text limit ({max_log_length} chars) reached for {resource_identifier}. Truncating logs for prompt.")
                         break
             # Đảo ngược lại để có thứ tự thời gian tăng dần trong prompt
             limited_logs.reverse()
             log_text = "\n".join(limited_logs) if limited_logs else "N/A"

        # Giới hạn context môi trường
        max_context_length = 15000 # Đổi tên biến
        context_limited = environment_context[:max_context_length] if environment_context else "N/A"
        if environment_context and len(environment_context) > max_context_length:
             logging.warning(f"Environment context limit ({max_context_length} chars) reached for {resource_identifier}. Truncating context for prompt.")

        # --- Tạo prompt động dựa trên initial reasons (logic K8s giữ nguyên) ---
        prompt_instructions = []
        reasons_str_upper = "; ".join(map(str, initial_reasons_list)).upper() if initial_reasons_list else ""

        # Chỉ áp dụng các gợi ý K8s nếu đúng loại môi trường
        if environment_type == "kubernetes":
            if "OOMKILLED" in reasons_str_upper:
                prompt_instructions.append("Kiểm tra kỹ thông tin 'limits' và 'requests' của container trong Ngữ cảnh Môi trường. Phân tích xem memory usage có tăng đột biến trong logs không.")
            if "CRASHLOOPBACKOFF" in reasons_str_upper:
                prompt_instructions.append("Tìm kiếm các lỗi nghiêm trọng (fatal error, exception, panic) trong logs ngay trước thời điểm container bị restart (dựa vào last terminated state trong Ngữ cảnh Môi trường).")
            if "UNSCHEDULABLE" in reasons_str_upper:
                prompt_instructions.append("Phân tích lý do không thể điều phối pod dựa vào pod conditions, node conditions, resource quotas/limits, node selectors, affinity/anti-affinity, taints/tolerations trong Ngữ cảnh Môi trường.")
            if "IMAGEPULLBACKOFF" in reasons_str_upper or "ERRIMAGEPULL" in reasons_str_upper:
                prompt_instructions.append("Xác nhận tên image và tag có chính xác không. Kiểm tra ImagePullSecrets và kết nối mạng tới container registry trong Ngữ cảnh Môi trường.")
            if "READINESS PROBE FAILED" in reasons_str_upper or "LIVENESS PROBE FAILED" in reasons_str_upper:
                 prompt_instructions.append("Kiểm tra cấu hình readiness/liveness probe trong Ngữ cảnh Môi trường. Phân tích logs để tìm lý do tại sao ứng dụng không phản hồi probe (ví dụ: khởi động chậm, lỗi kết nối, treo ứng dụng).")
        # TODO: Thêm các prompt_instructions cho các loại môi trường/lý do khác (Linux, Windows...)

        dynamic_prompt_part = ""
        if prompt_instructions:
            dynamic_prompt_part = "\n**Yêu cầu phân tích bổ sung dựa trên lý do ban đầu:**\n- " + "\n- ".join(prompt_instructions) + "\n"
        # ---------------------------------------------------------------------

        # --- Format prompt cuối cùng ---
        try:
            reasons_str_for_prompt = "; ".join(map(str, initial_reasons_list)) if initial_reasons_list else "N/A"
            # Sử dụng các biến đã chuẩn bị (namespace, pod_name có thể là N/A hoặc tên resource)
            # Cập nhật tên placeholder trong format
            formatted_prompt_template = prompt_template + dynamic_prompt_part
            final_prompt = formatted_prompt_template.format(
                namespace=namespace, # Vẫn dùng placeholder cũ nếu template yêu cầu
                pod_name=pod_name,   # Vẫn dùng placeholder cũ nếu template yêu cầu
                environment_name=environment_name, # Thêm placeholder mới nếu template cập nhật
                resource_name=resource_name,       # Thêm placeholder mới nếu template cập nhật
                environment_type=environment_type, # Thêm placeholder mới nếu template cập nhật
                resource_type=resource_type,       # Thêm placeholder mới nếu template cập nhật
                initial_reasons=reasons_str_for_prompt,
                # --- Đổi tên placeholder context ---
                environment_context=context_limited, # Thay k8s_context
                # -----------------------------------
                log_text=log_text
            )
        except KeyError as e:
            logging.error(f"[AI Analysis - {resource_identifier}] Missing placeholder in PROMPT_TEMPLATE: {e}. Using default prompt structure.")
            # --- Cập nhật default prompt ---
            default_prompt_base = f"Phân tích tài nguyên '{resource_name}' (Loại: {resource_type}) trong môi trường '{environment_name}' (Loại: {environment_type}). Lý do ban đầu: {reasons_str_for_prompt}. Ngữ cảnh Môi trường: {context_limited}. Logs: {log_text}."
            final_prompt = default_prompt_base + dynamic_prompt_part + " Chỉ trả lời bằng JSON với khóa 'severity', 'summary', 'root_cause', 'troubleshooting_steps'."
            # -----------------------------
        except Exception as format_err:
             logging.error(f"[AI Analysis - {resource_identifier}] Error formatting prompt: {format_err}. Using default prompt structure.", exc_info=True)
             default_prompt_base = f"Phân tích tài nguyên '{resource_name}' (Loại: {resource_type}) trong môi trường '{environment_name}' (Loại: {environment_type}). Lý do ban đầu: {reasons_str_for_prompt}. Ngữ cảnh Môi trường: {context_limited}. Logs: {log_text}."
             final_prompt = default_prompt_base + dynamic_prompt_part + " Chỉ trả lời bằng JSON với khóa 'severity', 'summary', 'root_cause', 'troubleshooting_steps'."
        # ---------------------------

        # --- Gọi API AI (logic giữ nguyên) ---
        provider = config.get('ai_provider', 'none')
        api_key = config.get('ai_api_key', '')
        model_id = config.get('ai_model_identifier', '')
        local_endpoint = config.get('local_gemini_endpoint')

        analysis_result, raw_response_text, analysis_failed, ai_error_message = _execute_ai_analysis(
            provider, api_key, model_id, local_endpoint, final_prompt
        )
        # ---------------------------------

    # --- Xử lý kết quả và fallback ---
    if analysis_failed or analysis_result is None:
        if analysis_result is None and not analysis_failed:
             logging.warning(f"[AI Analysis - {resource_identifier}] AI analysis function returned successfully but result is None (likely parsing failed). Falling back.")
             ai_error_message = ai_error_message or "AI result parsing failed"
        elif analysis_failed:
             logging.warning(f"[AI Analysis - {resource_identifier}] AI analysis failed or provider unsupported/misconfigured. Falling back to rule-based analysis. Reason: {ai_error_message}")

        # --- Gọi hàm xác định severity với các tham số mới ---
        severity = determine_severity_from_rules(initial_reasons_list, logs, environment_type, resource_type)
        analysis_result = get_default_analysis(severity, initial_reasons_list, environment_name, resource_name)
        # ---------------------------------------------------
        if ai_error_message:
             analysis_result["summary"] += f" (AI Error: {ai_error_message})"

    # Đảm bảo kết quả cuối cùng là dict và có đủ các key
    if not isinstance(analysis_result, dict):
         logging.error(f"[AI Analysis - {resource_identifier}] CRITICAL: Analysis result is not a dictionary after processing. Final fallback.")
         severity = determine_severity_from_rules(initial_reasons_list, logs, environment_type, resource_type)
         analysis_result = get_default_analysis(severity, initial_reasons_list, environment_name, resource_name)
         raw_response_text = raw_response_text or "[Fallback due to invalid result type]"
         if ai_error_message: analysis_result["summary"] += f" (AI Error: {ai_error_message})"
    else:
         # Đảm bảo các key tồn tại, sử dụng rule-based severity làm fallback nếu AI trả về severity không hợp lệ
         rule_based_severity = determine_severity_from_rules(initial_reasons_list, logs, environment_type, resource_type)
         ai_severity = analysis_result.get("severity", rule_based_severity).upper()
         if ai_severity not in LOG_LEVELS:
             logging.warning(f"Invalid severity '{analysis_result.get('severity')}' returned by AI for {resource_identifier}. Falling back to rule-based: {rule_based_severity}")
             analysis_result["severity"] = rule_based_severity
         else:
              analysis_result["severity"] = ai_severity # Đảm bảo là UPPERCASE

         analysis_result.setdefault("summary", "N/A")
         analysis_result.setdefault("root_cause", "N/A")
         analysis_result.setdefault("troubleshooting_steps", "N/A")

    return analysis_result, final_prompt, raw_response_text
# --------------------------------

# --- get_and_reset_model_calls giữ nguyên ---
def get_and_reset_model_calls():
    global model_calls_counter
    with ai_counter_lock:
        calls = model_calls_counter
        model_calls_counter = 0
        return calls

