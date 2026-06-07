# Smart Room Monitoring & Control

Hệ thống Giám sát và Điều khiển Không gian Thông minh

Đây là Web UI Dashboard cho đồ án IoT Smart Room. Giai đoạn hiện tại tập trung vào giao diện web, Flask backend, mock sensor data, SQLite history và MQTT gateway chuẩn bị cho tích hợp Raspberry Pi/Arduino sau này.

## 1. Mục tiêu dự án

Dashboard dùng để hiển thị và điều khiển một phòng thông minh theo thời gian gần thực:

- Theo dõi nhiệt độ, độ ẩm, ánh sáng, khoảng cách.
- Hiển thị trạng thái cửa, LED, buzzer và chế độ Auto/Manual.
- Điều khiển LED, buzzer, cửa và Auto Mode qua nút bấm trên dashboard.
- Lưu lịch sử dữ liệu cảm biến vào SQLite.
- Vẽ biểu đồ bằng Chart.js.
- Chuẩn bị endpoint để sau này Raspberry Pi nhận dữ liệu từ Arduino qua Bluetooth/Serial/MQTT.

Hiện tại chưa cần code Arduino và chưa cần Bluetooth/Serial thật.

## 2. Công nghệ sử dụng

- Python 3
- Flask
- SQLite
- HTML
- CSS
- JavaScript vanilla
- Chart.js CDN
- paho-mqtt
- Mosquitto MQTT broker trên Raspberry Pi, nếu dùng MQTT thật

Không dùng React, Vue, Angular, Bootstrap hoặc Tailwind.

## 3. Cấu trúc thư mục

```text
smart-room-dashboard-ui/
├── app.py
├── requirements.txt
├── README.md
├── USER_GUIDE.md
├── smart_room.db              # tự sinh sau khi chạy app
├── templates/
│   ├── index.html
│   └── swagger.html
└── static/
    ├── style.css
    └── app.js
```

## 4. Tính năng chính

### Dashboard UI

- Header có tên dự án, subtitle, trạng thái kết nối và thời gian cập nhật.
- Sensor cards:
  - Temperature
  - Humidity
  - Light Level
  - Distance
  - Door Status
  - LED Status
  - Buzzer Status
  - Auto Mode
- Control panel:
  - LED ON/OFF
  - Buzzer ON/OFF
  - Door Open/Close
  - Auto Mode ON/OFF
- Biểu đồ Chart.js:
  - Temperature
  - Humidity
  - Light Level
  - Distance
- Bảng lịch sử 20 bản ghi mới nhất.
- System status panel.
- Responsive layout cho laptop, tablet và điện thoại.

### Backend Flask

- Mock data khi chưa có phần cứng.
- SQLite để lưu sensor history và trạng thái hệ thống.
- API cho dashboard frontend.
- API nhận sensor data thật sau này.
- MQTT publish command để Raspberry Pi bridge gửi lệnh xuống Arduino sau này.

## 5. Cài đặt và chạy trên máy tính/Raspberry Pi

### Bước 1: vào thư mục project

```bash
cd smart-room-dashboard-ui
```

Hoặc trên Raspberry Pi nếu dùng thư mục hiện tại:

```bash
cd /home/pi/Desktop/group_4/smart-room-capstone
```

### Bước 2: tạo virtual environment

```bash
python3 -m venv venv
```

### Bước 3: kích hoạt virtual environment

```bash
source venv/bin/activate
```

### Bước 4: cài thư viện

```bash
pip install -r requirements.txt
```

### Bước 5: chạy Flask

```bash
python app.py
```

Mặc định app chạy ở port `5001` và host `0.0.0.0`.

Mở trên chính Raspberry Pi:

```text
http://127.0.0.1:5001
```

Mở từ máy tính cùng mạng LAN:

```text
http://RASPBERRY_PI_IP:5001
```

Ví dụ:

```text
http://172.16.5.49:5001
```

## 6. Chạy bằng Thonny trên Raspberry Pi

Có thể chạy bằng Thonny, nhưng Terminal ổn định hơn cho Flask LAN demo.

Nếu muốn chạy bằng Thonny:

1. Mở Thonny.
2. Open file:

```text
/home/pi/Desktop/group_4/smart-room-capstone/app.py
```

3. Bấm Run.
4. Mở trình duyệt với URL Raspberry Pi in ra, ví dụ:

```text
http://172.16.5.49:5001
```

Nếu Thonny báo `Address already in use` hoặc `Port 5001 is in use`, nghĩa là Flask cũ vẫn đang chạy. Dừng process cũ bằng Terminal:

```bash
fuser -k 5001/tcp
```

Sau đó quay lại Thonny và bấm Run lại.

Nếu muốn đổi port tạm thời:

```bash
APP_PORT=5002 python app.py
```

Sau đó mở:

```text
http://RASPBERRY_PI_IP:5002
```

## 7. API endpoints

### Dashboard page

```http
GET /
```

Render giao diện dashboard.

### Swagger API docs

```http
GET /docs
```

Mở giao diện Swagger UI để xem/test API.

### OpenAPI JSON

```http
GET /api/openapi.json
```

Trả về OpenAPI spec.

### Current sensor data

```http
GET /api/current
```

Trả về dữ liệu cảm biến hiện tại. Nếu mock mode bật, endpoint này sinh dữ liệu mock và lưu vào SQLite.

Ví dụ response:

```json
{
  "temperature": 28.5,
  "humidity": 70,
  "light": 420,
  "distance": 18,
  "door_status": "OPEN",
  "led_status": "ON",
  "buzzer_status": "OFF",
  "auto_mode": true,
  "connection": "Bluetooth Connected",
  "last_updated": "2026-06-07 10:30:00"
}
```

### History

```http
GET /api/history?page=1&per_page=20
```

Trả về lịch sử sensor records từ SQLite.

### Chart data

```http
GET /api/chart-data
```

Trả về dữ liệu time-series cho Chart.js:

- labels
- temperature
- humidity
- light
- distance

### Control command

```http
POST /api/control
Content-Type: application/json

{"command":"LED_ON"}
```

Các command hỗ trợ:

- `LED_ON`
- `LED_OFF`
- `BUZZER_ON`
- `BUZZER_OFF`
- `DOOR_OPEN`
- `DOOR_CLOSE`

Ví dụ response:

```json
{
  "success": true,
  "message": "Command sent: LED_ON"
}
```

### Auto mode

```http
POST /api/auto
Content-Type: application/json

{"auto":true}
```

Hoặc:

```json
{"auto":false}
```

### Mock mode

Kiểm tra mock mode:

```http
GET /api/mock-mode
```

Bật/tắt mock mode:

```http
POST /api/mock-mode
Content-Type: application/json

{"mock_mode": true}
```

Khi mock mode bật, `/api/current` sẽ tự sinh dữ liệu demo.

Khi mock mode tắt, dashboard dùng dữ liệu thật đã được lưu qua `/api/sensor-data` hoặc MQTT.

### Gửi sensor data thật/test data

```http
POST /api/sensor-data
Content-Type: application/json

{
  "temperature": 28.5,
  "humidity": 70,
  "light": 420,
  "distance": 18,
  "door_status": "OPEN",
  "led_status": "ON",
  "buzzer_status": "OFF",
  "auto_mode": true
}
```

Endpoint này dùng để test hoặc để Raspberry Pi/Bluetooth/Serial bridge gửi dữ liệu thật sau này.

### Clear data

```http
POST /api/clear-data
Content-Type: application/json

{"reset_state": false}
```

Xóa sensor history trong SQLite.

Nếu `reset_state=true`, xóa history và reset trạng thái hệ thống.

### MQTT status

```http
GET /api/mqtt/status
```

Trả về trạng thái MQTT broker, topic data/log/control và trạng thái kết nối.

## 8. MQTT integration

Mặc định app kết nối MQTT broker local trên Raspberry Pi:

- Host: `127.0.0.1`
- Port: `1883`
- Data topic: `room/data`
- Log topic: `room/data/log`
- Control topic: `room/control`
- Client ID: `smart-room-flask-dashboard`

Control button trên dashboard sẽ gọi Flask API, sau đó Flask publish JSON command lên topic `room/control` nếu MQTT đang kết nối.

Sensor data thật có thể publish lên:

```text
room/data
```

hoặc:

```text
room/data/log
```

Ví dụ test publish trên Raspberry Pi:

```bash
mosquitto_pub -h 127.0.0.1 -t room/data -m '{"temperature":28.5,"humidity":70,"light":420,"distance":18,"door_status":"OPEN","led_status":"ON","buzzer_status":"OFF","auto_mode":true}'
```

Xem command dashboard gửi ra:

```bash
mosquitto_sub -h 127.0.0.1 -t room/control
```

Nếu chưa cần MQTT, có thể tắt tạm:

```bash
MQTT_ENABLED=false python app.py
```

## 9. Biến môi trường tùy chọn

Không bắt buộc dùng `.env`. Có thể override bằng biến môi trường khi chạy.

| Biến | Mặc định | Ý nghĩa |
|---|---:|---|
| `APP_HOST` | `0.0.0.0` | Host Flask listen |
| `APP_PORT` | `5001` | Port Flask |
| `PORT` | `5001` | Port dự phòng nếu không set `APP_PORT` |
| `APP_DEBUG` | `False` | Bật/tắt debug Flask |
| `UPDATE_INTERVAL_SECONDS` | `3` | Chu kỳ refresh dashboard |
| `CHART_MAX_POINTS` | `20` | Số điểm mới nhất trên chart |
| `HISTORY_DEFAULT_PER_PAGE` | `20` | Số record mặc định trong bảng history |
| `DATABASE_PATH` | `smart_room.db` | Đường dẫn SQLite DB |
| `MQTT_ENABLED` | `True` | Bật/tắt MQTT |
| `MQTT_HOST` | `127.0.0.1` | MQTT broker host |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_TOPIC_DATA` | `room/data` | Topic nhận sensor data |
| `MQTT_TOPIC_LOG` | `room/data/log` | Topic nhận sensor log |
| `MQTT_TOPIC_CONTROL` | `room/control` | Topic gửi command |
| `MQTT_CLIENT_ID` | `smart-room-flask-dashboard` | MQTT client id |

Ví dụ chạy port khác:

```bash
APP_PORT=5002 python app.py
```

Ví dụ tắt MQTT:

```bash
MQTT_ENABLED=false python app.py
```

## 10. Troubleshooting

### Lỗi: Port 5001 is already in use

Nguyên nhân: Flask process cũ vẫn đang chạy.

Kiểm tra process:

```bash
fuser -v 5001/tcp
```

Kill process đang dùng port:

```bash
fuser -k 5001/tcp
```

Chạy lại:

```bash
python app.py
```

### Mở từ máy tính không được

Kiểm tra các điểm sau:

1. Raspberry Pi và máy tính phải cùng mạng.
2. Dùng đúng IP Raspberry Pi.
3. Dùng `http://`, không dùng `https://`.
4. Flask phải listen `0.0.0.0`, không chỉ `127.0.0.1`.
5. Kiểm tra port đang mở:

```bash
ss -ltnp | grep 5001
```

URL đúng thường là:

```text
http://RASPBERRY_PI_IP:5001
```

### MQTT disconnected unexpectedly

Nếu log có dòng:

```text
MQTT disconnected unexpectedly
```

Kiểm tra Mosquitto:

```bash
systemctl status mosquitto
```

Kiểm tra MQTT port:

```bash
ss -ltnp | grep 1883
```

Nếu chưa cần MQTT trong lúc demo UI, chạy tạm:

```bash
MQTT_ENABLED=false python app.py
```

### Không thấy dữ liệu mới

- Kiểm tra mock mode đang bật hay tắt tại `/api/mock-mode`.
- Nếu mock mode tắt mà chưa gửi dữ liệu thật, `/api/current` có thể không có dữ liệu.
- Bật lại mock mode:

```bash
curl -X POST http://127.0.0.1:5001/api/mock-mode \
  -H 'Content-Type: application/json' \
  -d '{"mock_mode": true}'
```

## 11. Hướng phát triển tiếp theo

Sau khi UI ổn định, có thể làm tiếp theo thứ tự:

1. Arduino đọc DHT22, LDR, HC-SR04.
2. Arduino điều khiển LED, buzzer, servo.
3. Arduino gửi JSON qua Bluetooth.
4. Raspberry Pi đọc Bluetooth/Serial.
5. Raspberry Pi ghi dữ liệu vào SQLite hoặc publish MQTT.
6. Dashboard dùng dữ liệu thật thay cho mock data.

## 12. Ghi chú cho báo cáo/demo

Tên đề tài:

```text
Smart Room Monitoring & Control
Hệ thống Giám sát và Điều khiển Không gian Thông minh
```

Dashboard hiện tại đã có đủ phần trình diễn Web UI:

- Real-time sensor cards
- Control panel
- Chart.js line charts
- History table
- System status
- SQLite storage
- Mock API
- MQTT gateway chuẩn bị cho phần cứng
