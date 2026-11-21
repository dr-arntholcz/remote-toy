#include "esp_camera.h"
#include <WiFi.h>
#include <WebSocketsClient.h>
#include "esp_timer.h"
#include "img_converters.h"
#include "fb_gfx.h"
#include "soc/soc.h" //disable brownout problems
#include "soc/rtc_cntl_reg.h" //disable brownout problems
#include "driver/gpio.h"
#include <ArduinoJson.h>

int led_state = 0, moved = 0, turn = 0, brake = 0;

// configuration for AI Thinker Camera board
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

const char* ssid     = "example_your_wi-fi_name"; // CHANGE HERE
const char* password = ""; // CHANGE HERE

const char* websockets_server_host = "example.com"; //CHANGE HERE
const uint16_t websockets_server_port_m = 8025; // Управление
const uint16_t websockets_server_port_v = 8026; // Видео

camera_fb_t * fb = NULL;

WebSocketsClient client_m; // Управление
WebSocketsClient client_v; // Видео

esp_err_t init_camera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_VGA;
  config.jpeg_quality = 15;
  config.fb_count = 2;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("camera init FAIL: 0x%x", err);
    return err;
  }

  sensor_t * s = esp_camera_sensor_get();
  s->set_framesize(s, FRAMESIZE_VGA);
  Serial.println("camera init OK");
  return ESP_OK;
}

void handleEvents_m(WStype_t type, uint8_t * payload, size_t length) {
  switch(type) {
    case WStype_DISCONNECTED:
      Serial.println("[WS_M] Отключён");
      break;
    case WStype_CONNECTED:
      Serial.println("[WS_M] Подключён к серверу управления");
      client_m.sendTXT("Подключен к серверу управления");
      break;
    case WStype_TEXT:
      Serial.printf("[WS_M] Получен текст: %s\n", payload);

      StaticJsonDocument<200> doc;
      DeserializationError error = deserializeJson(doc, (char*)payload);
      if (error) {
        Serial.print("deserializeJson() failed: ");
        Serial.println(error.c_str());
        return;
      }

      led_state = doc["led_state"];
      moved = doc["moved"];
      turn = doc["turn"];
      brake = doc["brake"];

      if(led_state == 1) {
        digitalWrite(2, !digitalRead(2));
      }

      if(moved > 0) {
        analogWrite(12, moved + brake);
        analogWrite(13, 0 + brake);
      } else if(moved < 0) {
        analogWrite(12, 0 + brake);
        analogWrite(13, (moved * -1) + brake);
      } else {
        analogWrite(12, 0);
        analogWrite(13, 0);
      }

      if(turn > 0) {
        digitalWrite(14, HIGH);
        digitalWrite(15, LOW);
      } else if(turn < 0) {
        digitalWrite(14, LOW);
        digitalWrite(15, HIGH);
      } else {
        digitalWrite(14, LOW);
        digitalWrite(15, LOW);
      }
      break;
  }
}

void handleEvents_v(WStype_t type, uint8_t * payload, size_t length) {
  switch(type) {
    case WStype_DISCONNECTED:
      Serial.println("[WS_V] Отключён");
      break;
    case WStype_CONNECTED:
      Serial.println("[WS_V] Подключён к видеосерверу");
      client_v.sendTXT("Подключен к видеосерверу");
      break;
    case WStype_TEXT:
      Serial.printf("[WS_V] Получен текст: %s\n", payload);
      break;
  }
}

void setup() {
  pinMode(12, OUTPUT);
  pinMode(13, OUTPUT);
  pinMode(15, OUTPUT);
  pinMode(14, OUTPUT);
  pinMode(2, OUTPUT);

  digitalWrite(12, 0);
  digitalWrite(14, 0);

  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0); // отключить brownout

  Serial.begin(115200);

  init_camera();

  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println(" Подключено к Wi-Fi");

  // Подключаемся к серверу управления
  client_m.begin(websockets_server_host, websockets_server_port_m, "/");
  client_m.onEvent(handleEvents_m);

  // Подключаемся к видеосерверу
  client_v.begin(websockets_server_host, websockets_server_port_v, "/");
  client_v.onEvent(handleEvents_v);

  Serial.println("Клиенты WebSocket инициализированы");
}

void loop() {
  client_m.loop();
  client_v.loop();

  // Захват изображения и отправка по видеоканалу
  fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("Ошибка получения кадра камеры");
    return;
  }

  client_v.sendBIN((const uint8_t*)fb->buf, fb->len);
  Serial.println("Кадр отправлен");

  esp_camera_fb_return(fb);

//  delay(100); // Управляет частотой кадров
}
