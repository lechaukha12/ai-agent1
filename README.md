# ai-agent1: Agent Giám sát Log Grafana Loki tích hợp Gemini

**Mô tả:** Agent giám sát log Grafana Loki tích hợp Gemini (ai-agent1) là một ứng dụng Python được thiết kế để chạy trên Kubernetes. Nó định kỳ truy vấn log từ Grafana Loki cho các namespace được chỉ định, sử dụng API Google Gemini để phân tích các log đáng ngờ, và gửi cảnh báo qua Telegram nếu phát hiện sự cố nghiêm trọng.

## Mục lục

* [Kiến trúc](#kiến-trúc)
* [Yêu cầu](#yêu-cầu)
* [Cài đặt](#cài-đặt)
    * [Chuẩn bị API Keys và Tokens](#chuẩn-bị-api-keys-và-tokens)
    * [Cấu hình Agent](#cấu-hình-agent)
    * [Đóng gói Docker Image](#đóng-gói-docker-image)
* [Triển khai](#triển-khai)
* [Cấu hình chi tiết](#cấu-hình-chi-tiết)
    * [ConfigMap (`k8s/configmap.yaml`)](#configmap-k8sconfigmapyaml)
    * [Secret (`k8s/secret.yaml`)](#secret-k8ssecretyaml)
* [Cách hoạt động](#cách-hoạt-động)
* [Tùy chỉnh](#tùy-chỉnh)
* [Cấu trúc mã nguồn](#cấu-trúc-mã-nguồn)

## Kiến trúc

Luồng hoạt động chính của agent như sau:

1.  **Truy vấn Loki:** Agent định kỳ (ví dụ: mỗi 60 giây) gửi truy vấn LogQL đến API của Grafana Loki để lấy các bản ghi log mới nhất từ các namespace được cấu hình (ví dụ: `kube-system`, `app-infra`).
2.  **Lọc Log:** Agent xử lý các log nhận được, lọc ra những log đạt mức độ tối thiểu được cấu hình (`MIN_LOG_LEVEL_FOR_GEMINI`, ví dụ: `INFO` hoặc `WARNING`) hoặc chứa các từ khóa lỗi phổ biến.
3.  **Phân tích với Gemini:** Nếu có log cần phân tích, agent sẽ nhóm chúng theo namespace và gửi từng lô log đến Google Gemini API với một prompt yêu cầu xác định mức độ nghiêm trọng (INFO, WARNING, ERROR, CRITICAL) và cung cấp tóm tắt bằng tiếng Việt nếu mức độ là ERROR hoặc CRITICAL.
4.  **Gửi cảnh báo Telegram:** Nếu kết quả phân tích từ Gemini có mức độ nghiêm trọng nằm trong danh sách cấu hình (`ALERT_SEVERITY_LEVELS`, ví dụ: `ERROR,CRITICAL`), agent sẽ định dạng một tin nhắn cảnh báo chi tiết (bao gồm mức độ, tóm tắt tiếng Việt, khoảng thời gian, log mẫu) và gửi đến chat ID Telegram đã cấu hình thông qua Telegram Bot API.
5.  **Lặp lại:** Agent ngủ trong khoảng thời gian còn lại của chu kỳ truy vấn (`QUERY_INTERVAL_SECONDS`) và lặp lại quy trình.

[Sơ đồ kiến trúc đơn giản]
```mermaid
graph LR
    A[Grafana Loki] -- LogQL Query --> B(ai-agent1 Pod);
    B -- Filter Logs --> C{Logs >= Min Level?};
    C -- Yes --> D(Gemini API);
    C -- No --> E[Sleep];
    D -- Analysis Result (Severity, Summary) --> B;
    B -- Severity in Alert Levels? --> F(Telegram API);
    F -- Send Alert --> G((Telegram User/Group));
    B -- No Alert Needed --> E;
    E --> A;


Yêu cầu
Cluster Kubernetes đang hoạt động.

Grafana Loki và Promtail (hoặc log collector tương thích) đã được cài đặt và đang thu thập log từ các pod trong cluster.

kubectl được cài đặt và cấu hình để truy cập cluster.

Docker (hoặc công cụ build container tương thích) để đóng gói image.

Tài khoản Docker Hub hoặc container registry khác.

Python 3.8+ và pip cho môi trường phát triển (tùy chọn, nếu muốn chạy local).

Cài đặt
Chuẩn bị API Keys và Tokens

URL của Loki Service: Xác định URL nội bộ mà agent có thể dùng để truy cập Loki API trong cluster (ví dụ: http://loki-read.monitoring.svc.cluster.local:3100).

Google AI (Gemini) API Key: Tạo API Key từ Google AI Studio hoặc Google Cloud Console (Vertex AI).

Telegram Bot Token & Chat ID:

Nói chuyện với BotFather trên Telegram để tạo bot mới và lấy Bot Token.

Lấy Chat ID của bạn hoặc group muốn nhận cảnh báo (ví dụ: dùng @userinfobot).

Cấu hình Agent

Chỉnh sửa các file cấu hình trong thư mục k8s/:

k8s/secret.yaml:

Mã hóa các giá trị API Key và Token bằng Base64:

echo -n 'YOUR_GEMINI_API_KEY' | base64
echo -n 'YOUR_TELEGRAM_BOT_TOKEN' | base64
echo -n 'YOUR_TELEGRAM_CHAT_ID' | base64


Dán các chuỗi đã mã hóa vào các trường tương ứng (GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) trong file secret.yaml.

k8s/configmap.yaml:

Cập nhật LOKI_URL với URL Loki thực tế của bạn.

Thiết lập K8S_NAMESPACES với danh sách các namespace cần giám sát, cách nhau bởi dấu phẩy (ví dụ: "kube-system,app-infra").

Điều chỉnh MIN_LOG_LEVEL_FOR_GEMINI (mức log tối thiểu để phân tích, ví dụ: "INFO", "WARNING").

Điều chỉnh ALERT_SEVERITY_LEVELS (các mức độ sẽ gửi cảnh báo, ví dụ: "ERROR,CRITICAL" hoặc "WARNING,ERROR,CRITICAL").

(Tùy chọn) Điều chỉnh các tham số khác như QUERY_INTERVAL_SECONDS, LOKI_QUERY_RANGE_MINUTES.

Đóng gói Docker Image

Build Image: Sử dụng Docker Buildx để build image cho kiến trúc phù hợp với các node Kubernetes của bạn (thường là linux/amd64). Thay your-dockerhub-username/ai-agent1:vX.X bằng tên image và tag mong muốn.

# Build cho amd64 (phổ biến nhất)
docker buildx build --platform linux/amd64 -t your-dockerhub-username/ai-agent1:v1.0 --push .

# Hoặc build multi-platform
# docker buildx build --platform linux/amd64,linux/arm64 -t your-dockerhub-username/ai-agent1:v1.0 --push .


Cập nhật Deployment: Chỉnh sửa file k8s/deployment.yaml, thay thế giá trị spec.template.spec.containers[0].image bằng tên image và tag bạn vừa build.

Triển khai
Sử dụng kubectl để áp dụng các file cấu hình và deployment vào namespace mong muốn (ví dụ: monitoring):

# Áp dụng Secret (chứa thông tin nhạy cảm)
kubectl apply -f k8s/secret.yaml -n monitoring

# Áp dụng ConfigMap (chứa cấu hình)
kubectl apply -f k8s/configmap.yaml -n monitoring

# Áp dụng Deployment (chạy agent)
kubectl apply -f k8s/deployment.yaml -n monitoring

# (Tùy chọn) Áp dụng NetworkPolicy nếu cần
# kubectl apply -f k8s/networkpolicy.yaml -n monitoring


Kiểm tra trạng thái pod:

kubectl get pods -n monitoring -l app=ai-agent1


Xem log của agent:

kubectl logs -n monitoring -l app=ai-agent1 -f


Cấu hình chi tiết
ConfigMap (k8s/configmap.yaml)

LOKI_URL: (Bắt buộc) URL của Loki API endpoint.

QUERY_INTERVAL_SECONDS: (Tùy chọn, mặc định: 60) Khoảng thời gian giữa các lần truy vấn Loki (tính bằng giây).

LOKI_QUERY_RANGE_MINUTES: (Tùy chọn, mặc định: 5) Khoảng thời gian log cần lấy trong mỗi lần truy vấn (tính bằng phút).

LOKI_QUERY_LIMIT: (Tùy chọn, mặc định: 1000) Giới hạn số dòng log tối đa lấy về từ Loki mỗi lần query.

K8S_NAMESPACES: (Bắt buộc, mặc định: "kube-system") Danh sách các namespace cần giám sát, cách nhau bởi dấu phẩy.

MIN_LOG_LEVEL_FOR_GEMINI: (Tùy chọn, mặc định: "INFO") Mức log tối thiểu (DEBUG, INFO, WARNING, ERROR, CRITICAL, ...) mà agent sẽ xem xét để gửi cho Gemini phân tích.

ALERT_SEVERITY_LEVELS: (Tùy chọn, mặc định: "ERROR,CRITICAL") Danh sách các mức độ nghiêm trọng (do Gemini trả về) sẽ kích hoạt cảnh báo Telegram, cách nhau bởi dấu phẩy.

GEMINI_MODEL_NAME: (Tùy chọn, mặc định: "gemini-1.5-flash") Tên model Gemini sử dụng.

Secret (k8s/secret.yaml)

GEMINI_API_KEY: (Bắt buộc) API Key của Google AI/Vertex AI (đã mã hóa Base64).

TELEGRAM_BOT_TOKEN: (Bắt buộc) Token của Telegram Bot (đã mã hóa Base64).

TELEGRAM_CHAT_ID: (Bắt buộc) Chat ID của người dùng hoặc group Telegram nhận cảnh báo (đã mã hóa Base64).

Cách hoạt động
Sau khi triển khai, agent sẽ chạy như một Deployment trong Kubernetes. Nó liên tục thực hiện các chu kỳ giám sát: lấy log từ Loki, lọc, phân tích bằng Gemini (nếu cần), và gửi cảnh báo Telegram nếu phát hiện sự cố nghiêm trọng theo cấu hình.

Bạn sẽ nhận được thông báo trên Telegram với định dạng tương tự như sau khi có lỗi nghiêm trọng:

🚨 *Cảnh báo Log K8s (Namespace: kube-system)* 🚨
*Mức độ:* ERROR
*Tóm tắt:* [Tóm tắt vấn đề bằng tiếng Việt do Gemini cung cấp]
*Khoảng thời gian:* 2025-04-25 10:30:00 - 2025-04-25 10:31:00 UTC
*Log mẫu:*
- [Dòng log mẫu 1]
- [Dòng log mẫu 2]
- [Dòng log mẫu 3]

_Vui lòng kiểm tra log trên Loki để biết thêm chi tiết._


Tùy chỉnh
Thay đổi Namespace/Mức độ log/Cảnh báo: Chỉnh sửa các giá trị trong k8s/configmap.yaml và áp dụng lại ConfigMap, sau đó khởi động lại pod agent (kubectl rollout restart deployment ai-agent1 -n monitoring).

Thay đổi Prompt Gemini: Chỉnh sửa biến prompt trong hàm analyze_with_gemini của file app/main.py, sau đó build lại image và cập nhật Deployment.

Thêm Logic Lọc: Chỉnh sửa hàm preprocess_and_filter trong app/main.py để thêm các quy tắc lọc log phức tạp hơn trước khi gửi đến Gemini.

Cấu trúc mã nguồn
app/main.py: Mã nguồn chính của agent Python.
app/requirements.txt: Danh sách các thư viện Python cần thiết.

Dockerfile: File định nghĩa cách build Docker image cho agent.

k8s/: Thư mục chứa các file manifest Kubernetes.

configmap.yaml: Định nghĩa cấu hình cho agent.

secret.yaml: Định nghĩa các thông tin nhạy cảm (API keys, tokens).

deployment.yaml: Định nghĩa cách triển khai agent lên

