# Sử dụng base image Python chính thức
FROM python:3.10-slim

# Thiết lập thư mục làm việc trong container
WORKDIR /agent

# Cài đặt các gói cần thiết cho timezone data (trên Debian/Ubuntu slim)
# Quan trọng để thư viện zoneinfo hoạt động đúng
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

# Sao chép file requirements trước để tận dụng Docker cache
COPY app/requirements.txt .

# Cài đặt các thư viện Python
# --prefer-binary để ưu tiên cài wheel nếu có, nhanh hơn
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

# Sao chép toàn bộ code của ứng dụng vào thư mục làm việc
COPY ./app /agent/app

# Tạo thư mục /data và cấp quyền cho user không phải root (ví dụ: user 1001)
# Phải khớp với runAsUser/fsGroup trong deployment.yaml
RUN mkdir /data && chown 1001:1001 /data

# Chỉ định user không phải root để chạy ứng dụng
USER 1001

# Chỉ định lệnh sẽ chạy khi container khởi động
CMD ["python", "app/main.py"]
