import os
import logging
from flask import Flask, request, jsonify
import re # Import thư viện regex
import json # Import thư viện json
try:
    # Thay đổi import model phù hợp
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    import torch
except ImportError:
    logging.error("Transformers or PyTorch not installed.")
    exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# --- Load Model ---
MODEL_PATH = os.environ.get("MODEL_PATH", "/app/local_model")
DEVICE = "cpu"
model = None
tokenizer = None
# Định nghĩa các nhãn (labels)
LABELS = ["INFO", "WARNING", "ERROR", "CRITICAL"] # Thứ tự này quan trọng
ID2LABEL = {i: label for i, label in enumerate(LABELS)}
LABEL2ID = {label: i for i, label in enumerate(LABELS)}

try:
    logging.info(f"Loading local classification model from: {MODEL_PATH}")
    if not os.path.isdir(MODEL_PATH):
         raise FileNotFoundError(f"Model directory not found at {MODEL_PATH}")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
    # Thêm ignore_mismatched_sizes=True khi load model
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_PATH,
        num_labels=len(LABELS),
        id2label=ID2LABEL,
        label2id=LABEL2ID,
        ignore_mismatched_sizes=True # Bỏ qua lỗi không khớp kích thước lớp cuối
    )
    model.to(DEVICE)
    model.eval()
    logging.info(f"Local classification model loaded successfully onto device: {DEVICE}")
except Exception as e:
    logging.error(f"Failed to load model: {e}", exc_info=True)

@app.route('/healthz')
def healthz():
    if model and tokenizer:
        return "OK", 200
    else:
        return "Model not loaded", 503

@app.route('/generate', methods=['POST']) # Giữ tên endpoint cho agent không cần đổi
def classify_severity(): # Đổi tên hàm cho rõ nghĩa
    """Endpoint nhận prompt (log+context) và trả về mức độ nghiêm trọng."""
    global model, tokenizer

    if not model or not tokenizer:
        logging.error("Model or tokenizer not loaded.")
        return jsonify({"error": "Model not ready"}), 503

    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    prompt_content = data.get('prompt') # Vẫn nhận 'prompt' từ agent

    if not prompt_content:
        return jsonify({"error": "Missing 'prompt' in JSON body"}), 400

    logging.info(f"Received content for classification (length: {len(prompt_content)}): {prompt_content[:200]}...")

    # Mặc định là INFO, summary là null
    analysis_result = {"severity": "INFO", "summary": None}

    try:
        # Tokenize input cho sequence classification
        inputs = tokenizer(prompt_content, return_tensors="pt", max_length=512, truncation=True, padding=True).to(DEVICE)

        # Chạy inference
        with torch.no_grad():
            outputs = model(**inputs)
            logits = outputs.logits

        # Tìm nhãn có điểm cao nhất
        predicted_class_id = logits.argmax().item()
        # Kiểm tra ID có hợp lệ không trước khi truy cập id2label
        if predicted_class_id in model.config.id2label:
             predicted_severity = model.config.id2label[predicted_class_id]
        else:
             logging.warning(f"Predicted class ID {predicted_class_id} not found in model config. Defaulting to WARNING.")
             predicted_severity = "WARNING" # Fallback nếu ID không hợp lệ


        analysis_result["severity"] = predicted_severity.upper()
        # Giữ summary là null hoặc câu cố định
        analysis_result["summary"] = f"Model phân loại là {predicted_severity}." # Hoặc để null

        logging.info(f"Classification result: {analysis_result}")

        return jsonify(analysis_result), 200

    except Exception as e:
        logging.error(f"Error during local model classification: {e}", exc_info=True)
        analysis_result = {"severity": "ERROR", "summary": f"Lỗi khi phân loại: {e}"}
        return jsonify(analysis_result), 500

if __name__ == '__main__':
    # Chạy bằng Gunicorn trong Dockerfile, đây chỉ để test local nếu cần
    app.run(host='0.0.0.0', port=5000, debug=False)
