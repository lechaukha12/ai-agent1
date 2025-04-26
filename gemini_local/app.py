import os
import logging
from flask import Flask, request, jsonify
import re # Thêm import re
try:
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    import torch
except ImportError:
    logging.error("Transformers or PyTorch not installed.")
    exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# --- Load Model ---
MODEL_PATH = os.environ.get("MODEL_PATH", "/app/local_model") # Đường dẫn model trong container
DEVICE = "cpu" # Chỉ chạy CPU
model = None
tokenizer = None

try:
    logging.info(f"Loading local model from: {MODEL_PATH}")
    if not os.path.isdir(MODEL_PATH):
            raise FileNotFoundError(f"Model directory not found at {MODEL_PATH}")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
    model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_PATH)
    model.to(DEVICE)
    model.eval() # Chế độ evaluation
    logging.info(f"Local model loaded successfully onto device: {DEVICE}")
except Exception as e:
    logging.error(f"Failed to load model: {e}", exc_info=True)
    # Flask vẫn chạy nhưng sẽ trả lỗi 503 ở healthz

@app.route('/healthz')
def healthz():
    # Endpoint kiểm tra sức khỏe đơn giản
    if model and tokenizer:
        return "OK", 200
    else:
        return "Model not loaded", 503

@app.route('/generate', methods=['POST'])
def generate_text():
    """Endpoint nhận prompt và trả về kết quả phân tích."""
    global model, tokenizer # Sử dụng model và tokenizer đã load

    if not model or not tokenizer:
        logging.error("Model or tokenizer not loaded.")
        return jsonify({"error": "Model not ready"}), 503

    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    prompt = data.get('prompt')

    if not prompt:
        return jsonify({"error": "Missing 'prompt' in JSON body"}), 400

    logging.info(f"Received prompt (length: {len(prompt)}): {prompt[:200]}...")

    analysis_result = {"severity": "INFO", "summary": "Phân tích model local chưa hoàn chỉnh."} # Mặc định

    try:
        # Tokenize input
        inputs = tokenizer(prompt, return_tensors="pt", max_length=1024, truncation=True).to(DEVICE)

        # Generate output
        with torch.no_grad():
                outputs = model.generate(**inputs, max_new_tokens=150, num_beams=2, early_stopping=True)

        # Decode output
        response_text = tokenizer.decode(outputs[0], skip_special_tokens=True).strip()
        logging.info(f"Local model raw response: {response_text}")

        # Parse JSON
        cleaned_response_text = response_text
        if cleaned_response_text.startswith("```json"): cleaned_response_text = cleaned_response_text.strip("```json").strip("`").strip()
        elif cleaned_response_text.startswith("```"): cleaned_response_text = cleaned_response_text.strip("```").strip()
        # Dùng re.search đã import
        match = re.search(r'\{.*\}', cleaned_response_text, re.DOTALL); json_string_to_parse = match.group(0) if match else cleaned_response_text

        try:
            parsed_result = json.loads(json_string_to_parse)
            analysis_result["severity"] = parsed_result.get("severity", "WARNING").upper()
            analysis_result["summary"] = parsed_result.get("summary", "Không có tóm tắt từ model.")
            logging.info(f"Parsed local model response: {analysis_result}")

        except json.JSONDecodeError as json_err:
            logging.warning(f"Failed to decode local model response as JSON: {json_err}. Raw: {response_text}")
            severity = "WARNING"
            if "CRITICAL" in response_text.upper(): severity = "CRITICAL"
            elif "ERROR" in response_text.upper(): severity = "ERROR"
            analysis_result = {"severity": severity, "summary": f"Model trả về không phải JSON: {response_text[:200]}"}

        return jsonify(analysis_result), 200

    except Exception as e:
        logging.error(f"Error during local model inference: {e}", exc_info=True)
        return jsonify({"error": f"Inference error: {e}"}), 500

if __name__ == '__main__':
    # Chạy Flask dev server để test, production nên dùng Gunicorn
    app.run(host='0.0.0.0', port=5000, debug=False)
