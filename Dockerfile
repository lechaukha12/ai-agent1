FROM python:3.10-slim

# Thiết lập thư mục làm việc trong container
WORKDIR /agent

# Sao chép file requirements trước để tận dụng Docker cache
COPY app/requirements.txt .

# Cài đặt các thư viện Python
RUN pip install --no-cache-dir -r requirements.txt

# Sao chép toàn bộ code của ứng dụng vào thư mục làm việc
COPY ./app /agent/app

# Chỉ định lệnh sẽ chạy khi container khởi động
CMD ["python", "app/main.py"]
