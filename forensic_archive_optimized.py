#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI-Агент для організації архіву судових експертиз (оптимізована версія)
Мінімалістичний підхід: пошук за ЄРДР, номером експертизи, датою
"""

# =============================================================================
# СИСТЕМНІ ІМПОРТИ
# =============================================================================
import sqlite3
import os
import re
import shutil
import pickle
import hashlib
import time
from datetime import datetime
from pathlib import Path
from functools import lru_cache

# =============================================================================
# СТОРОННІ БІБЛІОТЕКИ
# =============================================================================
import streamlit as st
import pandas as pd
from docx import Document

# =============================================================================
# ОПЦІОНАЛЬНІ БІБЛІОТЕКИ З ПЕРЕВІРКОЮ ДОСТУПНОСТІ
# =============================================================================
try:
    import win32com.client as win32
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False

try:
    import PyPDF2
    import pdfplumber
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# =============================================================================
# КОНСТАНТИ ТА НАЛАШТУВАННЯ
# =============================================================================
CACHE_DIR = "cache"
SEARCH_CACHE_SIZE = 1000
FILE_SCAN_CACHE_TTL = 3600
MAX_FILE_SIZE_MB = 100

# Підтримувані формати файлів
SUPPORTED_EXTENSIONS = ('.doc', '.docx', '.pdf')

# Системні папки для пропуску при сканування
SKIP_DIRECTORIES = {'temp', 'tmp', 'cache', 'recycle.bin', '$recycle.bin', 'system volume information'}

# Налаштування бази даних
DB_PRAGMAS = {
    'journal_mode': 'WAL',
    'synchronous': 'NORMAL',
    'cache_size': 10000,
    'temp_store': 'memory'
}

# =============================================================================
# ДОПОМІЖНІ ФУНКЦІЇ
# =============================================================================
def safe_get_value(row, column, default="Не вказано"):
    """Безпечне отримання значення з pandas Series"""
    try:
        value = row[column]
        return value if pd.notna(value) and value != '' else default
    except (KeyError, IndexError):
        return default

def ensure_directory_exists(directory_path):
    """Створити директорію якщо не існує"""
    try:
        os.makedirs(directory_path, exist_ok=True)
        return True
    except Exception as e:
        print(f"Помилка створення директорії {directory_path}: {e}")
        return False

def get_file_size_mb(file_path):
    """Отримати розмір файлу в МБ"""
    try:
        return os.path.getsize(file_path) / (1024 * 1024)
    except OSError:
        return 0

def is_system_directory(dir_name):
    """Перевірити чи це системна директорія"""
    return dir_name.lower().startswith('.') or dir_name.lower() in SKIP_DIRECTORIES

# =============================================================================
# ОСНОВНИЙ КЛАС АГЕНТА
# =============================================================================
class ForensicArchiveAgent:
    """
    Агент для роботи з архівом судових експертиз
    Підтримує два режими: створення нового архіву та індексування існуючого
    """
    
    def __init__(self, db_path="forensic_archive.db", archive_folder="archive", 
                 existing_archive_path=None, index_only_mode=False, lazy_init=True):
        """
        Ініціалізація агента
        
        Args:
            db_path: Шлях до бази даних SQLite
            archive_folder: Папка для нового архіву
            existing_archive_path: Шлях до існуючого архіву для індексування
            index_only_mode: Режим тільки індексування (без копіювання файлів)
            lazy_init: Відкладена ініціалізація важких компонентів
        """
        # Основні параметри
        self.db_path = db_path
        self.archive_folder = archive_folder
        self.existing_archive_path = existing_archive_path
        self.index_only_mode = index_only_mode
        self.lazy_init = lazy_init
        
        # Стан ініціалізації
        self._database_initialized = False
        self._cache_initialized = False
        self._archive_structure_created = False
        
        # Конфігурація секторів (спрощена)
        self.sectors = {
            "Сектор почеркознавчих досліджень": "почерк та ТЕД",
            "Сектор досліджень зброї": "балісти", 
            "Сектор трасологічних досліджень": "трасологія",
            "Сектор дактилоскопічних досліджень": "дактилоскопія",
            "Сектор балістичного обліку": "бал. облік"
        }
        
        # Ключові слова для визначення типів експертиз
        self.expertise_keywords = {
            "почеркознавча": ["почерк", "підпис", "рукопис"],
            "зброї": ["зброя", "пістолет", "автомат", "ніж", "холодна", "куля", "гільза", "балісти"],
            "трасологічна": ["слід", "відбиток", "трасол"],
            "дактилоскопічна": ["дактило", "палець", "папіляр"],
            "бал. облік": ["куля", "гільза"],
        }
        
        # Кеш для оптимізації
        self.search_cache = {}
        self.file_scan_cache = {}
        self.cache_timestamps = {}
        
        # Ініціалізація (може бути відкладена)
        if not self.lazy_init:
            self._initialize_all_components()
        else:
            # Мінімальна ініціалізація
            self._ensure_cache_directory()
    
    def _initialize_all_components(self):
        """Ініціалізація всіх компонентів агента"""
        self._ensure_database_initialized()
        self._ensure_cache_initialized()
        self._ensure_archive_structure()
    
    def _ensure_database_initialized(self):
        """Ледача ініціалізація бази даних"""
        if not self._database_initialized:
            self.init_database()
            self._database_initialized = True
    
    def _ensure_cache_initialized(self):
        """Ледача ініціалізація системи кешування"""
        if not self._cache_initialized:
            self.init_cache_system()
            self._cache_initialized = True
    
    def _ensure_archive_structure(self):
        """Ледача створення структури архіву"""
        if not self._archive_structure_created and not self.index_only_mode:
            self.create_archive_structure()
            self._archive_structure_created = True
    
    def _ensure_cache_directory(self):
        """Створити директорію кешу якщо не існує"""
        ensure_directory_exists(CACHE_DIR)
    
    def init_database(self):
        """
        Оптимізована ініціалізація бази даних з перевіркою необхідності міграції
        """
        db_exists = os.path.exists(self.db_path)
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Налаштування продуктивності SQLite
            for pragma, value in DB_PRAGMAS.items():
                cursor.execute(f"PRAGMA {pragma}={value}")
            
            if not db_exists:
                # Створення нової бази
                self._create_fresh_database(cursor)
            else:
                # Перевірка та міграція існуючої бази
                self._migrate_existing_database(cursor)
            
            conn.commit()
            conn.close()
            print("База даних успішно ініціалізована")
            
        except Exception as e:
            print(f"Помилка ініціалізації бази даних: {str(e)}")
            if 'conn' in locals():
                conn.close()
            raise
    
    def _create_fresh_database(self, cursor):
        """Створення нової бази даних з оптимальною структурою"""
        cursor.execute('''
            CREATE TABLE expertise_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                
                -- Основні поля для пошуку (з індексами)
                erddr_number TEXT,
                expertise_number TEXT UNIQUE,
                expertise_date TEXT,
                expertise_year INTEGER,
                expertise_type TEXT,
                
                -- Поля для структури архіву
                expert_name TEXT NOT NULL DEFAULT "Невідомий_експерт",
                sector TEXT NOT NULL DEFAULT "почерк та ТЕД",
                
                -- Технічні поля
                source_file TEXT,
                file_path TEXT,
                file_size INTEGER DEFAULT 0,
                file_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Створення індексів для оптимізації пошуку
        self._create_database_indexes(cursor)
        print("Створена нова база даних з оптимальною структурою")
    
    def _migrate_existing_database(self, cursor):
        """Міграція існуючої бази даних з перевіркою необхідності"""
        # Перевірка існування таблиці
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='expertise_cases'")
        if not cursor.fetchone():
            self._create_fresh_database(cursor)
            return
        
        # Отримання поточної структури
        cursor.execute("PRAGMA table_info(expertise_cases)")
        existing_columns = {column[1]: column[2] for column in cursor.fetchall()}
        
        # Необхідні колонки з типами
        required_columns = {
            'expert_name': 'TEXT',
            'sector': 'TEXT', 
            'file_size': 'INTEGER',
            'file_hash': 'TEXT',
            'updated_at': 'TIMESTAMP'
        }
        
        # Додавання відсутніх колонок
        migration_needed = False
        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                cursor.execute(f'ALTER TABLE expertise_cases ADD COLUMN {column_name} {column_type}')
                migration_needed = True
                print(f"Додано колонку {column_name}")
        
        if migration_needed:
            # Оновлення NULL значень
            cursor.execute('UPDATE expertise_cases SET expert_name = "Невідомий_експерт" WHERE expert_name IS NULL OR expert_name = ""')
            cursor.execute('UPDATE expertise_cases SET sector = "почерк та ТЕД" WHERE sector IS NULL OR sector = ""')
            cursor.execute('UPDATE expertise_cases SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL')
            print("Виконана міграція даних")
        
        # Перевірка та створення індексів
        self._create_database_indexes(cursor)
    
    def _create_database_indexes(self, cursor):
        """Створення індексів для оптимізації пошуку"""
        indexes = [
            'CREATE INDEX IF NOT EXISTS idx_erddr ON expertise_cases(erddr_number)',
            'CREATE INDEX IF NOT EXISTS idx_number ON expertise_cases(expertise_number)',
            'CREATE INDEX IF NOT EXISTS idx_year ON expertise_cases(expertise_year)',
            'CREATE INDEX IF NOT EXISTS idx_expert ON expertise_cases(expert_name)',
            'CREATE INDEX IF NOT EXISTS idx_sector ON expertise_cases(sector)',
            'CREATE INDEX IF NOT EXISTS idx_type ON expertise_cases(expertise_type)',
            'CREATE INDEX IF NOT EXISTS idx_created ON expertise_cases(created_at)',
            'CREATE INDEX IF NOT EXISTS idx_composite_search ON expertise_cases(expertise_year, expert_name, sector)'
        ]
        
        for index_sql in indexes:
            cursor.execute(index_sql)
    
    def init_cache_system(self):
        """Ініціалізація системи кешування з оптимізацією"""
        self._ensure_cache_directory()
        
        # Ініціалізація кешів у пам'яті
        if not hasattr(self, 'search_cache'):
            self.search_cache = {}
        if not hasattr(self, 'file_scan_cache'):
            self.file_scan_cache = {}
        if not hasattr(self, 'cache_timestamps'):
            self.cache_timestamps = {}
        
        # Очищення застарілого кешу при ініціалізації
        self._cleanup_expired_cache()
        
        print("Система кешування ініціалізована")
    
    def _cleanup_expired_cache(self):
        """Очищення застарілого кешу з диску"""
        if not os.path.exists(CACHE_DIR):
            return
        
        current_time = time.time()
        cleaned_count = 0
        
        try:
            for filename in os.listdir(CACHE_DIR):
                file_path = os.path.join(CACHE_DIR, filename)
                if os.path.isfile(file_path):
                    file_age = current_time - os.path.getmtime(file_path)
                    if file_age > FILE_SCAN_CACHE_TTL:
                        os.remove(file_path)
                        cleaned_count += 1
            
            if cleaned_count > 0:
                print(f"Очищено {cleaned_count} застарілих файлів кешу")
        except Exception as e:
            print(f"Помилка очищення кешу: {e}")
    
    def create_archive_structure(self):
        """Створення структури архіву тільки при необхідності"""
        if self.index_only_mode:
            return  # Не створюємо структуру в режимі індексування
        
        try:
            # Створення основної папки архіву
            if not ensure_directory_exists(self.archive_folder):
                raise Exception(f"Не вдалося створити папку архіву: {self.archive_folder}")
            
            # Створення папок секторів
            created_sectors = []
            for sector_name, sector_code in self.sectors.items():
                sector_path = os.path.join(self.archive_folder, sector_code)
                if ensure_directory_exists(sector_path):
                    created_sectors.append(sector_code)
                else:
                    print(f"Попередження: не вдалося створити папку сектора {sector_code}")
            
            print(f"Створена структура архіву: {len(created_sectors)} секторів")
            
        except Exception as e:
            print(f"Помилка створення структури архіву: {str(e)}")
            # Не припиняємо роботу, просто логуємо помилку

    # =============================================================================
    # МЕТОДИ КЕШУВАННЯ (ОПТИМІЗОВАНІ)
    # =============================================================================
    
    @lru_cache(maxsize=1000)
    def get_cache_key(self, **kwargs):
        """Оптимізована генерація ключа кешу з LRU кешуванням"""
        # Створюємо детермінований ключ з параметрів
        cache_data = tuple(sorted((k, str(v)) for k, v in kwargs.items() if v is not None))
        return hashlib.md5(str(cache_data).encode()).hexdigest()

    def is_cache_valid(self, cache_key, ttl_seconds=FILE_SCAN_CACHE_TTL):
        """Оптимізована перевірка актуальності кешу"""
        timestamp = self.cache_timestamps.get(cache_key)
        if timestamp is None:
            return False
        return (time.time() - timestamp) < ttl_seconds

    def save_search_cache(self, cache_key, results):
        """Асинхронне збереження результатів пошуку в кеш"""
        if not self._cache_initialized:
            return False
            
        cache_file = os.path.join(CACHE_DIR, f"search_{cache_key}.pkl")
        try:
            # Обмежуємо розмір кешу
            if len(results) > SEARCH_CACHE_SIZE:
                results = results.head(SEARCH_CACHE_SIZE)
            
            with open(cache_file, 'wb') as f:
                pickle.dump(results, f, protocol=pickle.HIGHEST_PROTOCOL)
            
            self.cache_timestamps[cache_key] = time.time()
            return True
        except Exception as e:
            print(f"Помилка збереження кешу: {e}")
            return False

    def load_search_cache(self, cache_key):
        """Оптимізоване завантаження результатів з кешу"""
        if not self._cache_initialized:
            return None
            
        cache_file = os.path.join(CACHE_DIR, f"search_{cache_key}.pkl")
        
        try:
            if os.path.exists(cache_file) and self.is_cache_valid(cache_key):
                with open(cache_file, 'rb') as f:
                    return pickle.load(f)
        except Exception as e:
            print(f"Помилка завантаження кешу: {e}")
            # Видаляємо пошкоджений файл кешу
            try:
                os.remove(cache_file)
            except:
                pass
        
        return None

    # =============================================================================
    # ПУБЛІЧНІ МЕТОДИ (ОБГОРТКИ З ПЕРЕВІРКОЮ ІНІЦІАЛІЗАЦІЇ)
    # =============================================================================
    
    def search_documents(self, **kwargs):
        """Пошук документів з автоматичною ініціалізацією"""
        self._ensure_database_initialized()
        self._ensure_cache_initialized()
        return self._search_documents_impl(**kwargs)
    
    def add_document(self, file_path, **kwargs):
        """Додавання документа з автоматичною ініціалізацією"""
        self._ensure_database_initialized()
        self._ensure_archive_structure()
        return self._add_document_impl(file_path, **kwargs)
    
    def scan_existing_archive(self, archive_path, **kwargs):
        """Сканування архіву з автоматичною ініціалізацією"""
        self._ensure_cache_initialized()
        return self._scan_existing_archive_impl(archive_path, **kwargs)

    # Тут будуть імплементації методів у наступних частинах...
    
    def _search_documents_impl(self, **kwargs):
        """Заготовка для імплементації пошуку"""
        pass
    
    def _add_document_impl(self, file_path, **kwargs):
        """Заготовка для імплементації додавання"""
        pass
    
    def _scan_existing_archive_impl(self, archive_path, **kwargs):
        """Заготовка для імплементації сканування"""
        pass