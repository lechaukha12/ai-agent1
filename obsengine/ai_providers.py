# ai-agent1/obsengine/ai_providers.py
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, InternalServerError, ServiceUnavailable
import requests
import json
import re
import logging
import threading
import os
from datetime import datetime, timezone, MINYEAR # <--- THÊM timezone VÀ MINYEAR Ở ĐÂY
import time

# --- Giữ nguyên các hằng số và hàm đã có ---
gemini_model_instance = None
last_used_gemini_api_key = None
last_used_gemini_model_id = None
model_calls_counter = 0
ai_counter_lock = threading.Lock()

K8S_CRITICAL_REASONS = ["OOMKILLED", "FAILED", "NODEUNREACHABLE", "READINESS PROBE FAILED"]
K8S_ERROR_REASONS = ["ERROR", "CRASHLOOPBACKOFF", "CONTAINERCANNOTRUN", "DEADLINEEXCEEDED"]
K8S_WARNING_REASONS = ["UNSCHEDULABLE", "IMAGEPULLBACKOFF", "ERRIMAGEPULL", "BACKOFF", "WAITING", "NODE PRESSURE", "EVICTED"]
LOG_CRITICAL_KEYWORDS = ["CRITICAL", "ALERT", "EMERGENCY", "PANIC", "FATAL"]
LOG_ERROR_KEYWORDS = ["ERROR", "EXCEPTION", "DENIED", "REFUSED", "UNAUTHORIZED", "FAILURE"]
LOG_WARNING_KEYWORDS = ["WARNING", "WARN", "TIMEOUT", "UNABLE", "SLOW", "DEPRECATED"]
LOG_LEVELS = ["INFO", "WARNING", "ERROR", "CRITICAL"]

def determine_severity_from_rules(initial_reasons, log_batch):
    severity = "INFO"
    reasons_upper_list = [r.upper() for r in initial_reasons if isinstance(r, str)] if initial_reasons else []
    combined_reasons_str = "; ".join(reasons_upper_list)

    if any(crit in combined_reasons_str for crit in K8S_CRITICAL_REASONS):
        severity = "CRITICAL"
    elif any(err in combined_reasons_str for err in K8S_ERROR_REASONS):
        severity = "ERROR"
    elif any(warn in combined_reasons_str for warn in K8S_WARNING_REASONS):
        severity = "WARNING"

    if severity in ["INFO", "WARNING"]:
        log_severity = "INFO"
        for entry in log_batch[:30]:
            log_message = entry.get('message')
            if not isinstance(log_message, str):
                continue
            log_upper = log_message.upper()
            if any(kw in log_upper for kw in LOG_CRITICAL_KEYWORDS):
                log_severity = "CRITICAL"; break
            if any(kw in log_upper for kw in LOG_ERROR_KEYWORDS):
                log_severity = "ERROR";
            if log_severity == "INFO" and any(kw in log_upper for kw in LOG_WARNING_KEYWORDS):
                 log_severity = "WARNING"

        current_severity_index = LOG_LEVELS.index(severity) if severity in LOG_LEVELS else 0
        log_severity_index = LOG_LEVELS.index(log_severity) if log_severity in LOG_LEVELS else 0

        if log_severity_index > current_severity_index:
            severity = log_severity

    logging.info(f"[Rule Based] Initial Reasons: '{combined_reasons_str}'. Severity determined: {severity}")
    return severity

def get_default_analysis(severity, initial_reasons_list):
     reasons_str = "; ".join(initial_reasons_list) if initial_reasons_list else 'Không có'
     summary = f"Phát hiện sự cố tiềm ẩn. Lý do ban đầu: {reasons_str}. Mức độ ước tính (Rule-based): {severity}."
     if severity != "INFO":
        summary += " (Phân tích AI bị tắt hoặc thất bại)."
     root_cause = "Không có phân tích AI."
     troubleshooting_steps = "Kiểm tra ngữ cảnh Kubernetes và log chi tiết thủ công."
     return {"severity": severity, "summary": summary, "root_cause": root_cause, "troubleshooting_steps": troubleshooting_steps}

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

def perform_analysis(log_batch, k8s_context, initial_reasons_list, config, prompt_template):
    analysis_result = None
    final_prompt = None
    raw_response_text = None
    analysis_failed = False
    ai_error_message = None

    if not config.get('enable_ai_analysis', False):
        logging.info("[AI Analysis] AI analysis is disabled by configuration.")
        analysis_failed = True
        ai_error_message = "AI analysis disabled"
    else:
        namespace = "unknown_namespace"
        pod_name = "unknown_pod"
        if log_batch and isinstance(log_batch, list) and len(log_batch) > 0 and isinstance(log_batch[0], dict) and log_batch[0].get('labels'):
             labels = log_batch[0].get('labels', {})
             namespace = labels.get('namespace', namespace)
             pod_name = labels.get('pod', pod_name)
        elif k8s_context:
             match_ns_pod = re.search(r"Pod:\s*([\w.-]+)/([\w.-]+)", k8s_context)
             if match_ns_pod:
                 namespace = match_ns_pod.group(1)
                 pod_name = match_ns_pod.group(2)

        log_text = "N/A"
        if log_batch and isinstance(log_batch, list):
             limited_logs = []
             total_log_length = 0
             max_log_length = 25000
             # Sử dụng datetime.min.replace(tzinfo=timezone.utc) để có giá trị mặc định timezone-aware
             log_batch.sort(key=lambda x: x.get('timestamp', datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
             for entry in log_batch:
                 if isinstance(entry, dict) and 'timestamp' in entry and 'message' in entry:
                     ts = entry['timestamp']
                     ts_str = ts.isoformat() if isinstance(ts, datetime) else str(ts)
                     msg = str(entry['message'])
                     log_line = f"[{ts_str}] {msg}"
                     if total_log_length + len(log_line) < max_log_length:
                         limited_logs.append(log_line)
                         total_log_length += len(log_line) + 1
                     else:
                         logging.warning(f"Log text limit ({max_log_length} chars) reached. Truncating logs for prompt.")
                         break
             limited_logs.reverse()
             log_text = "\n".join(limited_logs) if limited_logs else "N/A"

        max_k8s_context_length = 15000
        k8s_context_limited = k8s_context[:max_k8s_context_length] if k8s_context else "N/A"
        if k8s_context and len(k8s_context) > max_k8s_context_length:
             logging.warning(f"K8s context limit ({max_k8s_context_length} chars) reached. Truncating context for prompt.")

        prompt_instructions = []
        reasons_str_upper = "; ".join(map(str, initial_reasons_list)).upper() if initial_reasons_list else ""

        if "OOMKILLED" in reasons_str_upper:
            prompt_instructions.append("Kiểm tra kỹ thông tin 'limits' và 'requests' của container trong Ngữ cảnh Kubernetes. Phân tích xem memory usage có tăng đột biến trong logs không.")
        if "CRASHLOOPBACKOFF" in reasons_str_upper:
            prompt_instructions.append("Tìm kiếm các lỗi nghiêm trọng (fatal error, exception, panic) trong logs ngay trước thời điểm container bị restart (dựa vào last terminated state).")
        if "UNSCHEDULABLE" in reasons_str_upper:
            prompt_instructions.append("Phân tích lý do không thể điều phối pod dựa vào pod conditions, node conditions, resource quotas/limits, node selectors, affinity/anti-affinity, taints/tolerations trong Ngữ cảnh Kubernetes.")
        if "IMAGEPULLBACKOFF" in reasons_str_upper or "ERRIMAGEPULL" in reasons_str_upper:
            prompt_instructions.append("Xác nhận tên image và tag có chính xác không. Kiểm tra ImagePullSecrets và kết nối mạng tới container registry.")
        if "READINESS PROBE FAILED" in reasons_str_upper or "LIVENESS PROBE FAILED" in reasons_str_upper:
             prompt_instructions.append("Kiểm tra cấu hình readiness/liveness probe trong K8s context. Phân tích logs để tìm lý do tại sao ứng dụng không phản hồi probe (ví dụ: khởi động chậm, lỗi kết nối, treo ứng dụng).")

        dynamic_prompt_part = ""
        if prompt_instructions:
            dynamic_prompt_part = "\n**Yêu cầu phân tích bổ sung dựa trên lý do ban đầu:**\n- " + "\n- ".join(prompt_instructions) + "\n"

        try:
            reasons_str_for_prompt = "; ".join(map(str, initial_reasons_list)) if initial_reasons_list else "N/A"
            formatted_prompt_template = prompt_template + dynamic_prompt_part
            final_prompt = formatted_prompt_template.format(
                namespace=namespace,
                pod_name=pod_name,
                initial_reasons=reasons_str_for_prompt,
                k8s_context=k8s_context_limited,
                log_text=log_text
            )
        except KeyError as e:
            logging.error(f"[AI Analysis] Missing placeholder in PROMPT_TEMPLATE: {e}. Using default prompt structure.")
            default_prompt_base = f"Phân tích pod {namespace}/{pod_name}. Lý do ban đầu: {reasons_str_for_prompt}. Ngữ cảnh K8s: {k8s_context_limited}. Logs: {log_text}."
            final_prompt = default_prompt_base + dynamic_prompt_part + " Chỉ trả lời bằng JSON với khóa 'severity', 'summary', 'root_cause', 'troubleshooting_steps'."
        except Exception as format_err:
             logging.error(f"[AI Analysis] Error formatting prompt: {format_err}. Using default prompt structure.", exc_info=True)
             default_prompt_base = f"Phân tích pod {namespace}/{pod_name}. Lý do ban đầu: {reasons_str_for_prompt}. Ngữ cảnh K8s: {k8s_context_limited}. Logs: {log_text}."
             final_prompt = default_prompt_base + dynamic_prompt_part + " Chỉ trả lời bằng JSON với khóa 'severity', 'summary', 'root_cause', 'troubleshooting_steps'."

        provider = config.get('ai_provider', 'none')
        api_key = config.get('ai_api_key', '')
        model_id = config.get('ai_model_identifier', '')
        local_endpoint = config.get('local_gemini_endpoint')

        analysis_result, raw_response_text, analysis_failed, ai_error_message = _execute_ai_analysis(
            provider, api_key, model_id, local_endpoint, final_prompt
        )

    if analysis_failed or analysis_result is None:
        if analysis_result is None and not analysis_failed:
             logging.warning(f"[AI Analysis] AI analysis function returned successfully but result is None (likely parsing failed). Falling back.")
             ai_error_message = ai_error_message or "AI result parsing failed"
        elif analysis_failed:
             logging.warning(f"[AI Analysis] AI analysis failed or provider unsupported/misconfigured. Falling back to rule-based analysis. Reason: {ai_error_message}")

        severity = determine_severity_from_rules(initial_reasons_list, log_batch)
        analysis_result = get_default_analysis(severity, initial_reasons_list)
        if ai_error_message:
             analysis_result["summary"] += f" (AI Error: {ai_error_message})"

    if not isinstance(analysis_result, dict):
         logging.error(f"[AI Analysis] CRITICAL: Analysis result is not a dictionary after processing. Final fallback.")
         severity = determine_severity_from_rules(initial_reasons_list, log_batch)
         analysis_result = get_default_analysis(severity, initial_reasons_list)
         raw_response_text = raw_response_text or "[Fallback due to invalid result type]"
         if ai_error_message: analysis_result["summary"] += f" (AI Error: {ai_error_message})"
    else:
         analysis_result.setdefault("severity", determine_severity_from_rules(initial_reasons_list, log_batch))
         analysis_result.setdefault("summary", "N/A")
         analysis_result.setdefault("root_cause", "N/A")
         analysis_result.setdefault("troubleshooting_steps", "N/A")
         if analysis_result["severity"].upper() not in LOG_LEVELS:
             logging.warning(f"Invalid severity '{analysis_result['severity']}' returned. Falling back to rule-based.")
             analysis_result["severity"] = determine_severity_from_rules(initial_reasons_list, log_batch)

    return analysis_result, final_prompt, raw_response_text

def get_and_reset_model_calls():
    global model_calls_counter
    with ai_counter_lock:
        calls = model_calls_counter
        model_calls_counter = 0
        return calls

