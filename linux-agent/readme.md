Hướng dẫn Tạo Môi trường và Build Linux Agent

Các bước này giả định bạn đang ở trong thư mục gốc chứa các file code của Linux Agent (main.py, config_manager.py, linux_monitor.py, log_reader.py, requirements.txt).

1. Tạo và Kích hoạt Môi trường ảo (Virtual Environment)

Môi trường ảo giúp cô lập các thư viện Python cho dự án này, tránh xung đột với các dự án khác hoặc thư viện hệ thống.

Tạo môi trường ảo:

Mở terminal hoặc command prompt trong thư mục gốc của dự án.

Chạy lệnh sau (thay venv bằng tên bạn muốn nếu cần):

python3 -m venv venv

(Lưu ý: Sử dụng python thay vì python3 nếu đó là lệnh mặc định cho Python 3 trên hệ thống của bạn)

Kích hoạt môi trường ảo:

Trên Linux/macOS:

source venv/bin/activate

Trên Windows (cmd.exe):

venv\Scripts\activate.bat

Trên Windows (PowerShell):

.\venv\Scripts\Activate.ps1

(Nếu gặp lỗi về execution policy trên PowerShell, bạn có thể cần chạy: Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process rồi thử lại lệnh activate)

Sau khi kích hoạt, bạn sẽ thấy tên môi trường ảo (ví dụ: (venv)) xuất hiện ở đầu dòng lệnh.

2. Cài đặt các Thư viện cần thiết

Khi môi trường ảo đã được kích hoạt, cài đặt các thư viện được liệt kê trong file requirements.txt.

Chạy lệnh:

pip install -r requirements.txt

Lệnh này sẽ cài đặt aiohttp, python-dotenv, psutil, aiofiles.

3. Cài đặt PyInstaller

PyInstaller là công cụ chúng ta sẽ dùng để đóng gói code thành file binary.

Chạy lệnh:

pip install pyinstaller

4. Build file Binary obsagent

Bây giờ, bạn có thể sử dụng PyInstaller để tạo file thực thi.

Đảm bảo bạn vẫn đang ở trong thư mục gốc của dự án (nơi có main.py).

Chạy lệnh sau:

pyinstaller --onefile --name obsagent main.py

--onefile: Yêu cầu PyInstaller tạo ra một file thực thi duy nhất, bao gồm cả các thư viện phụ thuộc.

--name obsagent: Đặt tên cho file thực thi đầu ra là obsagent.

main.py: Chỉ định file Python chính để bắt đầu quá trình build.

5. Tìm file Binary đã Build

PyInstaller sẽ tạo một số thư mục (build, dist) và file (.spec).

File thực thi cuối cùng bạn cần sẽ nằm trong thư mục dist. Tìm file có tên obsagent (không có phần mở rộng .exe trên Linux/macOS) trong thư mục dist.

Kết quả:

Bây giờ bạn đã có file dist/obsagent. Bạn có thể copy file này đến bất kỳ máy chủ Linux nào (có kiến trúc tương thích) và chạy nó bằng các tham số dòng lệnh như đã thảo luận:

./dist/obsagent --obsengine="http://<your-obsengine-url>" --agent-id="<your-agent-id>" --env-name="<your-env-name>"

Hoặc bạn có thể copy file này vào /usr/local/bin/ và cấu hình systemd service để chạy nó.

