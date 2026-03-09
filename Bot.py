import os
import asyncio
import time
import sys
import hashlib
from datetime import datetime, timezone
import logging
from typing import Optional, Dict, Any

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Telegram imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)

# YouTube imports
from pytube import YouTube
from pytube.exceptions import PytubeError

# VK imports
import vk_api
from vk_api.exceptions import ApiError

# HTTP imports
import requests
import aiohttp
from urllib3.util.retry import Retry
from urllib3.exceptions import InsecureRequestWarning
import certifi
import ssl

# Other imports
from tqdm import tqdm
import yarl
import websockets
from PIL import Image
from moviepy.editor import VideoFileClip
import mutagen
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4


class MediaBot:
    """
    Многофункциональный бот для работы с медиа из разных источников
    """
    
    def __init__(self, telegram_token: str, vk_token: Optional[str] = None):
        """
        Инициализация бота с настройками повторных попыток
        
        Args:
            telegram_token: Токен Telegram бота
            vk_token: Токен VK API (опционально)
        """
        # Настройки повторных попыток
        self.retry_config = {
            'max_attempts': 3,
            'initial_delay': 1,
            'max_delay': 10,
            'backoff_factor': 2,
            'timeout': 30
        }
        
        # Счетчики для статистики
        self.stats = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'retry_count': 0,
            'start_time': datetime.now(timezone.utc)
        }
        
        # Инициализация клиентов
        self.telegram_token = telegram_token
        self.vk_token = vk_token
        self.vk_session = None
        self.vk_api = None
        
        if vk_token:
            self._init_vk()
        
        # Настройка SSL/TLS с certifi
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())
        
        # Настройка сессии requests с повторными попытками
        self.session = self._create_retry_session()
        
        logger.info("Bot initialized successfully")
    
    def _create_retry_session(self) -> requests.Session:
        """Создание сессии с механизмом повторных попыток"""
        session = requests.Session()
        
        retry_strategy = Retry(
            total=self.retry_config['max_attempts'],
            backoff_factor=self.retry_config['backoff_factor'],
            status_forcelist=[429, 500, 502, 503, 504],
        )
        
        adapter = requests.adapters.HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
    
    def _init_vk(self):
        """Инициализация VK API"""
        try:
            self.vk_session = vk_api.VkApi(token=self.vk_token)
            self.vk_api = self.vk_session.get_api()
            logger.info("VK API initialized")
        except Exception as e:
            logger.error(f"Failed to initialize VK API: {e}")
    
    async def execute_with_retry(self, func, *args, **kwargs) -> Any:
        """
        Универсальный метод выполнения с повторными попытками
        
        Args:
            func: Асинхронная функция для выполнения
            *args, **kwargs: Аргументы функции
        
        Returns:
            Результат выполнения функции
        """
        self.stats['total_requests'] += 1
        attempt = 0
        delay = self.retry_config['initial_delay']
        
        while attempt < self.retry_config['max_attempts']:
            try:
                result = await func(*args, **kwargs)
                self.stats['successful_requests'] += 1
                return result
                
            except Exception as e:
                attempt += 1
                self.stats['retry_count'] += 1
                
                if attempt == self.retry_config['max_attempts']:
                    self.stats['failed_requests'] += 1
                    logger.error(f"Failed after {attempt} attempts: {e}")
                    raise
                
                logger.warning(f"Attempt {attempt} failed, retrying in {delay}s: {e}")
                await asyncio.sleep(delay)
                delay = min(delay * self.retry_config['backoff_factor'], 
                          self.retry_config['max_delay'])
    
    async def download_youtube_video(self, url: str, quality: str = "highest") -> Dict[str, Any]:
        """
        Скачивание видео с YouTube
        
        Args:
            url: URL видео на YouTube
            quality: Качество видео
        
        Returns:
            Информация о скачанном видео
        """
        async def _download():
            yt = YouTube(url, use_oauth=False, allow_oauth_cache=True)
            
            # Получение информации о видео
            video_info = {
                'title': yt.title,
                'author': yt.author,
                'length': yt.length,
                'views': yt.views,
                'description': yt.description[:200] if yt.description else "No description"
            }
            
            # Выбор потока для скачивания
            if quality == "highest":
                stream = yt.streams.get_highest_resolution()
            else:
                stream = yt.streams.filter(res=quality).first()
                if not stream:
                    stream = yt.streams.get_highest_resolution()
            
            # Скачивание с прогресс-баром
            filename = f"downloads/{yt.video_id}.mp4"
            os.makedirs("downloads", exist_ok=True)
            
            # Используем tqdm для отображения прогресса
            stream.download(output_path="downloads", filename=f"{yt.video_id}.mp4")
            
            # Получение информации о файле
            file_size = os.path.getsize(filename)
            
            return {
                'filename': filename,
                'file_size': file_size,
                'info': video_info,
                'stream_info': {
                    'resolution': stream.resolution,
                    'fps': stream.fps,
                    'filesize_mb': round(file_size / (1024 * 1024), 2)
                }
            }
        
        return await self.execute_with_retry(_download)
    
    async def get_vk_video_info(self, video_id: str) -> Dict[str, Any]:
        """
        Получение информации о видео из VK
        
        Args:
            video_id: ID видео в формате owner_id_video_id
        
        Returns:
            Информация о видео
        """
        if not self.vk_api:
            raise ValueError("VK API not initialized")
        
        async def _get_info():
            owner_id, video_id = video_id.split('_')
            video = self.vk_api.video.get(
                owner_id=owner_id,
                videos=f"{owner_id}_{video_id}"
            )
            
            if video['items']:
                item = video['items'][0]
                return {
                    'title': item['title'],
                    'duration': item['duration'],
                    'views': item['views'],
                    'player_url': item['player']
                }
            return None
        
        return await self.execute_with_retry(_get_info)
    
    async def process_image(self, image_path: str, output_format: str = "JPEG") -> str:
        """
        Обработка изображения с помощью PIL
        
        Args:
            image_path: Путь к изображению
            output_format: Формат выходного файла
        
        Returns:
            Путь к обработанному изображению
        """
        try:
            # Открываем изображение
            img = Image.open(image_path)
            
            # Конвертируем в RGB если нужно
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Изменяем размер если слишком большое
            max_size = (1920, 1080)
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            
            # Сохраняем в новом формате
            output_path = image_path.rsplit('.', 1)[0] + f'.{output_format.lower()}'
            img.save(output_path, output_format, quality=85)
            
            return output_path
            
        except Exception as e:
            logger.error(f"Image processing failed: {e}")
            raise
    
    async def get_video_info_mutagen(self, video_path: str) -> Dict[str, Any]:
        """
        Получение информации о видео через mutagen
        
        Args:
            video_path: Путь к видеофайлу
        
        Returns:
            Информация о видео
        """
        try:
            # Пробуем как MP4
            if video_path.endswith('.mp4'):
                video = MP4(video_path)
                info = {
                    'length': round(video.info.length, 2),
                    'bitrate': video.info.bitrate,
                    'sample_rate': video.info.sample_rate
                }
            else:
                video = mutagen.File(video_path)
                info = {'length': round(video.info.length, 2)}
            
            return info
            
        except Exception as e:
            logger.error(f"Failed to get video info: {e}")
            return {}
    
    async def compress_video(self, input_path: str, output_path: str, 
                            target_size_mb: int = 10) -> bool:
        """
        Сжатие видео с помощью moviepy
        
        Args:
            input_path: Путь к исходному видео
            output_path: Путь для сохранения сжатого видео
            target_size_mb: Целевой размер в МБ
        
        Returns:
            True если успешно, иначе False
        """
        try:
            # Загружаем видео
            clip = VideoFileClip(input_path)
            
            # Рассчитываем битрейт для целевого размера
            duration = clip.duration
            target_bitrate = int((target_size_mb * 8 * 1024) / duration)
            
            # Сжимаем видео
            clip.write_videofile(
                output_path,
                bitrate=f"{target_bitrate}k",
                codec='libx264',
                audio_codec='aac'
            )
            
            clip.close()
            return True
            
        except Exception as e:
            logger.error(f"Video compression failed: {e}")
            return False
    
    async def download_file_with_progress(self, url: str, filename: str) -> str:
        """
        Скачивание файла с отображением прогресса через tqdm
        
        Args:
            url: URL файла
            filename: Имя для сохранения
        
        Returns:
            Путь к скачанному файлу
        """
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                total_size = int(response.headers.get('content-length', 0))
                
                # Создаем прогресс-бар
                progress = tqdm(
                    total=total_size,
                    unit='B',
                    unit_scale=True,
                    desc=filename
                )
                
                # Скачиваем чанками
                with open(filename, 'wb') as f:
                    async for chunk in response.content.iter_chunked(8192):
                        f.write(chunk)
                        progress.update(len(chunk))
                
                progress.close()
                
        return filename
    
    def get_stats(self) -> Dict[str, Any]:
        """Получение статистики работы бота"""
        uptime = datetime.now(timezone.utc) - self.stats['start_time']
        
        return {
            'uptime': str(uptime).split('.')[0],
            'total_requests': self.stats['total_requests'],
            'success_rate': round(
                self.stats['successful_requests'] / max(self.stats['total_requests'], 1) * 100, 
                2
            ),
            'retry_count': self.stats['retry_count'],
            'failed_requests': self.stats['failed_requests']
        }


class TelegramBotHandler:
    """Обработчик команд Telegram бота"""
    
    def __init__(self, token: str, vk_token: Optional[str] = None):
        self.media_bot = MediaBot(token, vk_token)
        self.application = Application.builder().token(token).build()
        self._setup_handlers()
    
    def _setup_handlers(self):
        """Настройка обработчиков команд"""
        # Команды
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        self.application.add_handler(CommandHandler("youtube", self.youtube_command))
        self.application.add_handler(CommandHandler("vkvideo", self.vkvideo_command))
        
        # Обработчики сообщений
        self.application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, self.handle_text
        ))
        self.application.add_handler(MessageHandler(
            filters.VIDEO, self.handle_video
        ))
        self.application.add_handler(MessageHandler(
            filters.PHOTO, self.handle_photo
        ))
        
        # Обработчик callback-запросов
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        welcome_message = (
            "👋 Добро пожаловать Super bot!\n\n"
            "Я могу помочь вам с:\n"
            "📹 Скачиванием видео с YouTube\n"
            "🎥 Информацией о видео VK\n"
            "🖼️ Обработкой изображений\n"
            "🎬 Сжатием видео\n\n"
            "Используйте /help для списка команд"
        )
        await update.message.reply_text(welcome_message)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /help"""
        help_text = (
            "📋 Доступные команды:\n\n"
            "/start - Начало работы\n"
            "/help - Это сообщение\n"
            "/stats - Статистика работы\n"
            "/youtube [URL] - Скачать видео с YouTube\n"
            "/vkvideo [ID] - Информация о видео VK\n\n"
            "📎 Также я могу обрабатывать:\n"
            "• Отправленные видео (сжатие)\n"
            "• Отправленные фото (обработка)\n"
            "• Текстовые ссылки на YouTube"
        )
        await update.message.reply_text(help_text)
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /stats"""
        stats = self.media_bot.get_stats()
        stats_text = (
            "📊 Статистика работы:\n\n"
            f"⏱️ Uptime: {stats['uptime']}\n"
            f"📥 Всего запросов: {stats['total_requests']}\n"
            f"✅ Успешно: {stats['success_rate']}%\n"
            f"🔄 Повторов: {stats['retry_count']}\n"
            f"❌ Ошибок: {stats['failed_requests']}"
        )
        await update.message.reply_text(stats_text)
    
    async def youtube_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /youtube"""
        if not context.args:
            await update.message.reply_text("❌ Укажите URL видео YouTube")
            return
        
        url = context.args[0]
        await update.message.reply_text("⏬ Скачиваю видео...")
        
        try:
            # Скачиваем видео
            result = await self.media_bot.download_youtube_video(url)
            
            # Отправляем информацию
            info_text = (
                f"✅ Видео скачано!\n\n"
                f"🎬 {result['info']['title']}\n"
                f"👤 Автор: {result['info']['author']}\n"
                f"📏 Разрешение: {result['stream_info']['resolution']}\n"
                f"📦 Размер: {result['stream_info']['filesize_mb']} MB"
            )
            
            await update.message.reply_text(info_text)
            
            # Отправляем видео файл
            with open(result['filename'], 'rb') as video:
                await update.message.reply_video(video)
            
            # Получаем дополнительную информацию через mutagen
            video_info = await self.media_bot.get_video_info_mutagen(result['filename'])
            if video_info:
                await update.message.reply_text(
                    f"ℹ️ Длительность: {video_info.get('length', 'N/A')} сек"
                )
            
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")
    
    async def vkvideo_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /vkvideo"""
        if not context.args:
            await update.message.reply_text("❌ Укажите ID видео (формат: owner_id_video_id)")
            return
        
        video_id = context.args[0]
        
        try:
            info = await self.media_bot.get_vk_video_info(video_id)
            if info:
                info_text = (
                    f"🎥 Информация о видео VK:\n\n"
                    f"📌 {info['title']}\n"
                    f"⏱️ Длительность: {info['duration']} сек\n"
                    f"👁️ Просмотров: {info['views']}\n"
                    f"🔗 {info['player_url']}"
                )
                await update.message.reply_text(info_text)
            else:
                await update.message.reply_text("❌ Видео не найдено")
                
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений"""
        text = update.message.text
        
        # Проверяем, является ли текст ссылкой на YouTube
        if 'youtube.com' in text or 'youtu.be' in text:
            await update.message.reply_text("⏬ Обнаружена ссылка YouTube, скачиваю...")
            
            try:
                result = await self.media_bot.download_youtube_video(text)
                
                # Конвертируем в MP3 если короткое видео
                if result['info']['length'] < 600:  # меньше 10 минут
                    await update.message.reply_text("🔄 Конвертирую в MP3...")
                    
                with open(result['filename'], 'rb') as video:
                    await update.message.reply_video(video)
                    
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")
        else:
            await update.message.reply_text("Я понимаю только команды и ссылки YouTube")
    
    async def handle_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка видеофайлов"""
        video = update.message.video
        await update.message.reply_text(f"📹 Получено видео: {video.file_name}\nПытаюсь сжать...")
        
        # Здесь можно добавить логику сжатия видео
        keyboard = [
            [InlineKeyboardButton("Сжать до 10 MB", callback_data="compress_10")],
            [InlineKeyboardButton("Сжать до 20 MB", callback_data="compress_20")],
            [InlineKeyboardButton("Получить информацию", callback_data="video_info")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Выберите действие:", reply_markup=reply_markup)
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка фотографий"""
        photo = update.message.photo[-1]  # Берем самое большое фото
        file = await photo.get_file()
        
        await update.message.reply_text("🖼️ Обрабатываю фото...")
        
        # Сохраняем и обрабатываем фото
        file_path = f"downloads/photo_{int(time.time())}.jpg"
        os.makedirs("downloads", exist_ok=True)
        await file.download_to_drive(file_path)
        
        # Обрабатываем изображение
        processed_path = await self.media_bot.process_image(file_path, "JPEG")
        
        # Отправляем обработанное фото
        with open(processed_path, 'rb') as img:
            await update.message.reply_photo(img, caption="✅ Фото обработано")
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка callback-запросов"""
        query = update.callback_query
        await query.answer()
        
        if query.data == "video_info":
            await query.edit_message_text("ℹ️ Функция информации о видео в разработке")
        elif query.data.startswith("compress_"):
            size = query.data.split("_")[1]
            await query.edit_message_text(f"🔧 Сжатие до {size} MB... (в разработке)")
    
    def run(self):
        """Запуск бота"""
        logger.info("Starting bot...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)


def main():
    """Главная функция запуска"""
    # Получаем токены из переменных окружения
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '8687522698:AAHQ_1xra4q_70IY-nuCjn5mGFmuRj6ApNI
')
    VK_TOKEN = os.getenv('VK_TOKEN', None)
    
    if TELEGRAM_TOKEN == '8687522698:AAHQ_1xra4q_70IY-nuCjn5mGFmuRj6ApNI
':
        logger.error("Please set your TELEGRAM_TOKEN")
        sys.exit(1)
    
    # Создаем и запускаем бота
    bot_handler = TelegramBotHandler(TELEGRAM_TOKEN, VK_TOKEN)
    
    try:
        bot_handler.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
