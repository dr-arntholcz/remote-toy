import pygame
import json
import asyncio
import websockets
import threading
import queue
import sys
import binascii
from io import BytesIO
from PIL import Image, UnidentifiedImageError
import time

# === Глобальные переменные ===
trim = 0
joystick_data_queue = queue.Queue()  # Для джойстика
stop_event = threading.Event()

# Для изображений
current_image_surface = None
image_lock = threading.Lock()

# Цвета
BLACK = pygame.Color('black')
WHITE = pygame.Color('white')

class TextPrint(object):
    def __init__(self):
        self.reset()
        self.font = pygame.font.Font(None, 20)

    def tprint(self, screen, textString):
        textBitmap = self.font.render(textString, True, BLACK)
        screen.blit(textBitmap, (self.x, self.y))
        self.y += self.line_height

    def reset(self):
        self.x = 10
        self.y = 10
        self.line_height = 15

    def indent(self):
        self.x += 10

    def unindent(self):
        self.x -= 10

# === Функции джойстика ===
def joystick_thread_func(data_queue, stop_evt):
    global trim
    pygame.init()
    pygame.joystick.init()

    if pygame.joystick.get_count() == 0:
        print("❌ Джойстик не найден в потоке джойстика!")
        pygame.quit()
        return

    joy = pygame.joystick.Joystick(0)
    joy.init()
    print(f"✅ Джойстик инициализирован: {joy.get_name()}")

    while not stop_evt.is_set():
        for event in pygame.event.get():
            if event.type == pygame.JOYBUTTONDOWN:
                if event.button == 10:
                    trim -= 1
                elif event.button == 11:
                    trim += 1

        try:
            throttle = round(joy.get_axis(3) * 255, 2) - 1
            brake = round((joy.get_axis(5) + 1) * 127, 2)
            direction = round(joy.get_axis(0) * 255, 2) - 1 + trim
            l = joy.get_button(4)

            data = {
                "led_state": l,
                "moved": throttle,
                "turn": direction,
                "brake": brake,
                "trim": trim
            }
            if not data_queue.full():
                data_queue.put(data)
        except Exception as e:
            print(f"Ошибка при чтении джойстика: {e}")

    pygame.quit()

async def handler_joystick(websocket):
    print("🌐 Клиент джойстика подключен")
    try:
        while not stop_event.is_set():
            try:
                if not joystick_data_queue.empty():
                    data = joystick_data_queue.get_nowait()
                    await websocket.send(json.dumps(data))
            except queue.Empty:
                pass

            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=0.1)
                print(f"Получено от клиента джойстика: {message}")
            except asyncio.TimeoutError:
                continue
            except websockets.exceptions.ConnectionClosed:
                print("🌐 Клиент джойстика отключился")
                break
    except Exception as e:
        print(f"Ошибка в обработчике джойстика: {e}")
    finally:
        print("Обработчик джойстика завершён")

def is_valid_image(image_bytes):
    try:
        Image.open(BytesIO(image_bytes))
        return True
    except UnidentifiedImageError:
        print("❌ Изображение повреждено")
        return False

async def handle_image_connection(websocket):
    print("📷 Клиент изображения подключен")
    try:
        async for message in websocket:
            if isinstance(message, bytes) and len(message) > 5000:
                if is_valid_image(message):
                    # Преобразуем изображение в pygame.Surface
                    pil_image = Image.open(BytesIO(message))
                    pil_image = pil_image.convert("RGB")  # Убедиться, что формат RGB
                    mode = pil_image.mode
                    size = pil_image.size
                    data = pil_image.tobytes()

                    img_surface = pygame.image.fromstring(data, size, mode)
                    
                    # Масштабируем изображение под размер окна (например, 480x360)
                    img_surface = pygame.transform.scale(img_surface, (480, 360))

                    global current_image_surface
                    with image_lock:
                        current_image_surface = img_surface
    except websockets.exceptions.ConnectionClosed:
        print("📷 Клиент изображения отключён")

def run_pygame_loop():
    global trim, current_image_surface
    pygame.init()
    screen = pygame.display.set_mode((500, 700))  # Размер окна
    pygame.display.set_caption("🎮 Joystick Controller + Camera Feed")
    clock = pygame.time.Clock()
    textPrint = TextPrint()

    running = True
    last_joystick_data = {"led_state": 0, "moved": 0, "turn": 0, "brake": 0, "trim": trim}

    print("🎨 Pygame запущен")

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                print("🚪 Окно закрыто пользователем")
                running = False
                stop_event.set()
                return

        try:
            while not joystick_data_queue.empty():
                last_joystick_data = joystick_data_queue.get_nowait()
        except queue.Empty:
            pass

        # Отрисовка
        screen.fill(WHITE)

        # Если есть изображение, рисуем его вверху
        with image_lock:
            img_surf = current_image_surface

        if img_surf:
            screen.blit(img_surf, (10, 10))  # Рисуем изображение в позиции (10,10)

        # Рисуем текстовые данные под изображением
        textPrint.reset()
        # Смещаем текст ниже изображения
        textPrint.y = 370  # Примерно под изображением 480x360
        textPrint.tprint(screen, "🎮 Joystick Data:")
        textPrint.tprint(screen, f"Trim: {last_joystick_data.get('trim', trim)}")
        textPrint.tprint(screen, f"Moved: {last_joystick_data.get('moved', 0)}")
        textPrint.tprint(screen, f"Turn: {last_joystick_data.get('turn', 0)}")
        textPrint.tprint(screen, f"Brake: {last_joystick_data.get('brake', 0)}")
        textPrint.tprint(screen, f"LED: {last_joystick_data.get('led_state', 0)}")

        pygame.display.flip()
        clock.tick(300)

    pygame.quit()
    print("🎨 Pygame завершён")

# === Основная асинхронная часть ===
async def start_servers():
    print("🚀 Запуск серверов...")

    # Джойстик
    joystick_server = await websockets.serve(handler_joystick, "", 8025)
    print("🌐 WebSocket джойстика запущен на порту 8025")

    # Приём изображений
    image_server = await websockets.serve(handle_image_connection, "", 8026)
    print("📷 WebSocket изображений запущен на порту 8026")

    # Ждём, пока не будет установлен stop_event
    while not stop_event.is_set():
        await asyncio.sleep(0.1)

    print("🛑 Остановка серверов...")
    image_server.close()
    joystick_server.close()
    await image_server.wait_closed()
    await joystick_server.wait_closed()
    print("✅ Серверы остановлены")

def run_main():
    print("🚀 Запуск объединённой программы...")

    # Запускаем джойстик в отдельном потоке
    joystick_thread = threading.Thread(target=joystick_thread_func, args=(joystick_data_queue, stop_event), daemon=True)
    joystick_thread.start()

    # Запускаем pygame в отдельном потоке
    pygame_thread = threading.Thread(target=run_pygame_loop, daemon=True)
    pygame_thread.start()

    # Запускаем асинхронные серверы в основном потоке
    try:
        asyncio.run(start_servers())
    except KeyboardInterrupt:
        print("\n🛑 Программа прервана пользователем")
    finally:
        stop_event.set()
        print("✅ Программа завершена")

if __name__ == "__main__":
    run_main()
