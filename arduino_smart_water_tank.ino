#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include "DHT.h"
#include <Servo.h>

// ================= CẤU HÌNH CHÂN =================
#define WATER_PIN A0
#define DHTPIN 2
#define DHTTYPE DHT22
#define PUMP_LED 3
#define BUZZER 4
#define SERVO_PIN 5
#define ALARM_LED 6

// ================= HIỆU CHUẨN CẢM BIẾN =================
const int MIN_WATER_RAW = 350;  // 0%
const int MAX_WATER_RAW = 580;  // 100%

DHT dht(DHTPIN, DHTTYPE);
LiquidCrystal_I2C lcd(0x27, 16, 2);
Servo pumpServo;

// ================= BỘ ĐỊNH TUYẾN THỜI GIAN =================
unsigned long previousWaterMillis = 0;
const unsigned long waterInterval = 100;      // đọc nước 0.1 giây/lần

unsigned long previousDhtMillis = 0;
const unsigned long dhtInterval = 2000;       // gửi dữ liệu lên Raspberry Pi 2 giây/lần

unsigned long previousServoMillis = 0;        // luồng motor servo
int servoPos = 0;
int servoStep = 15;

unsigned long previousAlarmMillis = 0;        // luồng báo động tràn
const unsigned long alarmInterval = 1000;
bool alarmState = false;

// ================= BIẾN TOÀN CỤC =================
bool isPumping = false;
int waterPercent = 0;
int waterRaw = 0;

void handleSerialCommand() {
  while (Serial.available() > 0) {
    char cmd = Serial.read();

    // Raspberry Pi bridge gửi '1' bật bơm, '0' tắt bơm.
    // Nếu sau này gửi P1/P0 thì vẫn bắt ký tự 1/0 được.
    if (cmd == '1') {
      if (waterPercent < 85) {
        isPumping = true;
      }
    } else if (cmd == '0') {
      isPumping = false;
    }
  }
}

void setup() {
  Serial.begin(9600);
  pinMode(WATER_PIN, INPUT);
  dht.begin();

  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0);
  lcd.print("Smart WaterTank");
  lcd.setCursor(0, 1);
  lcd.print("Starting...");

  pinMode(PUMP_LED, OUTPUT);
  pinMode(BUZZER, OUTPUT);
  pinMode(ALARM_LED, OUTPUT);

  digitalWrite(PUMP_LED, LOW);
  digitalWrite(BUZZER, LOW);
  digitalWrite(ALARM_LED, LOW);

  pumpServo.attach(SERVO_PIN);
  pumpServo.write(0);

  delay(2000);
  lcd.clear();
}

void loop() {
  unsigned long currentMillis = millis();

  // 1. NHẬN LỆNH TỪ RASPBERRY PI / WEB DASHBOARD
  handleSerialCommand();

  // 2. LUỒNG 0.1 GIÂY: ĐỌC NƯỚC & ĐIỀU KHIỂN BƠM
  if (currentMillis - previousWaterMillis >= waterInterval) {
    previousWaterMillis = currentMillis;

    waterRaw = analogRead(WATER_PIN);
    waterPercent = map(waterRaw, MIN_WATER_RAW, MAX_WATER_RAW, 0, 100);
    waterPercent = constrain(waterPercent, 0, 100);

    // Auto chống cạn/chống tràn
    if (waterPercent < 10 && !isPumping) {
      isPumping = true;
    }
    if (waterPercent >= 85 && isPumping) {
      isPumping = false;
    }

    lcd.setCursor(0, 1);
    lcd.print("Water:");
    lcd.print(waterPercent);
    lcd.print("%    ");
  }

  // 3. LUỒNG 2 GIÂY: ĐỌC DHT22 & GỬI CSV LÊN RASPBERRY PI
  if (currentMillis - previousDhtMillis >= dhtInterval) {
    previousDhtMillis = currentMillis;

    float t = dht.readTemperature();
    float h = dht.readHumidity();

    if (!isnan(t) && !isnan(h)) {
      // Format bridge đang đọc:
      // temperature,humidity,water_percent,is_pumping,water_raw
      Serial.print(t, 1); Serial.print(",");
      Serial.print(h, 1); Serial.print(",");
      Serial.print(waterPercent); Serial.print(",");
      Serial.print(isPumping ? 1 : 0); Serial.print(",");
      Serial.println(waterRaw);

      lcd.setCursor(0, 0);
      lcd.print("T:"); lcd.print(t, 1); lcd.print("C H:"); lcd.print(h, 0); lcd.print("% ");
    }
  }

  // 4. LUỒNG THỰC THI: MÁY BƠM (SERVO + LED)
  if (isPumping) {
    digitalWrite(PUMP_LED, HIGH);
    if (currentMillis - previousServoMillis > 30) {
      previousServoMillis = currentMillis;
      servoPos += servoStep;
      if (servoPos >= 180 || servoPos <= 0) {
        servoStep = -servoStep;
      }
      pumpServo.write(servoPos);
    }
  } else {
    digitalWrite(PUMP_LED, LOW);
  }

  // 5. LUỒNG BÁO ĐỘNG TRÀN (> 90%)
  if (waterPercent > 90) {
    if (currentMillis - previousAlarmMillis >= alarmInterval) {
      previousAlarmMillis = currentMillis;
      alarmState = !alarmState;
      digitalWrite(ALARM_LED, alarmState ? HIGH : LOW);
      digitalWrite(BUZZER, alarmState ? HIGH : LOW);
    }
  } else {
    digitalWrite(ALARM_LED, LOW);
    digitalWrite(BUZZER, LOW);
    alarmState = false;
  }
}
