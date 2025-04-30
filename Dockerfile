# Sử dụng base image Python chính thức
FROM python:3.10-slim

# Thiết lập thư mục làm việc trong container
WORKDIR /agent

# Cài đặt các gói cần thiết cho timezone data
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Sao chép file requirements trước
COPY app/requirements.txt .

# Cài đặt các thư viện Python
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

# Sao chép code ứng dụng vào thư mục làm việc
COPY ./app /agent/app

# Tạo thư mục /data và cấp quyền cho user không phải root
USER root
RUN mkdir /data && chown 1001:1001 /data
USER 1001 # Chuyển lại user 1001

# Chỉ định lệnh sẽ chạy khi container khởi động
CMD ["python", "app/main.py"]
