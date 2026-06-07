# Smart Water Tank Monitoring & Control

Hệ thống Giám sát và Điều khiển Bồn Nước Thông Minh

Đây là Web UI Dashboard cho đồ án IoT Water Tank, tập trung vào giao diện web, Flask backend, SQLite, mock data và MQTT gateway để sau này kết nối Raspberry Pi/Arduino.

## 1. Mục tiêu

Dashboard mô phỏng hệ thống giám sát và điều khiển bồn nước:

- Đo mực nước bằng cảm biến siêu âm HC-SR04.
- Mô phỏng phao nổi/cảm biến áp suất bằng biến trở hoặc LDR analog.
- Điều khiển bơm bằng servo/LED trạng thái bơm.
- Điều khiển van bằng servo mock.
- Báo động tràn/cạn bằng buzzer + LED đỏ.
- Lưu lịch sử vào SQLite.
- Hiển thị biểu đồ real-time bằng Chart.js.
- Publish command qua MQTT topic `tank/control` để Raspberry Pi bridge gửi xuống Arduino sau này.

## 2. Mapping thực tế và prototype

| Ứng dụng thực tế | Lab Prototype |
|---|---|
| Cảm biến mực nước siêu âm cho bể nước/tháp nước | HC-SR04, Trig D4 / Echo D5, tính % từ khoảng cách |
| Phao nổi / cảm biến áp suất backup | Biến trở / LDR phao giả lập, Analog A0 |
| Motor bơm điện 220V | Servo Motor SG90, PWM Pin 9, 0° tắt / 90° bơm giả lập |
| Relay + Contactor điều khiển bơm | LED xanh trạng thái bơm, Digital Pin 8 |
| Còi báo động công nghiệp | Buzzer Pin 7 + LED đỏ Pin 6 |
| Màn hình HMI công nghiệp | Web Dashboard + LCD 1602/LED 4-digit sau này |

Prototype kiểm chứng toàn bộ logic trước khi thay bằng thiết bị công nghiệp khi triển khai thực tế.

## 3. Công nghệ sử dụng

- Python 3
- Flask
- SQLite
- HTML/CSS/JavaScript vanilla
- Chart.js CDN
- paho-mqtt
- Mosquitto MQTT broker, nếu chạy trên Raspberry Pi

Không dùng React, Vue, Angular, Bootstrap hoặc Tailwind.

## 4. Cấu trúc project

```text
smart-room-dashboard-ui/
├── app.py
├── requirements.txt
├── README.md
├── USER_GUIDE.md
├── smart_tank.db              # tự sinh khi chạy app
├── templates/
│   ├── index.html
│   └── swagger.html
└── static/
    ├── style.css
    └── app.js
```

Tên thư mục có thể vẫn là `smart-room-dashboard-ui` hoặc `smart-room-capstone`, nhưng nội dung project hiện đã là Water Tank Dashboard.

## 5. Cài đặt

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 6. Chạy dashboard

Trên máy local:

```bash
python app.py
```

Mở:

```text
http://127.0.0.1:5001
```

Trên Raspberry Pi:

```bash
cd /home/pi/Desktop/group_4/smart-room-capstone
source venv/bin/activate
python app.py
```

Mở từ laptop cùng mạng:

```text
http://RASPBERRY_PI_IP:5001
```

Ví dụ:

```text
http://172.16.5.49:5001
```

## 7. Chạy bằng Thonny

1. Mở Thonny trên Raspberry Pi.
2. Open file:

```text
/home/pi/Desktop/group_4/smart-room-capstone/app.py
```

3. Bấm Run.
4. Mở dashboard:

```text
http://RASPBERRY_PI_IP:5001
```

Nếu báo port `5001` đã sử dụng:

```bash
fuser -k 5001/tcp
```

Sau đó quay lại Thonny bấm Run lại.

## 8. Các thành phần dashboard

### Header

- Project name: `Smart Water Tank Monitoring & Control`
- Subtitle: real-time IoT water tank dashboard
- Connection status
- Last updated time

### Sensor cards

- Water Level `%`
- Backup Float `%`
- Analog A0
- Ultrasonic Distance `cm`
- Valve Status `OPEN/CLOSED`
- Pump Status `ON/OFF`
- Alarm Status `ON/OFF`
- Auto Mode `ON/OFF`

### Control panel

- Pump ON/OFF
- Alarm ON/OFF
- Valve Open/Close
- Auto Mode ON/OFF

Khi bấm nút, frontend gọi Flask API, Flask cập nhật SQLite và publish MQTT command nếu MQTT đang kết nối.

### Charts

Chart.js hiển thị 4 biểu đồ:

- Water Level
- Backup Float
- Analog A0
- Ultrasonic Distance

Mặc định refresh mỗi 3 giây và giữ 20 điểm mới nhất.

### History table

Hiển thị lịch sử:

- Time
- Water Level
- Backup Float
- Analog A0
- Ultrasonic Distance
- Valve
- Pump
- Alarm
- Auto

## 9. API chính

### Dashboard

```http
GET /
```

### API docs

```http
GET /docs
```

### Current data

```http
GET /api/current
```

Ví dụ response:

```json
{
  "water_level": 72.5,
  "float_level": 73,
  "analog_value": 746,
  "distance_cm": 16,
  "valve_status": "OPEN",
  "pump_status": "ON",
  "alarm_status": "OFF",
  "auto_mode": true,
  "connection": "MQTT/Bluetooth Gateway Connected",
  "last_updated": "2026-06-07 10:30:00"
}
```

### History

```http
GET /api/history?page=1&per_page=20
```

### Chart data

```http
GET /api/chart-data
```

### Control command

```http
POST /api/control
Content-Type: application/json

{"command":"PUMP_ON"}
```

Command hỗ trợ:

- `PUMP_ON`
- `PUMP_OFF`
- `ALARM_ON`
- `ALARM_OFF`
- `VALVE_OPEN`
- `VALVE_CLOSE`

Các command cũ `LED_ON`, `BUZZER_ON`, `DOOR_OPEN` vẫn được giữ alias để tránh lỗi khi test cũ.

### Auto mode

```http
POST /api/auto
Content-Type: application/json

{"auto":true}
```

### Gửi dữ liệu sensor thật/test

```http
POST /api/sensor-data
Content-Type: application/json

{
  "water_level": 72.5,
  "float_level": 73,
  "analog_value": 746,
  "distance_cm": 16,
  "valve_status": "OPEN",
  "pump_status": "ON",
  "alarm_status": "OFF",
  "auto_mode": true
}
```

## 10. MQTT

Mặc định:

- Broker: `127.0.0.1:1883`
- Data topic: `tank/data`
- Log topic: `tank/data/log`
- Control topic: `tank/control`
- Client ID: `smart-water-tank-flask-dashboard`

Test command dashboard gửi ra:

```bash
mosquitto_sub -h 127.0.0.1 -t tank/control
```

Gửi sensor data test qua MQTT:

```bash
mosquitto_pub -h 127.0.0.1 -t tank/data -m '{"water_level":72.5,"float_level":73,"analog_value":746,"distance_cm":16,"valve_status":"OPEN","pump_status":"ON","alarm_status":"OFF","auto_mode":true}'
```

Nếu chỉ demo UI và chưa cần MQTT:

```bash
MQTT_ENABLED=false python app.py
```

## 11. Biến môi trường tùy chọn

| Biến | Mặc định | Ý nghĩa |
|---|---:|---|
| `APP_HOST` | `0.0.0.0` | Host Flask listen |
| `APP_PORT` | `5001` | Port Flask |
| `APP_DEBUG` | `False` | Debug Flask |
| `UPDATE_INTERVAL_SECONDS` | `3` | Chu kỳ refresh |
| `CHART_MAX_POINTS` | `20` | Số điểm biểu đồ |
| `HISTORY_DEFAULT_PER_PAGE` | `20` | Số dòng history mặc định |
| `DATABASE_PATH` | `smart_tank.db` | SQLite database |
| `MQTT_ENABLED` | `True` | Bật/tắt MQTT |
| `MQTT_HOST` | `127.0.0.1` | MQTT broker host |
| `MQTT_PORT` | `1883` | MQTT broker port |

## 12. Troubleshooting

### Port 5001 đã được sử dụng

```bash
fuser -k 5001/tcp
python app.py
```

### Không mở được từ laptop

- Kiểm tra laptop và Raspberry Pi cùng mạng.
- Dùng đúng IP Raspberry Pi.
- Dùng `http://`, không dùng `https://`.
- Kiểm tra Flask đang listen:

```bash
ss -ltnp | grep 5001
```

### MQTT disconnected unexpectedly

Nếu chưa cần MQTT:

```bash
MQTT_ENABLED=false python app.py
```

Nếu cần MQTT thật:

```bash
systemctl status mosquitto
ss -ltnp | grep 1883
```

## 13. Hướng phát triển tiếp theo

1. Arduino đọc HC-SR04 và biến trở/LDR phao giả lập.
2. Arduino điều khiển servo bơm/van, LED xanh, buzzer, LED đỏ.
3. Raspberry Pi nhận JSON từ Arduino qua Bluetooth/Serial.
4. Raspberry Pi publish dữ liệu lên `tank/data` hoặc gọi `/api/sensor-data`.
5. Dashboard hiển thị dữ liệu thật thay cho mock data.
