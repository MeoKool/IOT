# USER GUIDE - Smart Water Tank Dashboard

Hướng dẫn sử dụng dashboard:

```text
Smart Water Tank Monitoring & Control
Hệ thống Giám sát và Điều khiển Bồn Nước Thông Minh
```

## 1. Mở dashboard

Trên Raspberry Pi chạy:

```bash
cd /home/pi/Desktop/group_4/smart-room-capstone
source venv/bin/activate
python app.py
```

Mở trình duyệt trên laptop cùng mạng:

```text
http://RASPBERRY_PI_IP:5001
```

Ví dụ:

```text
http://172.16.5.49:5001
```

Nếu chạy trên chính máy local:

```text
http://127.0.0.1:5001
```

## 2. Chạy bằng Thonny

1. Mở Thonny.
2. Mở file:

```text
/home/pi/Desktop/group_4/smart-room-capstone/app.py
```

3. Bấm Run.
4. Mở dashboard bằng địa chỉ Raspberry Pi:

```text
http://RASPBERRY_PI_IP:5001
```

Nếu Thonny báo:

```text
Port 5001 is in use
Address already in use
```

Mở Terminal và chạy:

```bash
fuser -k 5001/tcp
```

Sau đó quay lại Thonny bấm Run lại.

## 3. Dashboard dùng để làm gì?

Dashboard này mô phỏng hệ thống bồn nước thông minh:

- Theo dõi mực nước hiện tại.
- Có cảm biến backup để xác nhận mực nước.
- Hiển thị khoảng cách HC-SR04 đo từ cảm biến tới mặt nước.
- Hiển thị giá trị analog A0 của phao giả lập.
- Điều khiển bơm, van, còi báo động và chế độ tự động.
- Lưu lịch sử vào SQLite.
- Hiển thị biểu đồ real-time.
- Chuẩn bị MQTT để sau này kết nối Arduino/Raspberry Pi.

## 4. Ý nghĩa các sensor cards

| Card | Ý nghĩa |
|---|---|
| Water Level | Phần trăm mực nước trong bồn |
| Backup Float | Giá trị xác nhận từ phao/cảm biến áp suất giả lập |
| Analog A0 | Giá trị analog từ biến trở/LDR phao giả lập |
| Ultrasonic Distance | Khoảng cách HC-SR04 đo được, đơn vị cm |
| Valve Status | Trạng thái van/servo: OPEN hoặc CLOSED |
| Pump Status | Trạng thái bơm: ON hoặc OFF |
| Alarm Status | Trạng thái còi/LED đỏ: ON hoặc OFF |
| Auto Mode | Chế độ tự động: ON hoặc OFF |

Dashboard tự refresh mỗi 3 giây.

## 5. Ý nghĩa control panel

Các nút/toggle điều khiển:

| Nút | Chức năng |
|---|---|
| Pump | Bật/tắt bơm |
| Alarm | Bật/tắt còi báo động |
| Valve | Mở/đóng van servo |
| Auto Mode | Bật/tắt chế độ tự động |

Khi bấm điều khiển:

1. Trình duyệt gửi request tới Flask.
2. Flask cập nhật trạng thái trong SQLite.
3. Nếu MQTT kết nối, Flask publish command lên topic `tank/control`.
4. Dashboard hiện toast thông báo command đã gửi.
5. UI cập nhật trạng thái mới.

## 6. Biểu đồ

Dashboard có 4 biểu đồ:

- Water Level `%`
- Backup Float `%`
- Analog A0
- Ultrasonic Distance `cm`

Mỗi biểu đồ lấy dữ liệu từ API:

```text
/api/chart-data
```

Mặc định giữ 20 điểm mới nhất.

## 7. History table

Bảng lịch sử hiển thị:

- Time
- Water Level
- Backup Float
- Analog A0
- Ultrasonic Distance
- Valve
- Pump
- Alarm
- Auto

Có phân trang và chọn số dòng: 10, 20, 50, 100.

## 8. System status

Panel hệ thống hiển thị:

- Communication method: Bluetooth + MQTT
- Bluetooth port: `/dev/rfcomm0`
- MQTT broker
- MQTT status
- Data topic: `tank/data`
- Control topic: `tank/control`
- Database: SQLite
- Backend: Flask
- Update interval: 3 seconds
- System mode: Manual/Auto

## 9. Dữ liệu thật

Dashboard không tự sinh dữ liệu nữa. Dữ liệu chỉ xuất hiện khi Raspberry Pi/Arduino bridge, MQTT, Swagger hoặc curl gửi vào endpoint:

```text
POST /api/sensor-data
```

Khi chưa có dữ liệu, dashboard sẽ hiển thị `--` và trạng thái chờ dữ liệu thật.

## 10. Test API bằng Swagger

Mở:

```text
http://RASPBERRY_PI_IP:5001/docs
```

Swagger dùng để test:

- `/api/current`
- `/api/history`
- `/api/chart-data`
- `/api/control`
- `/api/auto`
- `/api/sensor-data`
- `/api/mqtt/status`

## 11. Test gửi dữ liệu bồn nước

Có thể gửi dữ liệu test bằng curl:

```bash
curl -X POST http://127.0.0.1:5001/api/sensor-data \
  -H 'Content-Type: application/json' \
  -d '{
    "water_level": 72.5,
    "float_level": 73,
    "analog_value": 746,
    "distance_cm": 16,
    "valve_status": "OPEN",
    "pump_status": "ON",
    "alarm_status": "OFF",
    "auto_mode": true
  }'
```

Sau đó dashboard sẽ hiển thị dữ liệu vừa gửi.

## 12. Test control API

Bật bơm:

```bash
curl -X POST http://127.0.0.1:5001/api/control \
  -H 'Content-Type: application/json' \
  -d '{"command":"PUMP_ON"}'
```

Tắt bơm:

```bash
curl -X POST http://127.0.0.1:5001/api/control \
  -H 'Content-Type: application/json' \
  -d '{"command":"PUMP_OFF"}'
```

Mở van:

```bash
curl -X POST http://127.0.0.1:5001/api/control \
  -H 'Content-Type: application/json' \
  -d '{"command":"VALVE_OPEN"}'
```

Đóng van:

```bash
curl -X POST http://127.0.0.1:5001/api/control \
  -H 'Content-Type: application/json' \
  -d '{"command":"VALVE_CLOSE"}'
```

Bật báo động:

```bash
curl -X POST http://127.0.0.1:5001/api/control \
  -H 'Content-Type: application/json' \
  -d '{"command":"ALARM_ON"}'
```

## 13. MQTT test

Xem command dashboard gửi:

```bash
mosquitto_sub -h 127.0.0.1 -t tank/control
```

Sau đó bấm Pump/Valve/Alarm trên dashboard.

Gửi dữ liệu sensor qua MQTT:

```bash
mosquitto_pub -h 127.0.0.1 -t tank/data -m '{"water_level":72.5,"float_level":73,"analog_value":746,"distance_cm":16,"valve_status":"OPEN","pump_status":"ON","alarm_status":"OFF","auto_mode":true}'
```

Nếu chỉ demo UI, có thể tắt MQTT:

```bash
MQTT_ENABLED=false python app.py
```

## 14. Kịch bản demo gợi ý

1. Mở dashboard.
2. Giới thiệu đề tài: giám sát và điều khiển bồn nước thông minh.
3. Chỉ vào Water Level và Ultrasonic Distance.
4. Giải thích HC-SR04 đo khoảng cách tới mặt nước, từ đó tính % mực nước.
5. Chỉ vào Backup Float và Analog A0.
6. Giải thích biến trở/LDR mô phỏng phao nổi/cảm biến áp suất backup.
7. Bấm Pump ON/OFF.
8. Bấm Valve Open/Close.
9. Bấm Alarm ON/OFF.
10. Bật Auto Mode.
11. Chỉ vào biểu đồ và history table.
12. Nếu có MQTT, mở `mosquitto_sub` để cho thấy command được publish.

## 15. Lỗi thường gặp

### Port 5001 đã sử dụng

```bash
fuser -k 5001/tcp
python app.py
```

### Không mở được dashboard từ laptop

Kiểm tra IP Raspberry Pi:

```bash
hostname -I
```

Kiểm tra Flask có mở port 5001 không:

```bash
ss -ltnp | grep 5001
```

Mở đúng dạng:

```text
http://RASPBERRY_PI_IP:5001
```

Không dùng `https://`.

### Không có dữ liệu mới

Kiểm tra Arduino/bridge có đang gửi dữ liệu vào Flask không:

```bash
python serial_api_bridge.py
```

Hoặc gửi một record test vào `/api/sensor-data`.

### MQTT báo disconnected

Nếu chưa cần MQTT:

```bash
MQTT_ENABLED=false python app.py
```

Nếu cần MQTT:

```bash
systemctl status mosquitto
ss -ltnp | grep 1883
```

## 16. Dừng server

Nếu chạy trong Terminal:

```text
Ctrl + C
```

Nếu process vẫn giữ port:

```bash
fuser -k 5001/tcp
```
