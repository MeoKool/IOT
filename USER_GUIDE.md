# USER GUIDE - Smart Room Monitoring & Control Dashboard

Hướng dẫn sử dụng Web Dashboard cho đồ án:

```text
Smart Room Monitoring & Control
Hệ thống Giám sát và Điều khiển Không gian Thông minh
```

Tài liệu này dành cho người dùng/demo, tập trung vào cách mở dashboard, xem dữ liệu, bấm điều khiển và xử lý lỗi thường gặp.

## 1. Mở dashboard

### Nếu chạy trên Raspberry Pi

Trên Raspberry Pi, mở Terminal và chạy:

```bash
cd /home/pi/Desktop/group_4/smart-room-capstone
source venv/bin/activate
python app.py
```

Sau đó mở trình duyệt trên máy tính cùng mạng:

```text
http://RASPBERRY_PI_IP:5001
```

Ví dụ:

```text
http://172.16.5.49:5001
```

Lưu ý: phải dùng `http://`, không dùng `https://`.

### Nếu chạy trực tiếp trên máy đang code

```bash
cd smart-room-dashboard-ui
source venv/bin/activate
python app.py
```

Mở:

```text
http://127.0.0.1:5001
```

## 2. Chạy bằng Thonny

Nếu dùng Thonny trên Raspberry Pi:

1. Mở Thonny.
2. Chọn `File > Open`.
3. Mở file:

```text
/home/pi/Desktop/group_4/smart-room-capstone/app.py
```

4. Bấm nút Run.
5. Mở dashboard bằng URL:

```text
http://RASPBERRY_PI_IP:5001
```

Nếu Thonny báo port đã được sử dụng:

```text
Port 5001 is in use
Address already in use
```

Mở Terminal trên Raspberry Pi và chạy:

```bash
fuser -k 5001/tcp
```

Sau đó quay lại Thonny và bấm Run lại.

## 3. Các khu vực trên dashboard

Dashboard gồm các phần chính:

1. Header
2. Real-time sensor cards
3. Control panel
4. Charts
5. History table
6. System status panel
7. MQTT/API status, nếu được bật trên giao diện

## 4. Header

Header hiển thị:

- Tên dự án: `Smart Room Monitoring & Control`
- Subtitle: `Real-time IoT Room Dashboard`
- Trạng thái kết nối
- Thời gian cập nhật cuối cùng

Ý nghĩa trạng thái kết nối:

- `Bluetooth Connected`: mô phỏng trạng thái kết nối tốt hoặc đã có dữ liệu.
- `Bluetooth Disconnected`: mô phỏng mất kết nối hoặc chưa có nguồn dữ liệu thật.
- MQTT status, nếu có, cho biết Flask có kết nối được MQTT broker hay không.

## 5. Real-time sensor cards

Các card hiển thị dữ liệu hiện tại:

| Card | Ý nghĩa |
|---|---|
| Temperature | Nhiệt độ phòng, đơn vị °C |
| Humidity | Độ ẩm phòng, đơn vị % |
| Light Level | Mức ánh sáng từ cảm biến LDR |
| Distance | Khoảng cách từ cảm biến siêu âm, đơn vị cm |
| Door Status | Trạng thái cửa: OPEN/CLOSED |
| LED Status | Trạng thái đèn LED: ON/OFF |
| Buzzer Status | Trạng thái còi: ON/OFF |
| Auto Mode | Chế độ tự động: ON/OFF |

Dashboard tự refresh dữ liệu mỗi 3 giây.

## 6. Control panel

Khu vực Control panel có các nút:

- `LED ON`
- `LED OFF`
- `Buzzer ON`
- `Buzzer OFF`
- `Door Open`
- `Door Close`
- `Auto Mode ON`
- `Auto Mode OFF`

Khi bấm nút:

1. Trình duyệt gửi request tới Flask API.
2. Flask cập nhật trạng thái trong SQLite.
3. Nếu MQTT đang kết nối, Flask publish command lên topic `room/control`.
4. Dashboard hiện toast thông báo lệnh đã gửi.
5. UI cập nhật trạng thái mới.

Ví dụ khi bấm `LED ON`, hệ thống gửi command:

```text
LED_ON
```

Và hiển thị thông báo:

```text
Command sent: LED_ON
```

## 7. Charts

Dashboard có biểu đồ line chart cho:

- Temperature
- Humidity
- Light Level
- Distance

Dữ liệu biểu đồ lấy từ endpoint:

```text
/api/chart-data
```

Mặc định:

- Refresh mỗi 3 giây.
- Giữ 20 điểm dữ liệu mới nhất.
- Dữ liệu được lưu trong SQLite.

## 8. History table

Bảng History hiển thị các record cảm biến mới nhất.

Các cột:

- Time
- Temperature
- Humidity
- Light
- Distance
- Door
- LED
- Buzzer
- Auto

Các trạng thái ON/OFF, OPEN/CLOSED được hiển thị bằng badge để dễ nhìn khi demo.

Nếu bảng không có dữ liệu:

- Kiểm tra app đang chạy chưa.
- Kiểm tra mock mode có bật không.
- Gọi `/api/current` hoặc chờ dashboard refresh vài giây.

## 9. System status panel

Panel này hiển thị thông tin hệ thống:

| Thông tin | Ý nghĩa |
|---|---|
| Communication method | Phương thức giao tiếp dự kiến, ví dụ Bluetooth/MQTT |
| Port | Cổng Bluetooth/Serial dự kiến, ví dụ `/dev/rfcomm0` |
| Database | SQLite |
| Backend | Flask |
| Update interval | Chu kỳ refresh, mặc định 3 giây |
| System mode | Manual hoặc Auto |

## 10. Mock mode và dữ liệu thật

### Mock mode là gì?

Mock mode là chế độ dashboard tự tạo dữ liệu cảm biến giả để demo UI khi chưa có Arduino.

Khi mock mode bật:

- Dashboard vẫn có dữ liệu mới.
- Charts và history vẫn chạy.
- Không cần Arduino.
- Không cần Bluetooth thật.

Kiểm tra mock mode:

```text
http://RASPBERRY_PI_IP:5001/api/mock-mode
```

Bật mock mode bằng Terminal:

```bash
curl -X POST http://127.0.0.1:5001/api/mock-mode \
  -H 'Content-Type: application/json' \
  -d '{"mock_mode": true}'
```

Tắt mock mode:

```bash
curl -X POST http://127.0.0.1:5001/api/mock-mode \
  -H 'Content-Type: application/json' \
  -d '{"mock_mode": false}'
```

### Gửi dữ liệu test giống dữ liệu thật

Có thể gửi dữ liệu test qua API:

```bash
curl -X POST http://127.0.0.1:5001/api/sensor-data \
  -H 'Content-Type: application/json' \
  -d '{
    "temperature": 28.5,
    "humidity": 70,
    "light": 420,
    "distance": 18,
    "door_status": "OPEN",
    "led_status": "ON",
    "buzzer_status": "OFF",
    "auto_mode": true
  }'
```

Sau khi gửi sensor data thật/test, dashboard sẽ đọc dữ liệu từ SQLite.

## 11. Swagger UI để test API

Có thể mở Swagger UI tại:

```text
http://RASPBERRY_PI_IP:5001/docs
```

Hoặc trên máy local:

```text
http://127.0.0.1:5001/docs
```

Swagger dùng để:

- Xem danh sách API.
- Test `/api/current`.
- Test `/api/control`.
- Test `/api/auto`.
- Test `/api/sensor-data`.
- Test clear data.

## 12. MQTT khi dùng Raspberry Pi

Nếu Mosquitto đang chạy trên Raspberry Pi, dashboard sẽ dùng MQTT để chuẩn bị gửi/nhận dữ liệu thật.

Thông tin mặc định:

| Mục | Giá trị |
|---|---|
| MQTT host | `127.0.0.1` |
| MQTT port | `1883` |
| Sensor data topic | `room/data` |
| Sensor log topic | `room/data/log` |
| Control topic | `room/control` |

### Kiểm tra Mosquitto

```bash
systemctl status mosquitto
```

### Xem command từ dashboard

Mở Terminal trên Raspberry Pi:

```bash
mosquitto_sub -h 127.0.0.1 -t room/control
```

Sau đó bấm nút `LED ON` trên dashboard. Terminal sẽ nhận command JSON.

### Gửi sensor data qua MQTT để test

```bash
mosquitto_pub -h 127.0.0.1 -t room/data -m '{"temperature":28.5,"humidity":70,"light":420,"distance":18,"door_status":"OPEN","led_status":"ON","buzzer_status":"OFF","auto_mode":true}'
```

Nếu chưa cần MQTT để demo UI, có thể tắt MQTT khi chạy app:

```bash
MQTT_ENABLED=false python app.py
```

## 13. Xóa dữ liệu demo/history

Xóa toàn bộ sensor history nhưng giữ trạng thái hiện tại:

```bash
curl -X POST http://127.0.0.1:5001/api/clear-data \
  -H 'Content-Type: application/json' \
  -d '{"reset_state": false}'
```

Xóa history và reset trạng thái hệ thống:

```bash
curl -X POST http://127.0.0.1:5001/api/clear-data \
  -H 'Content-Type: application/json' \
  -d '{"reset_state": true}'
```

## 14. Kịch bản demo đề xuất

Khi trình bày đồ án, có thể demo theo thứ tự:

1. Mở dashboard:

```text
http://RASPBERRY_PI_IP:5001
```

2. Giới thiệu mục tiêu:

```text
Dashboard giám sát và điều khiển phòng thông minh theo thời gian gần thực.
```

3. Chỉ vào sensor cards:

- Nhiệt độ
- Độ ẩm
- Ánh sáng
- Khoảng cách
- Cửa
- LED
- Buzzer
- Auto Mode

4. Chỉ vào biểu đồ:

```text
Dữ liệu được refresh mỗi 3 giây và giữ 20 điểm mới nhất.
```

5. Bấm control buttons:

- LED ON
- LED OFF
- Door Open
- Door Close
- Auto Mode ON
- Auto Mode OFF

6. Chỉ vào history table:

```text
Mỗi lần cập nhật, dữ liệu được lưu vào SQLite để xem lịch sử.
```

7. Nếu có MQTT:

- Mở `mosquitto_sub` topic `room/control`.
- Bấm nút trên dashboard.
- Cho người xem thấy command JSON được publish.

8. Kết luận:

```text
Hiện tại dashboard đã hoàn thành phần Web UI và backend mock. Bước tiếp theo là kết nối Arduino, Bluetooth/Serial và dữ liệu cảm biến thật.
```

## 15. Lỗi thường gặp

### Lỗi 1: Port 5001 đã được sử dụng

Thông báo thường gặp:

```text
Address already in use
Port 5001 is in use
```

Cách sửa:

```bash
fuser -k 5001/tcp
python app.py
```

Nếu dùng Thonny, chạy `fuser -k 5001/tcp` trong Terminal rồi bấm Run lại trong Thonny.

### Lỗi 2: Không mở được từ máy tính

Kiểm tra:

1. Máy tính và Raspberry Pi cùng mạng.
2. Đúng IP Raspberry Pi.
3. Đúng port `5001`.
4. Dùng `http://`, không dùng `https://`.
5. Flask đang chạy.

Kiểm tra IP Raspberry Pi:

```bash
hostname -I
```

Kiểm tra Flask có listen port 5001 không:

```bash
ss -ltnp | grep 5001
```

### Lỗi 3: Dashboard không cập nhật dữ liệu

Cách kiểm tra nhanh:

```bash
curl http://127.0.0.1:5001/api/current
```

Nếu API có dữ liệu nhưng trình duyệt không cập nhật:

- Refresh trang.
- Hard refresh.
- Mở tab ẩn danh.

Nếu API không có dữ liệu:

- Bật mock mode.
- Hoặc gửi test data vào `/api/sensor-data`.

### Lỗi 4: MQTT disconnected unexpectedly

Lỗi này liên quan MQTT broker/client, không phải lỗi UI chính.

Nếu chỉ demo dashboard UI, chạy tạm:

```bash
MQTT_ENABLED=false python app.py
```

Nếu cần MQTT thật, kiểm tra:

```bash
systemctl status mosquitto
ss -ltnp | grep 1883
```

## 16. Dừng server

Nếu chạy trong Terminal, nhấn:

```text
Ctrl + C
```

Nếu process vẫn còn chiếm port:

```bash
fuser -k 5001/tcp
```

## 17. Ghi nhớ nhanh

Lệnh chạy chuẩn:

```bash
cd /home/pi/Desktop/group_4/smart-room-capstone
source venv/bin/activate
python app.py
```

URL mở dashboard:

```text
http://RASPBERRY_PI_IP:5001
```

Kill port nếu bị chiếm:

```bash
fuser -k 5001/tcp
```

Mở API docs:

```text
http://RASPBERRY_PI_IP:5001/docs
```
