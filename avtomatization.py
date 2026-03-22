import os
import json
import time
import random
import hashlib
import requests
import re
import schedule
import subprocess
import boto3
from botocore.exceptions import NoCredentialsError
from dotenv import load_dotenv

# Загружаем переменные из файла .env
load_dotenv()

# ==================== КОНФИГУРАЦИЯ ИЗ .ENV ====================

required_keys = ['SUPABASE_URL', 'SUPABASE_KEY', 'CF_R2_ENDPOINT', 'CF_R2_ACCESS_KEY', 'CF_R2_SECRET_KEY']
for key in required_keys:
    if not os.getenv(key):
        raise ValueError(f"🚨 КРИТИЧЕСКАЯ ОШИБКА: Ключ {key} не найден в файле .env!")

# Supabase
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
SUPABASE_TABLE = os.getenv('SUPABASE_TABLE', 'mothers_day')
SUPABASE_HEADERS = {
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "apikey": SUPABASE_KEY
}

# Cloudflare R2
CF_R2_ENDPOINT = os.getenv('CF_R2_ENDPOINT')
CF_R2_ACCESS_KEY = os.getenv('CF_R2_ACCESS_KEY')
CF_R2_SECRET_KEY = os.getenv('CF_R2_SECRET_KEY')
CF_R2_BUCKET_NAME = os.getenv('CF_R2_BUCKET_NAME', 'pinterest-videos')
CF_R2_PUBLIC_URL = os.getenv('CF_R2_PUBLIC_URL')

# Локальные сервисы
OLLAMA_URL = os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434/api/chat')
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'qwen2.5:3b-instruct-q4_K_M')
BROWSERLESS_URL = os.getenv('BROWSERLESS_URL', 'http://127.0.0.1:3000/screenshot?token=super_secret_key_12345')

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def transliterate(text):
    char_map = {
        'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'E', 'Ж': 'Zh', 'З': 'Z', 'И': 'I', 'Й': 'Y',
        'К': 'K', 'Л': 'L', 'М': 'M', 'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U', 'Ф': 'F',
        'Х': 'Kh', 'Ц': 'Ts', 'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Shch', 'Ъ': '', 'Ы': 'Y', 'Ь': '', 'Э': 'E', 'Ю': 'Yu', 'Я': 'Ya',
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y',
        'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f',
        'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
    }
    for cyrillic, latin in char_map.items():
        text = text.replace(cyrillic, latin)
    return text

def create_slug(title):
    transliterated = transliterate(title)
    truncated = re.split(r'[\.\?\!\,\;]', transliterated)[0]
    slug = truncated.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s-]+', '-', slug)
    return slug.strip('-')

def generate_hash(src):
    ts = str(int(time.time() * 1000))
    return hashlib.md5((src + ts).encode()).hexdigest()[:8]

def upload_to_r2(file_path, object_name):
    """Загружает файл в Cloudflare R2 и возвращает публичную ссылку"""
    s3 = boto3.client('s3',
                      endpoint_url=CF_R2_ENDPOINT,
                      aws_access_key_id=CF_R2_ACCESS_KEY,
                      aws_secret_access_key=CF_R2_SECRET_KEY,
                      region_name='auto')
    try:
        s3.upload_file(file_path, CF_R2_BUCKET_NAME, object_name, ExtraArgs={'ContentType': 'video/mp4'})
        public_url = f"{CF_R2_PUBLIC_URL}/{object_name}"
        print(f"✅ Видео загружено в Cloudflare R2: {public_url}")
        return public_url
    except Exception as e:
        print(f"❌ Ошибка загрузки в R2: {e}")
        return None

def create_zoom_video(input_png_path, output_mp4_path):
    """Превращает PNG в 5-секундное видео с эффектом зума через FFmpeg"""
    print("🎬 Создаем видео-анимацию через FFmpeg...")
    animations = [
        "zoompan=z='min(zoom+0.0015,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=150:s=768x1344:fps=30",
        "zoompan=z=1.15:x='min(x+2, iw/zoom)':y='ih/2-(ih/zoom/2)':d=150:s=768x1344:fps=30"
    ]
    anim = random.choice(animations)
    
    command = [
        'ffmpeg', '-loop', '1', '-framerate', '30', '-i', input_png_path,
        '-vf', f"format=yuv420p,{anim}", 
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22', '-t', '5', '-y', output_mp4_path
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Ошибка FFmpeg: {e}")
        return False

# ==================== ГЕНЕРАЦИЯ HTML ====================
def generate_html_and_params(item):
    img_width = int(item.get('width', 736))
    img_height = int(item.get('height', 1108))
    canvas_width, canvas_height = 768, 1344
    scale = min(canvas_width / img_width, canvas_height / img_height)
    
    palette = random.choice([{'c1': '#1a1a1a', 'c2': '#2d2d2d'}, {'c1': '#1a2332', 'c2': '#2d3d4d'}])
    
    v = {
        "color1": palette['c1'], "color2": palette['c2'], "borderRadius": random.randint(50, 65),
        "blur": random.randint(20, 30), "brightness": round(random.uniform(0.75, 0.9), 2),
        "saturation": round(random.uniform(1.0, 1.2), 2), "rotation": random.randint(-2, 2),
        "showBlurredBg": True, "bgOpacity": 0.95, "padding": random.randint(35, 50),
        "imageBorderRadius": random.randint(15, 25), "borderWidth": random.randint(2, 5),
        "borderColor": 'rgba(255, 255, 255, 0.35)', "shadowY": 20, "shadowBlur": 50,
        "shadowOpacity": 0.6, "imageScale": 1.0, "accentColor": 'rgba(255, 255, 255, 0.3)',
        "watermark": "Free Download"
    }

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body, html {{ width: 768px; height: 1344px; overflow: hidden; }}
            .canvas {{ width: 768px; height: 1344px; position: relative; overflow: hidden; background: linear-gradient(135deg, {v['color1']} 0%, {v['color2']} 100%); }}
            .background-layer {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; background-image: url('{item.get('image_url', '')}'); background-size: cover; background-position: center; filter: blur({v['blur']}px) brightness({v['brightness']}) saturate({v['saturation']}); transform: scale(1.1) rotate({v['rotation']}deg); opacity: {v['bgOpacity']}; }}
            .frame {{ position: relative; z-index: 2; width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; padding: {v['padding']}px; }}
            .main-image {{ display: block; max-width: 100%; max-height: 100%; object-fit: contain; border-radius: {v['imageBorderRadius']}px; border: {v['borderWidth']}px solid {v['borderColor']}; box-shadow: 0 {v['shadowY']}px {v['shadowBlur']}px -10px rgba(0, 0, 0, {v['shadowOpacity']}); transform: scale({v['imageScale']}); }}
            .watermark {{ position: absolute; bottom: 30px; width: 100%; text-align: center; color: white; font-family: sans-serif; font-size: 32px; font-weight: bold; z-index: 10; text-shadow: 0 4px 10px rgba(0,0,0,0.8); }}
        </style>
    </head>
    <body>
        <div class="canvas">
            <div class="background-layer"></div>
            <div class="frame">
                <img class="main-image" src="{item.get('image_url', '')}">
            </div>
            <div class="watermark">{v['watermark']}</div>
        </div>
    </body>
    </html>
    """
    return html_content

# ==================== ОСНОВНАЯ ЛОГИКА ====================

def process_items():
    # Берем сразу пачку из 100 задач, чтобы не дергать базу слишком часто
    print(f"🔍 Ищем новые задачи в таблице {SUPABASE_TABLE}...")
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?select=*&status=eq.new&limit=100"
    response = requests.get(url, headers=SUPABASE_HEADERS)
    
    if response.status_code != 200:
        print(f"❌ Ошибка Supabase: {response.text}")
        return False

    items = response.json()
    if not items:
        print("📭 Все задачи обработаны. Новых пинов нет.")
        return False # Сигнал, что задач больше нет

    print(f"📦 Найдено {len(items)} задач. Начинаем потоковую обработку...")

    for item in items:
        item_id = item.get('id')
        print(f"\n🚀 Обработка [{items.index(item) + 1}/{len(items)}] | ID: {item_id}")

        temp_png = ""
        temp_mp4 = ""

        try:
            # 1. Рендер изображения
            html_content = generate_html_and_params(item)
            payload = {"html": html_content, "viewport": {"width": 768, "height": 1344}, "options": {"type": "png"}, "gotoOptions": {"waitUntil": "networkidle0"}}
            img_resp = requests.post(BROWSERLESS_URL, json=payload)
            
            if img_resp.status_code != 200:
                print(f"⚠️ Пропуск ID {item_id}: Ошибка Browserless")
                continue

            short_hash = generate_hash(str(item_id))
            temp_png = f"/tmp/temp_img_{short_hash}.png"
            temp_mp4 = f"/tmp/final_video_{short_hash}.mp4"
            
            with open(temp_png, 'wb') as f:
                f.write(img_resp.content)

            # 2. Создание Видео
            if not create_zoom_video(temp_png, temp_mp4):
                print(f"⚠️ Пропуск ID {item_id}: Ошибка FFmpeg")
                continue

            # 3. Загрузка в Cloudflare R2
            r2_filename = f"pin_video_{short_hash}.mp4"
            public_video_url = upload_to_r2(temp_mp4, r2_filename)
            
            if not public_video_url:
                print(f"⚠️ Пропуск ID {item_id}: Ошибка R2")
                continue

            # 4. Обращение к ИИ (Ollama)
            ai_data = None
            try:
                system_prompt = "You are Pinterest Optimizer 6.0. Output ONLY valid JSON containing 'title', 'description', 'alt_text'."
                user_prompt = f"Original Data:\nTitle: {item.get('title')}\nDesc: {item.get('description')}"
                
                ai_payload = {
                    "model": OLLAMA_MODEL,
                    "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                    "format": "json", "stream": False
                }
                ai_resp = requests.post(OLLAMA_URL, json=ai_payload)
                ai_data = json.loads(ai_resp.json()['message']['content'])
            except Exception as ai_err:
                print(f"⚠️ Ошибка ИИ для {item_id}: {ai_err}")
                continue

            # 5. Ссылка и Slug
            slug = create_slug(ai_data.get('title', 'pin'))
            pin_url = f"https://mothers-day-emroidery.pages.dev/?{slug}"

            # 6. Запись в Supabase
            patch_url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?id=eq.{item_id}"
            update_data = {
                "status": "completed",
                "final_title": ai_data.get('title', ''),
                "final_desc": ai_data.get('description', ''),
                "video_link": public_video_url,
                "final_alt_text": ai_data.get('alt_text', ''),
                "final_pin_url": pin_url
            }
            requests.patch(patch_url, headers=SUPABASE_HEADERS, json=update_data)
            print(f"✅ Готово! ID: {item_id}")

        except Exception as e:
            print(f"❌ Ошибка на элементе {item_id}: {e}")
            
        finally:
            if os.path.exists(temp_png): os.remove(temp_png)
            if os.path.exists(temp_mp4): os.remove(temp_mp4)
            
    return True # Сообщаем, что пачка обработана успешно
# ==================== ПЛАНИРОВЩИК ====================
if __name__ == "__main__":
    print("🚀 Запуск массовой уникализации...")
    
    while True:
        # Запускаем обработку пачки
        has_more = process_items()
        
        if has_more:
            print("\n⏳ Пачка завершена. Берем следующую через 5 секунд...")
            time.sleep(5)
        else:
            print("\n💎 Все задачи в базе выполнены! Переходим в режим ожидания (проверка каждые 30 минут).")
            time.sleep(1800) # Спим 30 минут, если задач больше нет