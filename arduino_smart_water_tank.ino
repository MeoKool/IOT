#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <DHT.h>
#include <Servo.h>

// =====================================================
// SMART WATER TANK - ARDUINO UNO
// Arduino -> Raspberry Pi: USB Serial CSV
// Raspberry Pi -> Arduino: Serial command '1' / '0'
//
// CSV format sent every 2 seconds:
// temperature,humidity,water_percent,is_pumping,water_raw
// Example:
// 28.5,70.2,45,1,460
// =====================================================

// ================= PIN CONFIG =================
#define WATER_PIN A0
#define DHT_PIN 2
#define DHT_TYPE DHT22

#define PUMP_LED_PIN 3
#define BUZZER_PIN 4
#define SERVO_PIN 5
#define ALARM_LED_PIN 6

// ================= WATER SENSOR CALIBRATION =================
// Adjust these values after testing your real water sensor.
// Read water_raw from Serial/LCD when tank is empty/full.
const int MIN_WATER_RAW = 350;   // 0% water
const int MAX_WATER_RAW = 580;   // 100% water

// ================= THRESHOLDS =================
const int LOW_WATER_THRESHOLD = 10;       // Auto pump ON below 10%
const int STOP_PUMP_THRESHOLD = 85;       // Auto pump OFF at/above 85%
const int OVERFLOW_THRESHOLD = 90;        // Alarm above 90%

// ================= TIMING =================
const unsigned long WATER_READ_INTERVAL = 100;    // 0.1 second
const unsigned long DATA_SEND_INTERVAL = 2000;    // 2 seconds
const unsigned long SERVO_INTERVAL = 30;          // servo animation speed
const unsigned long ALARM_INTERVAL = 500;         // alarm blink speed

unsigned long lastWaterReadMillis = 0;
unsigned long lastDataSendMillis = 0;
unsigned long lastServoMillis = 0;
unsigned long lastAlarmMillis = 0;

// ================= DEVICES =================
DHT dht(DHT_PIN, DHT_TYPE);
LiquidCrystal_I2C lcd(0x27, 16, 2);
Servo pumpServo;

// ================= STATE =================
bool isPumping = false;
bool alarmState = false;

int waterRaw = 0;
int waterPercent = 0;

int servoPosition = 0;
int servoStep = 15;

// =====================================================
// Read command from Raspberry Pi
// '1' = pump ON
// '0' = pump OFF
// =====================================================
void handleSerialCommand() {
  while (Serial.available() > 0) {
    char command = Serial.read();

    if (command == '1') {
      if (waterPercent < STOP_PUMP_THRESHOLD) {
        isPumping = true;
      }
    } else if (command == '0') {
      isPumping = false;
    }
  }
}

// =====================================================
// Convert analog water sensor value to percent
// =====================================================
int readWaterPercent() {
  waterRaw = analogRead(WATER_PIN);

  long percent = map(waterRaw, MIN_WATER_RAW, MAX_WATER_RAW, 0, 100);
  percent = constrain(percent, 0, 100);

  return (int)percent;
}

// =====================================================
// Automatic pump safety logic
// =====================================================
void updatePumpAutoLogic() {
  if (waterPercent < LOW_WATER_THRESHOLD) {
    isPumping = true;
  }

  if (waterPercent >= STOP_PUMP_THRESHOLD) {
    isPumping = false;
  }
}

// =====================================================
// Update LCD display
// =====================================================
void updateLcd(float temperature, float humidity, bool dhtOk) {
  lcd.setCursor(0, 0);

  if (dhtOk) {
    lcd.print("T:");
    lcd.print(temperature, 1);
    lcd.print("C H:");
    lcd.print(humidity, 0);
    lcd.print("%   ");
  } else {
    lcd.print("DHT22 Error     ");
  }

  lcd.setCursor(0, 1);
  lcd.print("W:");
  lcd.print(waterPercent);
  lcd.print("% P:");
  lcd.print(isPumping ? "ON " : "OFF");
  lcd.print(" R:");
  lcd.print(waterRaw);
  lcd.print("   ");
}

// =====================================================
// Send clean CSV line to Raspberry Pi bridge
// Do not print debug text to Serial, because bridge parses CSV.
// =====================================================
void sendDataToRaspberryPi(float temperature, float humidity) {
  Serial.print(temperature, 1);
  Serial.print(",");
  Serial.print(humidity, 1);
  Serial.print(",");
  Serial.print(waterPercent);
  Serial.print(",");
  Serial.print(isPumping ? 1 : 0);
  Serial.print(",");
  Serial.println(waterRaw);
}

// =====================================================
// Pump output: LED + servo animation
// =====================================================
void updatePumpOutput(unsigned long currentMillis) {
  if (isPumping) {
    digitalWrite(PUMP_LED_PIN, HIGH);

    if (currentMillis - lastServoMillis >= SERVO_INTERVAL) {
      lastServoMillis = currentMillis;

      servoPosition += servoStep;

      if (servoPosition >= 180) {
        servoPosition = 180;
        servoStep = -servoStep;
      } else if (servoPosition <= 0) {
        servoPosition = 0;
        servoStep = -servoStep;
      }

      pumpServo.write(servoPosition);
    }
  } else {
    digitalWrite(PUMP_LED_PIN, LOW);
    servoPosition = 0;
    servoStep = abs(servoStep);
    pumpServo.write(0);
  }
}

// =====================================================
// Overflow alarm: buzzer + LED blink
// =====================================================
void updateAlarmOutput(unsigned long currentMillis) {
  if (waterPercent > OVERFLOW_THRESHOLD) {
    if (currentMillis - lastAlarmMillis >= ALARM_INTERVAL) {
      lastAlarmMillis = currentMillis;
      alarmState = !alarmState;

      digitalWrite(ALARM_LED_PIN, alarmState ? HIGH : LOW);
      digitalWrite(BUZZER_PIN, alarmState ? HIGH : LOW);
    }
  } else {
    alarmState = false;
    digitalWrite(ALARM_LED_PIN, LOW);
    digitalWrite(BUZZER_PIN, LOW);
  }
}

void setup() {
  Serial.begin(9600);

  pinMode(WATER_PIN, INPUT);
  pinMode(PUMP_LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(ALARM_LED_PIN, OUTPUT);

  digitalWrite(PUMP_LED_PIN, LOW);
  digitalWrite(BUZZER_PIN, LOW);
  digitalWrite(ALARM_LED_PIN, LOW);

  dht.begin();

  lcd.init();
  lcd.backlight();
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Smart WaterTank");
  lcd.setCursor(0, 1);
  lcd.print("Starting...");

  pumpServo.attach(SERVO_PIN);
  pumpServo.write(0);

  delay(2000);
  lcd.clear();
}

void loop() {
  unsigned long currentMillis = millis();

  // 1. Receive manual command from Raspberry Pi/dashboard
  handleSerialCommand();

  // 2. Read water sensor quickly
  if (currentMillis - lastWaterReadMillis >= WATER_READ_INTERVAL) {
    lastWaterReadMillis = currentMillis;
    waterPercent = readWaterPercent();
    updatePumpAutoLogic();
  }

  // 3. Read DHT22 and send data every 2 seconds
  if (currentMillis - lastDataSendMillis >= DATA_SEND_INTERVAL) {
    lastDataSendMillis = currentMillis;

    float temperature = dht.readTemperature();
    float humidity = dht.readHumidity();
    bool dhtOk = !isnan(temperature) && !isnan(humidity);

    if (dhtOk) {
      sendDataToRaspberryPi(temperature, humidity);
    }

    updateLcd(temperature, humidity, dhtOk);
  }

  // 4. Update pump and alarm outputs continuously
  updatePumpOutput(currentMillis);
  updateAlarmOutput(currentMillis);
}
