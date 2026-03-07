import os
import asyncio
import subprocess
import sys
import hashlib
import time
from datetime import datetime, timedelta

# Функция для установки библиотек
def install_packages():
    packages = [
        'aiogram==2.25.1',
        'yt-dlp',
        'aiofiles',
        'redis'
    ]
    
    for package in packages:
        try:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', package])
        except:
            pass

# Устанавливаем библиотеки
install_packages()

# Импортируем после установки
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import ParseMode, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
import yt_dlp
import aiofiles
import json
import re

# Токен бота
TOKEN = '8687522698:AAHQ_1xra4q_70IY-nuCjn5mGFmuRj6ApNI'

# Инициализация бота
bot = Bot(token=TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# Класс для кеширования
class VideoCache:
    def __init__(self, cache_dir='video_cache', cache_duration=3600):  # 1 час
        self.cache_dir = cache_dir
        self.cache_duration = cache_duration
        os.makedirs(cache_dir, exist_ok=True)
        
    def get_cache_key(self, url):
        return hashlib.md5(url.encode()).hexdigest()
    
    def get_cached_video(self, url):
        cache_key = self.get_cache_key(url)
        cache_file = os.path.join(self.cache_dir, f"{cache_key}.json")
        video_file = os.path.join(self.cache_dir, f"{cache_key}.mp4")
        
        if os.path.exists(cache_file) and os.path.exists(video_file):
            with open(cache_file, 'r') as f:
                cache_data = json.load(f)
            
            cache_time = datetime.fromisoformat(cache_data['timestamp'])
            if datetime.now() - cache_time < timedelta(seconds=self.cache_duration):
                return video_file, cache_data['title']
        
        return None, None
    
    def save_to_cache(self, url, video_path, title):
        cache_key = self.get_cache_key(url)
        cache_file = os.path.join(self.cache_dir, f"{cache_key}.json")
        
        cache_data = {
            'url': url,
            'title': title,
            'timestamp': datetime.now().isoformat()
        }
        
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f)

# Создаем экземпляр кеша
video_cache = VideoCache()

# Класс для скачивания видео с расширенными возможностями обхода блокировок
class VideoDownloader:
    def __init__(self):
        # Базовые опции, которые будут дорабатываться под каждую платформу
        self.base_ydl_opts = {
            'format': 'best[height<=720]',  # Ограничим качество для скорости и экономии места
            'quiet': True,
            'no_warnings': True,
            'retries': 10,  # Увеличим число попыток
            'fragment_retries': 10,
            'ignoreerrors': True,  # Продолжать при ошибках в плейлистах
            'geo_bypass': True,  # Пытаться обойти гео-блокировки
        }
    
    async def download_video(self, url):
        # Проверяем кеш
        cached_video, cached_title = video_cache.get_cached_video(url)
        if cached_video and os.path.exists(cached_video):
            return cached_video, cached_title
        
        # Определяем платформу для выбора стратегии
        platform = self.detect_platform(url)
        
        # Настраиваем опции в зависимости от платформы
        ydl_opts = self.base_ydl_opts.copy()
        
        if platform == 'youtube':
            # Расширенные настройки для YouTube для обхода n-sig и других блокировок
            ydl_opts.update({
                'extractor_args': {'youtube': {'player_client': ['android', 'web']}},  # Маскируемся под разные клиенты
                'impersonate': 'chrome:windows-10',  # Имитируем Chrome на Windows 10
            })
        elif platform == 'tiktok':
            # TikTok требует тщательной маскировки под браузер
            ydl_opts.update({
                'impersonate': 'chrome:windows-10',  # Имитация браузера критична для TikTok
            })
        elif platform == 'instagram':
            # Instagram может требовать правильный Referer
            ydl_opts.update({
                'impersonate': 'chrome:windows-10',
                'http_headers': {
                    'Referer': 'https://www.instagram.com/',  # Важный заголовок для Instagram
                    'Origin': 'https://www.instagram.com',
                }
            })
        
        # Если платформа не определена, используем общие настройки с подменой браузера
        if not platform:
            ydl_opts['impersonate'] = 'chrome:windows-10'
        
        # Создаем временное имя файла
        cache_key = video_cache.get_cache_key(url)
        output_file = os.path.join(video_cache.cache_dir, f"{cache_key}.mp4")
        ydl_opts['outtmpl'] = output_file
        
        try:
            # Сначала получаем информацию о видео (чтобы узнать название)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get('title', 'video')
                
                # Скачиваем видео
                ydl.params['outtmpl'] = output_file
                ydl.download([url])
            
            # Сохраняем в кеш
            video_cache.save_to_cache(url, output_file, title)
            return output_file, title
            
        except Exception as e:
            # Если что-то пошло не так, пробуем с самыми базовыми настройками
            try:
                fallback_opts = {
                    'format': 'best[height<=720]',
                    'quiet': True,
                    'no_warnings': True,
                    'outtmpl': output_file,
                }
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    title = info.get('title', 'video')
                    ydl.download([url])
                
                video_cache.save_to_cache(url, output_file, title)
                return output_file, title
            except Exception as fallback_error:
                raise Exception(f"Ошибка при скачивании: {str(e)}. Резервный метод также не сработал: {str(fallback_error)}")
    
    def detect_platform(self, url):
        """Определяем платформу по URL"""
        patterns = {
            'tiktok': r'(https?://)?(www\.)?(tiktok\.com|vt\.tiktok\.com)/.+',
            'instagram': r'(https?://)?(www\.)?instagram\.com/(p|reel|tv)/.+',
            'youtube': r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+'
        }
        
        for platform, pattern in patterns.items():
            if re.match(pattern, url):
                return platform
        return None
    
    async def cleanup_old_files(self):
        """Очистка старых файлов из кеша"""
        now = datetime.now()
        for filename in os.listdir(video_cache.cache_dir):
            if filename.endswith('.mp4') or filename.endswith('.json'):
                filepath = os.path.join(video_cache.cache_dir, filename)
                file_time = datetime.fromtimestamp(os.path.getctime(filepath))
                if now - file_time > timedelta(hours=1):
                    try:
                        os.remove(filepath)
                    except:
                        pass

# Создаем экземпляр загрузчика
downloader = VideoDownloader()

# Функция для проверки ссылки (дублирует метод класса, но оставим для обратной совместимости)
def validate_url(url):
    return downloader.detect_platform(url)

# Команда старт
@dp.message_handler(commands=['start'])
async def start_command(message: types.Message):
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton(text="📱 TikTok", callback_data="info_tiktok"),
        InlineKeyboardButton(text="📷 Instagram", callback_data="info_instagram"),
        InlineKeyboardButton(text="▶️ YouTube", callback_data="info_youtube"),
        InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
        InlineKeyboardButton(text="🗑 Очистить кеш", callback_data="clean_cache")
    ]
    keyboard.add(*buttons)
    
    await message.reply(
        "👋 Привет! Я бот для скачивания видео из социальных сетей!\n\n"
        "📥 Просто отправь мне ссылку на видео из TikTok, Instagram или YouTube, "
        "и я скачаю его для тебя!\n\n"
        "⚡️ Видео кешируются для быстрой загрузки при повторных запросах.\n\n"
        "🔒 **Улучшенная защита**: бот использует современные методы обхода блокировок "
        "(имитация браузера, правильные заголовки, обход гео-блокировок)\n\n"
        "Выбери действие:",
        reply_markup=keyboard
    )

# Обработка кнопок
@dp.callback_query_handler(lambda c: True)
async def process_callback(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    
    if callback_query.data == "info_tiktok":
        await bot.send_message(
            callback_query.from_user.id,
            "📱 **TikTok**\n\n"
            "Поддерживаемые форматы ссылок:\n"
            "• https://www.tiktok.com/@user/video/123456789\n"
            "• https://vt.tiktok.com/ZS123456/\n\n"
            "⚙️ **Особенности**: бот имитирует браузер Chrome для обхода защиты TikTok.\n\n"
            "Просто отправьте ссылку и получите видео!"
        )
    
    elif callback_query.data == "info_instagram":
        await bot.send_message(
            callback_query.from_user.id,
            "📷 **Instagram**\n\n"
            "Поддерживаемые форматы ссылок:\n"
            "• https://www.instagram.com/p/ABC123/\n"
            "• https://www.instagram.com/reel/ABC123/\n"
            "• https://www.instagram.com/tv/ABC123/\n\n"
            "⚙️ **Особенности**: добавляются правильные HTTP-заголовки (Referer, Origin) "
            "для обхода блокировок Instagram.\n\n"
            "Отправьте ссылку на пост или рилс!"
        )
    
    elif callback_query.data == "info_youtube":
        await bot.send_message(
            callback_query.from_user.id,
            "▶️ **YouTube**\n\n"
            "Поддерживаемые форматы ссылок:\n"
            "• https://www.youtube.com/watch?v=ABC123\n"
            "• https://youtu.be/ABC123\n"
            "• https://www.youtube.com/shorts/ABC123\n\n"
            "⚙️ **Особенности**:\n"
            "• Используются разные типы плееров (Android, Web) для обхода n-sig защиты\n"
            "• Имитация Chrome на Windows 10\n"
            "• Попытка обхода гео-блокировок\n\n"
            "Видео скачиваются в максимальном качестве до 720p для экономии места."
        )
    
    elif callback_query.data == "help":
        await bot.send_message(
            callback_query.from_user.id,
            "❓ **Помощь**\n\n"
            "Как пользоваться ботом:\n"
            "1️⃣ Отправьте ссылку на видео\n"
            "2️⃣ Подождите несколько секунд\n"
            "3️⃣ Получите видео в чат\n\n"
            "⚠️ **Важно:**\n"
            "• Видео из YouTube могут загружаться дольше\n"
            "• При повторной отправке той же ссылки видео загрузится мгновенно из кеша\n"
            "• Кеш автоматически очищается каждый час\n"
            "• Если видео не загружается, попробуйте еще раз — бот использует несколько методов обхода\n\n"
            "По всем вопросам: @your_support"
        )
    
    elif callback_query.data == "clean_cache":
        try:
            for filename in os.listdir(video_cache.cache_dir):
                filepath = os.path.join(video_cache.cache_dir, filename)
                os.remove(filepath)
            await bot.send_message(
                callback_query.from_user.id,
                "🗑 Кеш успешно очищен!"
            )
        except:
            await bot.send_message(
                callback_query.from_user.id,
                "❌ Ошибка при очистке кеша"
            )

# Обработка ссылок
@dp.message_handler()
async def handle_video_link(message: types.Message):
    url = message.text.strip()
    
    # Проверяем ссылку
    platform = validate_url(url)
    if not platform:
        await message.reply(
            "❌ Неподдерживаемая ссылка!\n\n"
            "Пожалуйста, отправьте ссылку на видео из TikTok, Instagram или YouTube."
        )
        return
    
    # Отправляем статус
    status_msg = await message.reply(f"⏬ Начинаю загрузку видео из {platform.capitalize()} с использованием усиленной защиты...")
    
    try:
        # Скачиваем видео
        video_path, title = await downloader.download_video(url)
        
        # Отправляем видео
        with open(video_path, 'rb') as video_file:
            await message.reply_video(
                video=video_file,
                caption=f"✅ Видео успешно загружено!\n\n📹 {title}\n\nИсточник: {platform.capitalize()}",
                supports_streaming=True
            )
        
        # Удаляем статусное сообщение
        await status_msg.delete()
        
        # Очищаем старые файлы
        asyncio.create_task(downloader.cleanup_old_files())
        
    except Exception as e:
        await status_msg.edit_text(
            f"❌ Ошибка при загрузке видео:\n{str(e)}\n\n"
            "Пожалуйста, проверьте ссылку и попробуйте снова.\n\n"
            "💡 **Совет**: Иногда помогает повторная отправка той же ссылки."
        )

# Запуск бота
if __name__ == '__main__':
    print("🚀 Бот запущен с усиленной защитой от блокировок...")
    print("Поддерживаемые платформы: YouTube, TikTok, Instagram")
    executor.start_polling(dp, skip_updates=True)