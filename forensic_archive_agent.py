#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI-Агент для організації архіву судових експертиз (виправлена версія)
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

class ForensicArchiveAgent:
    def __init__(self, db_path="forensic_archive.db", archive_folder="archive", 
             existing_archive_path=None, index_only_mode=False):
        self.db_path = db_path
        self.archive_folder = archive_folder
        self.existing_archive_path = existing_archive_path  # Шлях до існуючого архіву
        self.index_only_mode = index_only_mode  # Режим тільки індексування
        
        # Спрощена структура секторів (тільки основні)
        self.sectors = {
            "Сектор почеркознавчих досліджень": "почерк та  ТЕД",
            "Сектор досліджень зброї": "балісти", 
            "Сектор трасологічних досліджень": "трасологія",
            "Сектор дактилоскопічних досліджень": "дактилоскопія",
            "Сектор балістичного обліку": "бал. облік"
        }
        
        # Спрощене визначення типів експертиз
        self.expertise_keywords = {
            "почеркознавча": ["почерк", "підпис", "рукопис"],
            "зброї": ["зброя", "пістолет", "автомат", "ніж", "холодна", "куля", "гільза", "балісти"],
            "трасологічна": ["слід", "відбиток", "трасол"],
            "дактилоскопічна": ["дактило", "палець", "папіляр"],
            "бал. облік": ["куля", "гільза"],
        }
        
        self.init_database()
        self.create_archive_structure()
    
    def init_cache_system(self):
        """Ініціалізація системи кешування"""
        if not os.path.exists(CACHE_DIR):
            os.makedirs(CACHE_DIR)
        
        self.search_cache = {}
        self.file_scan_cache = {}
        self.cache_timestamps = {}

    def init_database(self):
        """База даних з полем для експерта - ВИПРАВЛЕНА ВЕРСІЯ з міграцією"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Створення таблиці з усіма необхідними полями
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS expertise_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    
                    -- Основні поля для пошуку
                    erddr_number TEXT,
                    expertise_number TEXT UNIQUE,
                    expertise_date TEXT,
                    expertise_year INTEGER,
                    expertise_type TEXT,
                    
                    -- Поля для структури архіву
                    expert_name TEXT,
                    sector TEXT,
                    
                    -- Технічні поля
                    source_file TEXT,
                    file_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Перевірка існування колонок та їх додавання при необхідності
            cursor.execute("PRAGMA table_info(expertise_cases)")
            columns = [column[1] for column in cursor.fetchall()]
            
            # Додаємо відсутні колонки
            if 'expert_name' not in columns:
                cursor.execute('ALTER TABLE expertise_cases ADD COLUMN expert_name TEXT DEFAULT "Невідомий_експерт"')
                print("Додано колонку expert_name")
            
            if 'sector' not in columns:
                cursor.execute('ALTER TABLE expertise_cases ADD COLUMN sector TEXT DEFAULT "почерк та ТЕД"')
                print("Додано колонку sector")
            
            # Оновлення NULL значень для нових колонок
            cursor.execute('UPDATE expertise_cases SET expert_name = "Невідомий_експерт" WHERE expert_name IS NULL OR expert_name = ""')
            cursor.execute('UPDATE expertise_cases SET sector = "почерк та ТЕД" WHERE sector IS NULL OR sector = ""')
            
            # Індекси для швидкого пошуку
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_erddr ON expertise_cases(erddr_number)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_number ON expertise_cases(expertise_number)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_year ON expertise_cases(expertise_year)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_expert ON expertise_cases(expert_name)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_sector ON expertise_cases(sector)')
            
            conn.commit()
            conn.close()
            print("База даних успішно ініціалізована та оновлена")
            
        except Exception as e:
            print(f"Помилка ініціалізації бази даних: {str(e)}")
            if 'conn' in locals():
                conn.close()

    def create_archive_structure(self):
        """Створити мінімальну структуру архіву"""
        try:
            if not os.path.exists(self.archive_folder):
                os.makedirs(self.archive_folder)
            
            for sector_name, sector_code in self.sectors.items():
                sector_path = os.path.join(self.archive_folder, sector_code)
                if not os.path.exists(sector_path):
                    os.makedirs(sector_path)
        except Exception as e:
            st.error(f"Помилка створення структури архіву: {str(e)}")

    def get_cache_key(self, **kwargs):
        """Генерація ключа кешу з параметрів пошуку"""
        # Створюємо унікальний ключ з параметрів
        cache_data = str(sorted(kwargs.items()))
        return hashlib.md5(cache_data.encode()).hexdigest()

    def is_cache_valid(self, cache_key, ttl_seconds=3600):
        """Перевірка чи кеш ще актуальний"""
        if cache_key not in self.cache_timestamps:
            return False
        
        return (time.time() - self.cache_timestamps[cache_key]) < ttl_seconds

    def save_search_cache(self, cache_key, results):
        """Збереження результатів пошуку в кеш"""
        cache_file = os.path.join(CACHE_DIR, f"search_{cache_key}.pkl")
        try:
            with open(cache_file, 'wb') as f:
                pickle.dump(results, f)
            self.cache_timestamps[cache_key] = time.time()
            return True
        except Exception as e:
            print(f"Помилка збереження кешу: {e}")
            return False

    def load_search_cache(self, cache_key):
        """Завантаження результатів з кешу"""
        cache_file = os.path.join(CACHE_DIR, f"search_{cache_key}.pkl")
        try:
            if os.path.exists(cache_file) and self.is_cache_valid(cache_key):
                with open(cache_file, 'rb') as f:
                    return pickle.load(f)
        except Exception as e:
            print(f"Помилка завантаження кешу: {e}")
        return None

    def convert_doc_to_docx(self, doc_path):
        """Конвертація .doc в .docx з перевіркою доступності win32com"""
        if not WIN32_AVAILABLE:
            st.error("Конвертація .doc файлів недоступна. Встановіть pywin32 або використовуйте .docx файли.")
            return None
            
        try:
            word = win32.Dispatch("Word.Application")
            word.Visible = False
            
            doc = word.Documents.Open(doc_path)
            docx_path = doc_path.replace('.doc', '.docx')
            doc.SaveAs2(docx_path, 16)  # 16 = wdFormatXMLDocument
            doc.Close()
            word.Quit()
            
            return docx_path
        except Exception as e:
            st.error(f"Помилка конвертації {doc_path}: {str(e)}")
            return None
    
    def extract_docx_content(self, file_path):
        """Витягти текст з .docx файлу з покращеною обробкою кодування"""
        try:
            doc = Document(file_path)
            full_text = []
            for paragraph in doc.paragraphs:
                if paragraph.text.strip():
                    # Очищаємо текст від зайвих символів
                    clean_text = paragraph.text.strip().encode('utf-8', errors='ignore').decode('utf-8')
                    full_text.append(clean_text)
            return '\n'.join(full_text)
        except Exception as e:
            st.error(f"Помилка читання файлу {file_path}: {str(e)}")
            return None
    
    def extract_pdf_content(self, file_path):
        """Витягти текст з PDF файлу з двома методами"""
        if not PDF_AVAILABLE:
            return None
            
        text_content = ""
        
        # Спочатку пробуємо pdfplumber (краще для складних PDF)
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages[:10]:  # Обмежуємо до 10 сторінок для продуктивності
                    page_text = page.extract_text()
                    if page_text:
                        text_content += page_text + "\n"
            
            if text_content.strip():
                return text_content
        except Exception as e:
            print(f"pdfplumber помилка: {e}")
        
        # Якщо pdfplumber не спрацював, пробуємо PyPDF2
        try:
            import PyPDF2
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                for page_num in range(min(10, len(pdf_reader.pages))):  # Обмежуємо до 10 сторінок
                    page = pdf_reader.pages[page_num]
                    text_content += page.extract_text() + "\n"
            
            return text_content if text_content.strip() else None
        except Exception as e:
            st.error(f"Помилка читання PDF {file_path}: {str(e)}")
            return None

    def get_file_content(self, file_path):
        """Універсальна функція для читання різних типів файлів"""
        file_ext = os.path.splitext(file_path)[1].lower()
        
        if file_ext == '.docx':
            return self.extract_docx_content(file_path)
        elif file_ext == '.pdf':
            return self.extract_pdf_content(file_path)
        elif file_ext == '.doc':
            if WIN32_AVAILABLE:
                docx_path = self.convert_doc_to_docx(file_path)
                if docx_path:
                    content = self.extract_docx_content(docx_path)
                    # Видаляємо тимчасовий файл
                    try:
                        os.remove(docx_path)
                    except:
                        pass
                    return content
            return None
        else:
            return None
    
    def is_large_file(self, file_path, max_size_mb=50):
        """Перевірити чи файл не надто великий"""
        try:
            file_size = os.path.getsize(file_path)
            return file_size > (max_size_mb * 1024 * 1024)
        except:
            return True  # Якщо не можемо перевірити, вважаємо великим

    def parse_expertise_document(self, text, filename, sector_code=None):
        """Спрощений парсинг - тільки ключові поля з урахуванням сектора"""
        data = {
            'erddr_number': '',
            'expertise_number': '',
            'expertise_date': '',
            'expertise_year': None,
            'expertise_type': '',
            'source_file': filename
        }
        
        # №ЄРДР (найважливіше поле) - розширені патерни
        erddr_patterns = [
            r'№\s*ЄРДР\s*([^\s,\n\.]+)',
            r'ЄРДР\s*№?\s*([^\s,\n\.]+)',
            r'кримінальному провадженню\s*№\s*([^\s,\n\.]+)',
            r'провадження\s*№\s*([^\s,\n\.]+)',
            r'справі\s*№\s*([^\s,\n\.]+)'
        ]
        for pattern in erddr_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                erddr = match.group(1).strip()
                # Очищаємо тільки зайві пробіли та символи на краях
                erddr = re.sub(r'^[^\d]*|[^\d]*$', '', erddr)
                if len(erddr) >= 8:  # Зменшуємо мінімум до 8 цифр
                    data['erddr_number'] = erddr
                    break
        
        # Номер експертизи (ключове поле) - розширені патерни
        expertise_patterns = [
            r'№\s*(\d+/\d+[А-Я]+-\d+)',
            r'експертиза\s*№\s*([^\s,\n\.]+)',
            r'висновок\s*№\s*([^\s,\n\.]+)',
            r'висновок.*?№\s*([^\s,\n\.]+)',
            r'№\s*([^,\n\.]*\d+[^,\n\.]*)'
        ]
        for pattern in expertise_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                expertise_num = match.group(1).strip()
                # Перевіряємо чи містить цифри
                if re.search(r'\d', expertise_num) and len(expertise_num) > 3:
                    data['expertise_number'] = expertise_num
                    break
        
        # Дата та рік (важливо для сортування) - розширені патерни
        date_patterns = [
            r'(\d{1,2}\.\d{1,2}\.(\d{4}))',
            r'(\d{1,2}\/\d{1,2}\/(\d{4}))',
            r'від\s*(\d{1,2}\.\d{1,2}\.(\d{4}))'
        ]
        for pattern in date_patterns:
            match = re.search(pattern, text)
            if match:
                data['expertise_date'] = match.group(1)
                data['expertise_year'] = int(match.group(2))
                break
        
        # Якщо дату не знайдено, спробуємо знайти хоча б рік
        if not data['expertise_year']:
            year_match = re.search(r'20(\d{2})', text)
            if year_match:
                year = int(f"20{year_match.group(1)}")
                if 2000 <= year <= 2030:
                    data['expertise_year'] = year
        
        # Визначення типу з урахуванням сектора
        if sector_code:
            data['expertise_type'] = self.determine_expertise_type_by_sector(text, sector_code)
        else:
            data['expertise_type'] = self.determine_expertise_type(text)
        
        return data
    
    def determine_expertise_type(self, text):
        """Покращене визначення типу експертизи за ключовими словами з пріоритетами"""
        text_lower = text.lower()
        
        # Словник з ключовими словами та їх вагами (більший вага = вищий пріоритет)
        expertise_keywords_weighted = {
            "зброї": {
                "keywords": ["зброя", "пістолет", "автомат", "ніж", "холодна зброя", "вогнепальна", 
                           "куля", "гільза", "балісти", "постріл", "набій", "патрон", "ствол"],
                "weight": 10
            },
            "трасологічна": {
                "keywords": ["трасол", "слід", "відбиток", "папіляр", "взуття", "шини", 
                           "транспорт", "колесо", "протектор", "механоскопі", "замок", "ключ"],
                "weight": 8
            },
            "дактилоскопічна": {
                "keywords": ["дактило", "палець", "папіляр", "відбиток пальц", "дерматогліф"],
                "weight": 9
            },
            "почеркознавча": {
                "keywords": ["почерк", "підпис", "рукопис", "письмо", "графолог", "ідентифік.*підпис"],
                "weight": 7
            },
            "бал. облік": {
                "keywords": ["куля.*облік", "гільза.*облік", "балістичний облік", "ідентифік.*куля"],
                "weight": 6
            }
        }
        
        # Підрахунок балів для кожного типу
        type_scores = {}
        
        for expertise_type, data in expertise_keywords_weighted.items():
            score = 0
            keywords = data["keywords"]
            weight = data["weight"]
            
            for keyword in keywords:
                # Використовуємо регулярні вирази для більш точного пошуку
                import re
                if re.search(keyword, text_lower):
                    score += weight
                    # Додаткові бали за кількість входжень
                    occurrences = len(re.findall(keyword, text_lower))
                    score += (occurrences - 1) * 2  # За кожне додаткове входження +2 бали
            
            if score > 0:
                type_scores[expertise_type] = score
        
        # Спеціальна логіка для розрізнення схожих типів
        # Якщо знайдено "куля" або "гільза", але також є трасологічні ознаки
        if "трасологічна" in type_scores and "зброї" in type_scores:
            # Перевіряємо контекст - якщо більше трасологічних ознак
            trasology_indicators = ["слід", "відбиток", "взуття", "шини", "механоскопі", "замок"]
            weapons_indicators = ["постріл", "вогнепальна", "ствол", "набій"]
            
            trasology_count = sum(1 for ind in trasology_indicators if ind in text_lower)
            weapons_count = sum(1 for ind in weapons_indicators if ind in text_lower)
            
            if trasology_count > weapons_count:
                type_scores["трасологічна"] += 5  # Додаємо бонус
            else:
                type_scores["зброї"] += 5
        
        # Повертаємо тип з найвищим балом
        if type_scores:
            best_type = max(type_scores, key=type_scores.get)
            return best_type
        
        return "невизначено"
    
    def determine_expertise_type_by_sector(self, text, sector_code):
        """Визначення типу з урахуванням сектора"""
        # Спочатку використовуємо загальний алгоритм
        general_type = self.determine_expertise_type(text)
        
        # Потім коригуємо відповідно до сектора
        sector_type_mapping = {
            "почерк та ТЕД": "почеркознавча",
            "балісти": "зброї", 
            "трасологія": "трасологічна",
            "дактилоскопія": "дактилоскопічна",
            "бал. облік": "бал. облік"
        }
        
        # Якщо тип не визначено або не відповідає сектору, використовуємо тип сектора
        if general_type == "невизначено" or sector_code in sector_type_mapping:
            sector_type = sector_type_mapping.get(sector_code, general_type)
            
            # Але якщо загальний алгоритм дав чіткий результат, перевіряємо чи він не суперечить сектору
            if general_type != "невизначено":
                # Перевіряємо чи відповідає тип сектору
                expected_type = sector_type_mapping.get(sector_code)
                if expected_type and general_type != expected_type:
                    # Логування невідповідності для налагодження
                    print(f"УВАГА: Визначений тип '{general_type}' не відповідає сектору '{sector_code}' (очікувався '{expected_type}')")
                    # Повертаємо тип сектора, але з позначкою
                    return f"{expected_type} (авто)"
            
            return sector_type
        
        return general_type
    
    def determine_sector_from_path(self, file_path):
        """Визначити сектор з шляху файлу"""
        path_lower = file_path.lower()
        
        # Словник ключових слів для кожного сектора в шляху
        sector_keywords = {
            "почерк та ТЕД": ["почерк", "тед", "підпис", "рукопис", "графолог"],
            "балісти": ["балісти", "зброя", "пістолет", "куля", "гільза", "постріл"],
            "трасологія": ["трасол", "слід", "відбиток", "взуття", "шини", "механо"],
            "дактилоскопія": ["дактило", "палець", "папіляр", "дерматогліф"],
            "бал. облік": ["облік", "кулі", "гільзи", "ідентифік"]
        }
        
        # Аналізуємо шлях по частинах
        path_parts = file_path.replace('\\', '/').split('/')
        
        for sector, keywords in sector_keywords.items():
            for part in path_parts:
                for keyword in keywords:
                    if keyword in part.lower():
                        return self.sectors.get(sector, sector)
        
        # Якщо не знайдено, повертаємо за замовчуванням
        return "почерк та ТЕД"

    def extract_expert_from_path(self, file_path):
        """Витягти прізвище експерта з шляху"""
        path_parts = file_path.replace('\\', '/').split('/')
        
        # Шукаємо частину шляху що схожа на прізвище
        for i, part in enumerate(path_parts):
            # Прізвище зазвичай містить літери, можливо підкреслення
            if re.match(r'^[А-ЯЁа-яё\w_]+$', part) and len(part) > 3:
                # Перевіряємо чи це не назва сектора або рік
                if not re.match(r'^\d{4}$', part) and part.lower() not in ['архів', 'archive', 'documents']:
                    # Очищаємо від зайвих символів
                    clean_name = re.sub(r'[_\-\s]+', ' ', part).strip()
                    if clean_name:
                        return clean_name
        
        return "Невідомий_експерт"

    def extract_year_from_path(self, file_path):
        """Витягти рік з шляху файлу"""
        # Шукаємо 4-значний рік у шляху
        year_matches = re.findall(r'20\d{2}', file_path)
        
        for year_str in year_matches:
            year = int(year_str)
            if 2000 <= year <= 2030:
                return year
        
        return None

    def get_file_destination_path(self, sector_code, expert_name, expertise_year, expertise_number):
        """Покращена структура з кращою обробкою помилок"""
        year_folder = str(expertise_year) if expertise_year else str(datetime.now().year)
        
        # Безпечні імена папок
        safe_expert = re.sub(r'[<>:"/\\|?*]', '_', expert_name or 'Невідомий_експерт')
        safe_sector = re.sub(r'[<>:"/\\|?*]', '_', sector_code or 'почерк та ТЕД')
        
        if expertise_number:
            safe_expertise = re.sub(r'[<>:"/\\|?*]', '_', str(expertise_number))
        else:
            safe_expertise = f"експертиза_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        path = os.path.join(
            self.archive_folder,
            safe_sector,
            safe_expert,
            year_folder,
            safe_expertise
        )
        
        try:
            os.makedirs(path, exist_ok=True)
            return path
        except Exception as e:
            # Fallback до простішої структури
            simple_path = os.path.join(self.archive_folder, safe_sector)
            os.makedirs(simple_path, exist_ok=True)
            return simple_path
    
    def add_document(self, file_path, sector_code=None, expert_name=None, expertise_year=None, expertise_number=None):
        """Доопрацьована функція додавання файлів з можливістю вказати параметри"""
        if not os.path.exists(file_path):
            return False, "Файл не знайдено"
        
        file_ext = os.path.splitext(file_path)[1].lower()
        
        # Конвертація .doc в .docx тільки якщо доступно
        original_path = file_path
        if file_ext == '.doc':
            if not WIN32_AVAILABLE:
                return False, "Конвертація .doc файлів недоступна. Використовуйте .docx файли."
            
            docx_path = self.convert_doc_to_docx(file_path)
            if docx_path:
                file_path = docx_path
                file_ext = '.docx'
            else:
                return False, "Не вдалося конвертувати .doc файл"
        
        # Підтримка тільки .docx
        supported_formats = ['.doc', '.docx', '.pdf']
        if file_ext not in supported_formats:
            return False, f"Підтримуються файли: {', '.join(supported_formats)}. Отримано: {file_ext}"
        
        # Витягування тексту
        text = self.get_file_content(file_path)
        if not text:
            return False, f"Не вдалося прочитати текст з файлу {file_ext}"
        
        # Парсинг даних з файлу з урахуванням сектора
        parsed_data = self.parse_expertise_document(text, os.path.basename(original_path), sector_code)
        
        # Використовуємо передані параметри або дані з файлу
        data = {
            'erddr_number': parsed_data['erddr_number'],
            'expertise_number': expertise_number or parsed_data['expertise_number'],
            'expertise_date': parsed_data['expertise_date'],
            'expertise_year': expertise_year or parsed_data['expertise_year'],
            'expertise_type': parsed_data['expertise_type'],  # Вже враховує сектор
            'expert_name': expert_name or 'Невідомий_експерт',
            'sector': sector_code or 'почерк та ТЕД',
            'source_file': parsed_data['source_file']
        }
        
        # Валідація обов'язкових полів
        if not data['expert_name'] or data['expert_name'] == 'Невідомий_експерт':
            if not expert_name:
                return False, "Не вказано прізвище експерта. Будь ласка, введіть прізвище експерта."
        
        if not data['sector']:
            return False, "Не вказано сектор. Будь ласка, оберіть сектор."
        
        # М'яка перевірка номера експертизи
        if not data['expertise_number']:
            # Генеруємо номер за датою якщо не знайдено
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            data['expertise_number'] = f"AUTO_{timestamp}"
        
        # Перевірка унікальності
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id FROM expertise_cases WHERE expertise_number = ?", (data['expertise_number'],))
            if cursor.fetchone():
                conn.close()
                return False, f"Експертиза №{data['expertise_number']} вже існує в базі"
        except Exception as e:
            conn.close()
            return False, f"Помилка перевірки унікальності: {str(e)}"
        
        # Структуроване збереження або збереження посилання
        try:
            if self.index_only_mode and self.existing_archive_path:
                # Режим індексування - зберігаємо тільки посилання
                if not file_path.startswith(self.existing_archive_path):
                    return False, "Файл не знаходиться в зазначеному архіві"
                data['file_path'] = file_path  # Зберігаємо оригінальний шлях
            else:
                # Режим копіювання (існуючий код)
                dest_path = self.get_file_destination_path(
                    data['sector'],
                    data['expert_name'],
                    data['expertise_year'],
                    data['expertise_number']
                )
                
                dest_file_path = os.path.join(dest_path, os.path.basename(original_path))
                shutil.copy2(original_path, dest_file_path)
                
                data['file_path'] = dest_file_path
                
        except Exception as e:
            conn.close()
            return False, f"Помилка копіювання файлу: {str(e)}"
        
        # Додавання в базу
        try:
            cursor.execute('''
                INSERT INTO expertise_cases 
                (erddr_number, expertise_number, expertise_date, expertise_year, 
                expertise_type, expert_name, sector, source_file, file_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                data['erddr_number'], data['expertise_number'], data['expertise_date'],
                data['expertise_year'], data['expertise_type'], data['expert_name'],
                data['sector'], data['source_file'], data['file_path']
            ))
            conn.commit()
            conn.close()
            
            # Видалення тимчасового .docx файлу якщо був створений
            if file_path != original_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass  # Не критично
            
            # Покращене повідомлення з типом експертизи
            return True, f"Додано в {data['sector']}/{data['expert_name']}: {os.path.basename(dest_path)} (тип: {data['expertise_type']})"
            
        except Exception as e:
            conn.close()
            return False, f"Помилка збереження в базу: {str(e)}"
    
    def scan_existing_archive(self, archive_path, progress_callback=None, 
                         file_limit=None, skip_large_files=True, update_database=False):
        """Оптимізоване сканування існуючого архіву з контролем ресурсів"""
        
        # Перевіряємо кеш сканування
        cache_key = f"scan_{hashlib.md5(archive_path.encode()).hexdigest()}"
        cache_file = os.path.join(CACHE_DIR, f"file_scan_{cache_key}.pkl")
        
        # Якщо кеш існує і актуальний, використовуємо його
        if os.path.exists(cache_file):
            try:
                cache_time = os.path.getmtime(cache_file)
                if (time.time() - cache_time) < FILE_SCAN_CACHE_TTL:
                    with open(cache_file, 'rb') as f:
                        cached_data = pickle.load(f)
                        return cached_data['found_files'], cached_data['errors']
            except Exception as e:
                print(f"Помилка читання кешу сканування: {e}")
        
        found_files = []
        errors = []
        files_processed = 0
        
        supported_extensions = ('.doc', '.docx', '.pdf')
        
        try:
            # Рекурсивний обхід з оптимізаціями
            for root, dirs, files in os.walk(archive_path):
                # Пропускаємо системні папки
                dirs[:] = [d for d in dirs if not d.startswith('.') and d.lower() not in ['temp', 'tmp', 'cache']]
                
                for file in files:
                    if not file.lower().endswith(supported_extensions):
                        continue
                    
                    if file_limit and files_processed >= file_limit:
                        break
                    
                    file_path = os.path.join(root, file)
                    
                    # Перевірка розміру файлу
                    if skip_large_files and self.is_large_file(file_path, max_size_mb=100):
                        errors.append(f"Пропущено великий файл: {file} (>100MB)")
                        continue
                    
                    # Перевірка доступності файлу
                    try:
                        if not os.access(file_path, os.R_OK):
                            errors.append(f"Немає доступу до файлу: {file}")
                            continue
                    except Exception as e:
                        errors.append(f"Помилка перевірки файлу {file}: {str(e)}")
                        continue
                    
                    found_files.append({
                        'file_path': file_path,
                        'relative_path': os.path.relpath(file_path, archive_path),
                        'file_name': file,
                        'directory': os.path.basename(root),
                        'file_size': os.path.getsize(file_path),
                        'file_ext': os.path.splitext(file)[1].lower()
                    })
                    
                    files_processed += 1
                    
                    # Виклик callback для прогресу
                    if progress_callback and files_processed % 10 == 0:  # Кожні 10 файлів
                        progress_callback(files_processed, file)
                
                if file_limit and files_processed >= file_limit:
                    break
        
        except Exception as e:
            errors.append(f"Помилка сканування архіву: {str(e)}")
        
        # Збереження результатів у кеш
        try:
            cache_data = {
                'found_files': found_files,
                'errors': errors,
                'scan_time': time.time()
            }
            with open(cache_file, 'wb') as f:
                pickle.dump(cache_data, f)
        except Exception as e:
            print(f"Помилка збереження кешу сканування: {e}")
        
        return found_files, errors

    def index_existing_archive_batch(self, archive_path, progress_callback=None):
        """Пакетне індексування існуючого архіву"""
        found_files, errors = self.scan_existing_archive(archive_path, update_database=False)
        
        results = []
        total_files = len(found_files)
        
        for i, file_info in enumerate(found_files):
            if progress_callback:
                progress_callback(i, total_files, file_info['file_name'])
                
            try:
                # Визначаємо сектор з шляху файлу
                sector_code = self.determine_sector_from_path(file_info['file_path'])
                expert_name = self.extract_expert_from_path(file_info['file_path'])
                
                success, message = self.add_document(
                    file_path=file_info['file_path'],
                    sector_code=sector_code,
                    expert_name=expert_name
                )
                
                results.append({
                    'file_path': file_info['file_path'],
                    'success': success,
                    'message': message
                })
                
            except Exception as e:
                results.append({
                    'file_path': file_info['file_path'],
                    'success': False,
                    'message': f"Помилка індексування: {str(e)}"
                })
        
        return results

    def search_documents(self, erddr_number="", expertise_number="", expertise_year=None, 
                    expert_name="", use_cache=True, limit=500):
        """Оптимізований пошук з кешуванням та пагінацією"""
        
        # Генеруємо ключ кешу
        if use_cache:
            cache_key = self.get_cache_key(
                erddr=erddr_number.strip(),
                expertise=expertise_number.strip(),
                year=expertise_year,
                expert=expert_name.strip(),
                limit=limit
            )
            
            # Перевіряємо кеш
            cached_result = self.load_search_cache(cache_key)
            if cached_result is not None:
                return cached_result
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Перевірка існування таблиці
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='expertise_cases'")
            if not cursor.fetchone():
                conn.close()
                return pd.DataFrame()
            
            # Оптимізований запит тільки з необхідними колонками
            selected_columns = [
                'id', 'erddr_number', 'expertise_number', 'expertise_date', 
                'expertise_year', 'expert_name', 'sector', 'expertise_type', 
                'file_path', 'source_file'
            ]
            
            # Перевірка існування колонок
            cursor.execute("PRAGMA table_info(expertise_cases)")
            existing_columns = [column[1] for column in cursor.fetchall()]
            
            # Використовуємо тільки існуючі колонки
            available_columns = [col for col in selected_columns if col in existing_columns]
            columns_str = ', '.join(available_columns)
            
            base_query = f"SELECT {columns_str} FROM expertise_cases WHERE 1=1"
            params = []
            
            # Додаємо умови пошуку з індексами
            if erddr_number.strip():
                base_query += " AND erddr_number LIKE ?"
                params.append(f"%{erddr_number.strip()}%")
            
            if expertise_number.strip():
                base_query += " AND expertise_number LIKE ?"
                params.append(f"%{expertise_number.strip()}%")
            
            if expertise_year and 'expertise_year' in existing_columns:
                base_query += " AND expertise_year = ?"
                params.append(expertise_year)
            
            if expert_name.strip() and 'expert_name' in existing_columns:
                base_query += " AND expert_name LIKE ?"
                params.append(f"%{expert_name.strip()}%")
            
            # Сортування та обмеження
            order_parts = []
            if 'expertise_year' in existing_columns:
                order_parts.append("expertise_year DESC")
            if 'expertise_date' in existing_columns:
                order_parts.append("expertise_date DESC")
            order_parts.append("id DESC")
            
            base_query += f" ORDER BY {', '.join(order_parts)} LIMIT ?"
            params.append(limit)
            
            # Виконання запиту
            df = pd.read_sql_query(base_query, conn, params=params)
            conn.close()
            
            # Збереження в кеш
            if use_cache and not df.empty:
                self.save_search_cache(cache_key, df)
            
            return df
            
        except Exception as e:
            if 'conn' in locals():
                conn.close()
            st.error(f"Помилка пошуку: {str(e)}")
            return pd.DataFrame()
    
    def open_document(self, file_path):
        """Покращена функція відкриття з кращою обробкою помилок"""
        try:
            if not os.path.exists(file_path):
                return False, "Файл не знайдено за вказаним шляхом"
            
            file_ext = os.path.splitext(file_path)[1].lower()
            if file_ext not in ['.doc', '.docx']:
                return False, f"Непідтримуваний формат файлу: {file_ext}"
            
            import platform
            import subprocess
            
            try:
                if platform.system() == 'Windows':
                    os.startfile(file_path)
                elif platform.system() == 'Darwin':
                    subprocess.run(['open', file_path], check=True)
                else:
                    subprocess.run(['xdg-open', file_path], check=True)
                
                return True, "Файл відкрито"
            except subprocess.CalledProcessError:
                return False, "Не вдалося відкрити файл. Перевірте чи встановлено програму для відкриття .doc/.docx файлів"
            except Exception as e:
                return False, f"Помилка відкриття файлу: {str(e)}"
                
        except Exception as e:
            return False, f"Помилка: {str(e)}"
           
    def delete_document(self, expertise_id):
        """Видалення документа з архіву та бази даних - ВИПРАВЛЕНА ВЕРСІЯ"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Отримання інформації про файл
            cursor.execute("SELECT file_path, expertise_number FROM expertise_cases WHERE id = ?", (expertise_id,))
            result = cursor.fetchone()
            
            if not result:
                conn.close()
                return False, "Документ не знайдено в базі даних"
            
            file_path, expertise_number = result
            
            # Видалення файлу з файлової системи
            if os.path.exists(file_path):
                try:
                    # Видалення файлу
                    os.remove(file_path)
                    
                    # Спроба видалити порожню папку експертизи
                    parent_dir = os.path.dirname(file_path)
                    if os.path.exists(parent_dir) and not os.listdir(parent_dir):
                        os.rmdir(parent_dir)
                except Exception as e:
                    st.warning(f"Не вдалося видалити файл з диску: {str(e)}")
                    # Продовжуємо видалення з бази навіть якщо файл не видалився
            
            # Видалення з бази даних
            cursor.execute("DELETE FROM expertise_cases WHERE id = ?", (expertise_id,))
            conn.commit()
            conn.close()
            
            return True, f"Експертизу №{expertise_number} видалено"
            
        except Exception as e:
            conn.close()
            return False, f"Помилка видалення: {str(e)}"
    
    def get_archive_statistics(self):
        """Статистика з повною обробкою помилок"""
        conn = sqlite3.connect(self.db_path)
        
        try:
            # Перевірка існування таблиці
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='expertise_cases'")
            if not cursor.fetchone():
                conn.close()
                return 0, pd.DataFrame(), pd.DataFrame()
            
            # Перевірка колонок
            cursor.execute("PRAGMA table_info(expertise_cases)")
            columns = [column[1] for column in cursor.fetchall()]
            
            # Загальна кількість
            total_result = pd.read_sql_query("SELECT COUNT(*) as count FROM expertise_cases", conn)
            total_docs = total_result.iloc[0]['count'] if not total_result.empty else 0
            
            # Статистика за типами (тільки якщо колонка існує)
            if 'expertise_type' in columns:
                by_type = pd.read_sql_query("""
                    SELECT expertise_type, COUNT(*) as count 
                    FROM expertise_cases 
                    WHERE expertise_type IS NOT NULL AND expertise_type != ''
                    GROUP BY expertise_type 
                    ORDER BY count DESC
                """, conn)
            else:
                by_type = pd.DataFrame()
            
            # Статистика за роками (тільки якщо колонка існує)
            if 'expertise_year' in columns:
                by_year = pd.read_sql_query("""
                    SELECT expertise_year, COUNT(*) as count 
                    FROM expertise_cases 
                    WHERE expertise_year IS NOT NULL
                    GROUP BY expertise_year 
                    ORDER BY expertise_year DESC
                """, conn)
            else:
                by_year = pd.DataFrame()
            
            conn.close()
            return total_docs, by_type, by_year
            
        except Exception as e:
            conn.close()
            return 0, pd.DataFrame(), pd.DataFrame()
    
    def batch_add_from_expert_folder(self, expert_folder_path):
        """Пакетне додавання з папки експерта: expert_name/year/expertise_number/*.doc(x)"""
        if not os.path.exists(expert_folder_path):
            return False, "Папка експерта не знайдена", []
        
        expert_name = os.path.basename(expert_folder_path.rstrip('/\\'))
        found_files = []
        errors = []
        
        print(f"Сканування папки: {expert_folder_path}")
        print(f"Прізвище експерта: {expert_name}")
        
        # Пошук структури: expert_name/year/expertise_number/*.doc(x)
        try:
            # Отримуємо список папок у головній папці експерта
            items_in_expert_folder = os.listdir(expert_folder_path)
            print(f"Знайдено елементів у папці експерта: {len(items_in_expert_folder)}")
            
            for item in items_in_expert_folder:
                item_path = os.path.join(expert_folder_path, item)
                
                # Перевіряємо чи це папка
                if not os.path.isdir(item_path):
                    print(f"Пропускаємо файл: {item}")
                    continue
                
                # Перевіряємо чи це папка з роком (4 цифри)
                if not re.match(r'^\d{4}$', item):
                    print(f"Пропускаємо папку (не рік): {item}")
                    continue
                
                year = int(item)
                if not (2000 <= year <= 2030):
                    print(f"Пропускаємо рік поза діапазоном: {year}")
                    continue
                
                print(f"Обробляємо рік: {year}")
                year_path = item_path
                
                # Шукаємо папки з номерами експертиз у папці року
                try:
                    expertise_folders = os.listdir(year_path)
                    print(f"У році {year} знайдено папок: {len(expertise_folders)}")
                    
                    for expertise_folder in expertise_folders:
                        expertise_folder_path = os.path.join(year_path, expertise_folder)
                        
                        if not os.path.isdir(expertise_folder_path):
                            print(f"Пропускаємо файл у році {year}: {expertise_folder}")
                            continue
                        
                        print(f"Обробляємо папку експертизи: {expertise_folder}")
                        
                        # Шукаємо файли експертиз у папці
                        try:
                            files_in_expertise = os.listdir(expertise_folder_path)
                            print(f"У папці {expertise_folder} знайдено файлів: {len(files_in_expertise)}")
                            
                            for file_name in files_in_expertise:
                                file_path = os.path.join(expertise_folder_path, file_name)
                                
                                if not os.path.isfile(file_path):
                                    continue
                                
                                # Перевіряємо розширення файлу
                                file_ext = os.path.splitext(file_name)[1].lower()
                                if file_ext not in ['.doc', '.docx']:
                                    print(f"Пропускаємо файл з невідповідним розширенням: {file_name}")
                                    continue
                                
                                print(f"Знайдено файл експертизи: {file_name}")
                                
                                found_files.append({
                                    'file_path': file_path,
                                    'expert_name': expert_name,
                                    'year': year,
                                    'expertise_number': expertise_folder,
                                    'file_name': file_name
                                })
                        
                        except Exception as e:
                            error_msg = f"Помилка обробки папки {expertise_folder}: {str(e)}"
                            print(error_msg)
                            errors.append(error_msg)
                
                except Exception as e:
                    error_msg = f"Помилка обробки року {year}: {str(e)}"
                    print(error_msg)
                    errors.append(error_msg)
        
        except Exception as e:
            error_msg = f"Помилка сканування папки експерта: {str(e)}"
            print(error_msg)
            return False, error_msg, []
        
        print(f"Всього знайдено файлів: {len(found_files)}")
        
        if not found_files:
            error_details = "\n".join(errors) if errors else "Перевірте структуру папки"
            return False, f"У папці експерта не знайдено файлів у правильній структурі.\n{error_details}", []
        
        return True, f"Знайдено {len(found_files)} файлів", found_files
    
    def process_expert_files(self, found_files, sector_code):
        """Обробка файлів експерта з певного сектору"""
        results = []
        
        for file_info in found_files:
            try:
                success, message = self.add_document(
                    file_path=file_info['file_path'],
                    sector_code=sector_code,
                    expert_name=file_info['expert_name'],
                    expertise_year=file_info['year'],
                    expertise_number=file_info['expertise_number']
                )
                
                results.append({
                    'file_name': file_info['file_name'],
                    'success': success,
                    'message': message
                })
                
            except Exception as e:
                results.append({
                    'file_name': file_info['file_name'],
                    'success': False,
                    'message': f"Помилка обробки: {str(e)}"
                })
        
        return results    

    def get_experts_list(self, sector_code=None):
        """Отримати список експертів в архіві з обробкою помилок"""
        conn = sqlite3.connect(self.db_path)
        try:
            # Перевірка існування таблиці та колонки
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(expertise_cases)")
            columns = [column[1] for column in cursor.fetchall()]
            
            if 'expert_name' not in columns:
                conn.close()
                return []
            
            if sector_code and 'sector' in columns:
                query = "SELECT DISTINCT expert_name FROM expertise_cases WHERE sector = ? AND expert_name IS NOT NULL ORDER BY expert_name"
                params = (sector_code,)
            else:
                query = "SELECT DISTINCT expert_name FROM expertise_cases WHERE expert_name IS NOT NULL ORDER BY expert_name"
                params = ()
            
            result = pd.read_sql_query(query, conn, params=params)
            conn.close()
            
            return result['expert_name'].tolist() if not result.empty else []
            
        except Exception as e:
            conn.close()
            return []
        
    def optimize_database(self):
        """Оптимізація бази даних для кращої продуктивності"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Аналіз таблиці для статистики
            cursor.execute("ANALYZE expertise_cases")
            
            # Перебудова індексів
            cursor.execute("REINDEX")
            
            # VACUUM для дефрагментації
            cursor.execute("VACUUM")
            
            # Оптимізація налаштувань SQLite для великих баз
            cursor.execute("PRAGMA optimize")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=10000")
            cursor.execute("PRAGMA temp_store=memory")
            
            conn.commit()
            conn.close()
            return True, "База даних оптимізована"
            
        except Exception as e:
            conn.close()
            return False, f"Помилка оптимізації: {str(e)}"

    def cleanup_cache(self, max_age_hours=24):
        """Очищення застарілого кешу"""
        if not os.path.exists(CACHE_DIR):
            return 0
        
        cleaned_count = 0
        current_time = time.time()
        max_age_seconds = max_age_hours * 3600
        
        try:
            for filename in os.listdir(CACHE_DIR):
                file_path = os.path.join(CACHE_DIR, filename)
                if os.path.isfile(file_path):
                    file_age = current_time - os.path.getmtime(file_path)
                    if file_age > max_age_seconds:
                        os.remove(file_path)
                        cleaned_count += 1
        except Exception as e:
            print(f"Помилка очищення кешу: {e}")
        
        return cleaned_count

    def get_memory_usage_info(self):
        """Інформація про використання пам'яті"""
        try:
            import psutil
            process = psutil.Process()
            memory_info = process.memory_info()
            
            return {
                'rss_mb': memory_info.rss / 1024 / 1024,  # Фізична пам'ять
                'vms_mb': memory_info.vms / 1024 / 1024,  # Віртуальна пам'ять
                'percent': process.memory_percent()
            }
        except ImportError:
            return None
    
# Спрощений Streamlit веб-інтерфейс - ВИПРАВЛЕНА ВЕРСІЯ
def main():
    if st.button("🔄 Перезавантажити систему", key="reload_system"):
        st.cache_data.clear()
        if 'agent' in st.session_state:
            del st.session_state.agent
        st.rerun()
    
    st.set_page_config(
        page_title="Архів Експертиз",
        page_icon="📁",
        layout="wide"
    )
    
    st.title("📁 Архів судових експертиз")
    
    # НОВИЙ БЛОК: Вибір режиму роботи
    st.sidebar.header("⚙️ Налаштування архіву")
    
    archive_mode = st.sidebar.radio(
        "Режим роботи:",
        ["Створити новий архів", "Підключити існуючий архів"],
        key="archive_mode"
    )
    
    if archive_mode == "Підключити існуючий архів":
        existing_archive_path = st.sidebar.text_input(
            "Шлях до існуючого архіву:",
            placeholder="D:\\Судові_експертизи\\Архів",
            key="existing_archive_input",
            help="Вкажіть папку з існуючими файлами експертиз"
        )
        
        index_only = st.sidebar.checkbox(
            "Тільки індексувати (не копіювати файли)",
            value=True,
            key="index_only_mode",
            help="Файли залишаться на своїх місцях, створюється тільки індекс"
        )
        
        if existing_archive_path and os.path.exists(existing_archive_path):
            st.sidebar.success("✅ Архів знайдено")
            
            # Показуємо інформацію про архів
            try:
                total_size = sum(os.path.getsize(os.path.join(root, file)) 
                               for root, dirs, files in os.walk(existing_archive_path) 
                               for file in files if file.lower().endswith(('.doc', '.docx', '.pdf')))
                st.sidebar.info(f"📊 Розмір архіву: {total_size / (1024**3):.1f} ГБ")
            except:
                pass
        elif existing_archive_path:
            st.sidebar.error("❌ Шлях не знайдено")
            existing_archive_path = None
    else:
        existing_archive_path = None
        index_only = False
    
    st.markdown("### Пошук за ЄРДР, номером експертизи, роком")
    
    # Ініціалізація агента з новими параметрами
    if 'agent' not in st.session_state or st.session_state.get('current_archive_path') != existing_archive_path:
        try:
            st.session_state.agent = ForensicArchiveAgent(
                existing_archive_path=existing_archive_path,
                index_only_mode=index_only
            )
            st.session_state.agent.init_cache_system()  # Ініціалізуємо кешування
            st.session_state.current_archive_path = existing_archive_path
            
            if existing_archive_path:
                st.info(f"🔗 Підключено до архіву: {existing_archive_path}")
        except Exception as e:
            st.error(f"Помилка ініціалізації: {str(e)}")
            return

    agent = st.session_state.agent

    # Додати перемикач режиму роботи
    st.sidebar.title("⚙️ Режим роботи")
    work_mode = st.sidebar.selectbox(
        "Оберіть режим:",
        ["Звичайний архів", "Індексування існуючого архіву"],
        key="work_mode"
    )

    # Налаштування для існуючого архіву
    if work_mode == "Індексування існуючого архіву":
        existing_archive_path = st.sidebar.text_input(
            "Шлях до існуючого архіву:",
            placeholder="C:\\Архів\\",
            key="existing_archive_path",
            help="Шлях до папки з існуючим архівом"
        )
        
        if existing_archive_path and not os.path.exists(existing_archive_path):
            st.sidebar.error("❌ Папка не знайдена")
            existing_archive_path = None
        
        # Переналаштовуємо агента для режиму індексування
        if existing_archive_path:
            agent.existing_archive_path = existing_archive_path
            agent.index_only_mode = True
            st.sidebar.success(f"✅ Архів: {os.path.basename(existing_archive_path)}")
    
    # Динамічні вкладки залежно від режиму
    if work_mode == "Індексування існуючого архіву" and 'existing_archive_path' in locals() and existing_archive_path:
        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            "🔍 Пошук", "📊 Індексування архіву", "📁 Структура архіву", 
            "🔧 Утиліти", "📊 Управління"
        ])
    else:
        tab1, tab2, tab3, tab4 = st.tabs([
            "🔍 Пошук", "📄 Додати файли", "📁 Пакетне завантаження", "📊 Управління"
        ])
    
    with tab1:
        st.header("Пошук експертиз")
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            erddr_search = st.text_input("№ ЄРДР:", placeholder="12345678901234567890", key="search_erddr")
        
        with col2:
            expertise_number_search = st.text_input("№ експертизи:", placeholder="123/24СЕ-456", key="search_number")
        
        with col3:
            expertise_year_search = st.number_input("Рік:", min_value=2000, max_value=2030, value=None, key="search_year")
        
        with col4:
            expert_name_search = st.text_input("Прізвище експерта:", placeholder="Іванов", key="search_expert")
        
        # Кнопка пошуку
        search_clicked = st.button("🔍 Знайти", type="primary", key="search_btn")

        col_limit, col_cache = st.columns(2)

        with col_limit:
            search_limit = st.selectbox(
                "Ліміт результатів:",
                [100, 500, 1000, 2000],
                index=1,  # 500 за замовчуванням
                key="search_limit"
            )

        with col_cache:
            use_search_cache = st.checkbox(
                "Використовувати кеш пошуку",
                value=True,
                key="use_search_cache"
            )
        
        # ВИПРАВЛЕННЯ: Ініціалізуємо results як порожній DataFrame
        results = pd.DataFrame()

        # Виконуємо пошук коли є критерії або натиснута кнопка
        if search_clicked or erddr_search or expertise_number_search or expertise_year_search or expert_name_search:
            results = agent.search_documents(
                erddr_number=erddr_search,
                expertise_number=expertise_number_search,
                expertise_year=expertise_year_search,
                expert_name=expert_name_search,
                use_cache=use_search_cache,
                limit=search_limit
            )
            
            if not results.empty:
                st.success(f"Знайдено {len(results)} експертиз")
                
                for idx, row in results.iterrows():
                    # Безпечне отримання значень
                    expertise_number = safe_get_value(row, 'expertise_number', 'Без номера')
                    expertise_year = safe_get_value(row, 'expertise_year', 'Невідомий рік')
                    expert_name = safe_get_value(row, 'expert_name', 'Невідомий експерт')
                    sector = safe_get_value(row, 'sector', 'почерк та ТЕД')
                    
                    sector_name = [k for k, v in agent.sectors.items() if v == sector]
                    sector_display = sector_name[0] if sector_name else sector
                    
                    with st.expander(f"📄 {expertise_number} ({expertise_year}) - {expert_name} - {sector_display}"):
                        col1, col2 = st.columns([3, 1])
                        
                        with col1:
                            st.write(f"**№ ЄРДР:** {safe_get_value(row, 'erddr_number', 'Не знайдено')}")
                            st.write(f"**Експерт:** {expert_name}")
                            st.write(f"**Сектор:** {sector_display}")
                            st.write(f"**Тип експертизи:** {safe_get_value(row, 'expertise_type', 'Невизначено')}")
                            st.write(f"**Дата:** {safe_get_value(row, 'expertise_date', 'Не вказано')}")
                            st.write(f"**Файл:** {safe_get_value(row, 'source_file', 'Не вказано')}")
                            st.write(f"**Шлях:** {safe_get_value(row, 'file_path', 'Не вказано')}")
                        
                        with col2:
                            # Кнопка відкриття
                            if st.button("📂 Відкрити", key=f"open_{row['id']}"):
                                success, message = agent.open_document(row['file_path'])
                                if success:
                                    st.success(message)
                                else:
                                    st.error(message)
                            
                            # Кнопка видалення з підтвердженням
                            if st.button("🗑️ Видалити", key=f"delete_{row['id']}", type="secondary"):
                                if f"confirm_delete_{row['id']}" not in st.session_state:
                                    st.session_state[f"confirm_delete_{row['id']}"] = True
                                    st.warning("Натисніть ще раз для підтвердження видалення")
                                else:
                                    success, message = agent.delete_document(row['id'])
                                    if success:
                                        st.success(message)
                                        # Оновлення сторінки
                                        st.rerun()
                                    else:
                                        st.error(message)
                                    del st.session_state[f"confirm_delete_{row['id']}"]
            else:
                st.warning("Експертизи не знайдені за вказаними критеріями")
    
    with tab2:
        st.header("📊 Індексування існуючого архіву")
        st.markdown(f"**Архів:** `{existing_archive_path}`")
        
        # Налаштування індексування
        col1, col2, col3 = st.columns(3)
        
        with col1:
            max_files = st.number_input(
                "Максимум файлів для обробки:",
                min_value=100,
                max_value=10000,
                value=1000,
                step=100,
                key="index_max_files"
            )
        
        with col2:
            skip_large = st.checkbox(
                "Пропускати великі файли (>100MB)",
                value=True,
                key="skip_large_files"
            )
        
        with col3:
            use_cache = st.checkbox(
                "Використовувати кеш",
                value=True,
                key="use_scan_cache"
            )
        
        # Ініціалізуємо стан сканування
        if 'scan_status' not in st.session_state:
            st.session_state.scan_status = None
        if 'scan_results' not in st.session_state:
            st.session_state.scan_results = None
        
        # Кнопка сканування
        if st.button("🔍 Сканувати архів", type="primary", key="scan_archive_btn"):
            st.session_state.scan_status = "scanning"
            
            # Контейнери для прогресу
            progress_container = st.container()
            results_container = st.container()
            
            with progress_container:
                progress_bar = st.progress(0)
                status_text = st.empty()
                file_counter = st.empty()
            
            # Функція callback для прогресу
            def progress_callback(files_count, current_file):
                progress = min(files_count / max_files, 1.0) if max_files > 0 else 0
                progress_bar.progress(progress)
                status_text.text(f"Сканування: {current_file}")
                file_counter.text(f"Знайдено файлів: {files_count}")
            
            # Виконання сканування
            try:
                found_files, errors = agent.scan_existing_archive(
                    existing_archive_path,
                    progress_callback=progress_callback,
                    file_limit=max_files,
                    skip_large_files=skip_large
                )
                
                st.session_state.scan_results = {
                    'found_files': found_files,
                    'errors': errors,
                    'total_files': len(found_files)
                }
                st.session_state.scan_status = "completed"
                
                progress_bar.progress(1.0)
                status_text.text("Сканування завершено!")
                
            except Exception as e:
                st.error(f"Помилка сканування: {str(e)}")
                st.session_state.scan_status = "error"
        
        # Показ результатів сканування
        if st.session_state.scan_results:
            results = st.session_state.scan_results
            
            st.markdown("---")
            st.subheader("📋 Результати сканування")
            
            # Статистика
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.metric("📄 Всього файлів", results['total_files'])
            
            with col2:
                pdf_count = sum(1 for f in results['found_files'] if f['file_ext'] == '.pdf')
                st.metric("📕 PDF файлів", pdf_count)
            
            with col3:
                doc_count = sum(1 for f in results['found_files'] if f['file_ext'] in ['.doc', '.docx'])
                st.metric("📘 DOC файлів", doc_count)
            
            with col4:
                st.metric("⚠️ Помилок", len(results['errors']))
            
            # Детальна інформація
            with st.expander("📁 Розподіл по папках", expanded=False):
                # Групування по директоріях
                dir_stats = {}
                for file_info in results['found_files']:
                    dir_name = file_info['directory']
                    if dir_name not in dir_stats:
                        dir_stats[dir_name] = {'count': 0, 'extensions': {}}
                    dir_stats[dir_name]['count'] += 1
                    ext = file_info['file_ext']
                    dir_stats[dir_name]['extensions'][ext] = dir_stats[dir_name]['extensions'].get(ext, 0) + 1
                
                # Показуємо топ-20 папок
                sorted_dirs = sorted(dir_stats.items(), key=lambda x: x[1]['count'], reverse=True)[:20]
                
                for dir_name, stats in sorted_dirs:
                    ext_info = ", ".join([f"{ext}: {count}" for ext, count in stats['extensions'].items()])
                    st.write(f"📁 **{dir_name}**: {stats['count']} файлів ({ext_info})")
            
            # Показ помилок
            if results['errors']:
                with st.expander("⚠️ Помилки сканування", expanded=False):
                    for error in results['errors'][:50]:  # Показуємо перші 50 помилок
                        st.text(f"• {error}")
                    if len(results['errors']) > 50:
                        st.text(f"... та ще {len(results['errors']) - 50} помилок")
            
            # Кнопка індексування
            st.markdown("---")
            st.subheader("🚀 Індексування файлів")
            
            if results['total_files'] > 0:
                st.info(f"Готово до індексування: {results['total_files']} файлів")
                
                col1, col2 = st.columns([1, 2])
                
                with col1:
                    start_indexing = st.button(
                        "🚀 Почати індексування", 
                        type="primary", 
                        key="start_indexing_btn"
                    )
                
                with col2:
                    st.warning("⚠️ Індексування може зайняти багато часу для великих архівів")
                
                # Процес індексування
                if start_indexing:
                    st.markdown("---")
                    st.subheader("📤 Процес індексування")
                    
                    index_progress = st.progress(0)
                    index_status = st.empty()
                    index_stats = st.empty()
                    
                    # Виконуємо індексування батчами
                    batch_size = 50  # Обробляємо по 50 файлів за раз
                    total_files = len(results['found_files'])
                    success_count = 0
                    error_count = 0
                    processed_count = 0
                    
                    for i in range(0, total_files, batch_size):
                        batch_files = results['found_files'][i:i+batch_size]
                        
                        for file_info in batch_files:
                            index_status.text(f"Індексування: {file_info['file_name']}")
                            
                            try:
                                # Визначаємо параметри з шляху
                                sector_code = agent.determine_sector_from_path(file_info['file_path'])
                                expert_name = agent.extract_expert_from_path(file_info['file_path'])
                                year = agent.extract_year_from_path(file_info['file_path'])
                                
                                # Додаємо до індексу
                                success, message = agent.add_document(
                                    file_path=file_info['file_path'],
                                    sector_code=sector_code,
                                    expert_name=expert_name,
                                    expertise_year=year
                                )
                                
                                if success:
                                    success_count += 1
                                else:
                                    error_count += 1
                                    # Логуємо помилку (не показуємо всі в інтерфейсі)
                                    print(f"Помилка індексування {file_info['file_name']}: {message}")
                                
                            except Exception as e:
                                error_count += 1
                                print(f"Виключення при індексуванні {file_info['file_name']}: {str(e)}")
                            
                            processed_count += 1
                            
                            # Оновлюємо прогрес
                            progress = processed_count / total_files
                            index_progress.progress(progress)
                            index_stats.text(f"Оброблено: {processed_count}/{total_files} | Успішно: {success_count} | Помилок: {error_count}")
                        
                        # Невелика пауза між батчами для зменшення навантаження
                        time.sleep(0.1)
                    
                    # Завершення індексування
                    index_status.text("Індексування завершено!")
                    index_progress.progress(1.0)
                    
                    # Підсумкова статистика
                    st.success(f"✅ Індексування завершено!")
                    
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.success(f"**Успішно:** {success_count}")
                    with col2:
                        if error_count > 0:
                            st.error(f"**Помилок:** {error_count}")
                        else:
                            st.info("**Помилок:** 0")
                    with col3:
                        st.info(f"**Всього:** {processed_count}")
                    
                    if success_count > 0:
                        st.balloons()
            else:
                st.warning("Файли для індексування не знайдено")
    
    with tab3:
        st.header("Пакетне завантаження з папки експерта")
        st.markdown("**Структура папки експерта:** `Прізвище_Експерта/Рік/Номер_Експертизи/*.doc(x)`")
        
        # Спочатку вибираємо сектор
        col1, col2 = st.columns(2)
        
        with col1:
            selected_sector_batch = st.selectbox(
                "Оберіть сектор:",
                options=list(agent.sectors.values()),
                key="batch_sector_select",
                help="Виберіть сектор, до якого належить експерт"
            )
        
        with col2:
            # Показуємо назву сектора для зручності
            sector_name = [k for k, v in agent.sectors.items() if v == selected_sector_batch]
            sector_display = sector_name[0] if sector_name else selected_sector_batch
            st.info(f"**Сектор:** {sector_display}")
        
        # Потім вказуємо папку з прізвищем експерта
        expert_folder_path = st.text_input(
            "Шлях до папки з прізвищем експерта:", 
            placeholder="C:\\Архів\\Іванов_І_І\\",
            key="expert_folder_path",
            help="Папка повинна містити підпапки з роками, а в них - папки з номерами експертиз"
        )
        
        # Показуємо приклад структури
        with st.expander("📁 Приклад правильної структури папки"):
            st.code("""
Іванов_І_І/
├── 2024/
│   ├── 123_24СЕ-456/
│   │   ├── висновок.docx
│   │   └── додаток.doc
│   ├── 124_24СЕ_457/
│   │   └── експертиза.docx
│   └── ...
├── 2023/
│   ├── 089_23СЕ_234/
│   │   └── висновок.doc
│   └── ...
└── ...
            """)
        
        # Ініціалізуємо session_state для збереження результатів сканування
        if 'scan_results' not in st.session_state:
            st.session_state.scan_results = None
        if 'found_files' not in st.session_state:
            st.session_state.found_files = []
        
        # Кнопка сканування
        if expert_folder_path and st.button("📁 Сканувати папку експерта", key="scan_expert_folder"):
            if not os.path.exists(expert_folder_path):
                st.error("❌ Папка не знайдена")
                st.session_state.scan_results = None
                st.session_state.found_files = []
            else:
                # Показуємо індикатор завантаження
                with st.spinner("Сканування папки..."):
                    # Отримуємо прізвище експерта з назви папки
                    expert_name = os.path.basename(expert_folder_path.rstrip('/\\'))
                    
                    success, result, found_files = agent.batch_add_from_expert_folder(expert_folder_path)
                    
                    # Зберігаємо результати в session_state
                    st.session_state.scan_results = {
                        'success': success,
                        'result': result,
                        'expert_name': expert_name,
                        'sector_display': sector_display,
                        'selected_sector_batch': selected_sector_batch
                    }
                    st.session_state.found_files = found_files
        
        # Показуємо результати сканування
        if st.session_state.scan_results:
            scan_data = st.session_state.scan_results
            found_files = scan_data.get("found_files", [])

            if not found_files:
                st.error("❌ Не знайдено жодного файлу в архіві")
            else:
                st.success(f"✅ Знайдено файлів: {len(found_files)}")
                st.info(f"**Експерт:** {scan_data.get('expert_name', 'Невідомо')}")
                st.info(f"**Сектор:** {scan_data.get('sector_display', 'Невідомо')}")
                
                # Показуємо список файлів з групуванням по роках
                with st.expander("📋 Список знайдених файлів", expanded=True):
                    # Групуємо файли по роках
                    files_by_year = {}
                    for file_info in found_files:
                        year = file_info['year']
                        if year not in files_by_year:
                            files_by_year[year] = []
                        files_by_year[year].append(file_info)
                    
                    # Показуємо по роках
                    for year in sorted(files_by_year.keys(), reverse=True):
                        st.write(f"**{year} рік ({len(files_by_year[year])} файлів):**")
                        
                        # Показуємо всі файли року або обмежуємо до 10
                        files_to_show = files_by_year[year][:10]
                        for file_info in files_to_show:
                            st.write(f"  • `{file_info['expertise_number']}`: {file_info['file_name']}")
                        
                        if len(files_by_year[year]) > 10:
                            st.write(f"  ... та ще {len(files_by_year[year]) - 10} файлів")
                
                # Кнопка для завантаження всіх файлів
                st.markdown("---")
                st.write(f"**Готово до завантаження:** {len(found_files)} файлів в сектор '{scan_data['sector_display']}'")
                
                col1, col2 = st.columns([1, 3])
                
                with col1:
                    start_upload = st.button("🚀 Завантажити всі файли", type="primary", key="batch_upload_all")
                
                with col2:
                    st.warning("⚠️ Це додасть всі знайдені файли в архів. Переконайтеся, що структура папки правильна.")
                
                # Процес завантаження
                if start_upload:
                    st.markdown("---")
                    st.subheader("📤 Процес завантаження")
                    
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    # Використовуємо метод process_expert_files
                    results = agent.process_expert_files(found_files, scan_data['selected_sector_batch'])
                    
                    success_count = 0
                    error_count = 0
                    error_details = []
                    
                    for i, result in enumerate(results):
                        status_text.text(f"Обробка файлу {i + 1} з {len(results)}: {result['file_name']}")
                        
                        if result['success']:
                            st.success(f"✅ {result['file_name']}: {result['message']}")
                            success_count += 1
                        else:
                            st.error(f"❌ {result['file_name']}: {result['message']}")
                            error_count += 1
                            error_details.append(f"{result['file_name']}: {result['message']}")
                        
                        progress_bar.progress((i + 1) / len(results))
                    
                    status_text.text("Пакетне завантаження завершено!")
                    progress_bar.progress(1.0)
                    
                    # Підсумок
                    st.markdown("### 📊 Результати завантаження")
                    
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.success(f"**Успішно:** {success_count}")
                    with col2:
                        if error_count > 0:
                            st.error(f"**Помилок:** {error_count}")
                        else:
                            st.info("**Помилок:** 0")
                    with col3:
                        st.info(f"**Всього:** {len(results)}")
                    
                    # Показуємо деталі помилок
                    if error_details:
                        with st.expander("❌ Деталі помилок"):
                            for error in error_details:
                                st.text(f"• {error}")
                    
                    # Очищаємо результати сканування після завантаження
                    if success_count > 0:
                        st.balloons()
                        if st.button("🔄 Очистити результати", key="clear_scan_results"):
                            st.session_state.scan_results = None
                            st.session_state.found_files = []
                            st.rerun()

        if existing_archive_path:
            with tab4:  # Вкладка індексування
                st.header("🗂️ Індексування існуючого архіву")
                st.info(f"**Робочий архів:** {existing_archive_path}")
                
                # Налаштування сканування
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    scan_limit = st.number_input(
                        "Максимум файлів для сканування:",
                        min_value=100,
                        max_value=50000,
                        value=5000,
                        step=100,
                        key="scan_file_limit",
                        help="Обмеження для запобігання перевантаженню"
                    )
                
                with col2:
                    skip_large = st.checkbox(
                        "Пропускати файли >100MB",
                        value=True,
                        key="skip_large_files_2",
                        help="Великі файли можуть сповільнити обробку"
                    )
                
                with col3:
                    use_cache = st.checkbox(
                        "Використовувати кеш сканування",
                        value=True,
                        key="use_scan_cache_2",
                        help="Прискорює повторне сканування"
                    )
                
                # Кнопка сканування архіву
                if st.button("🔍 Сканувати архів", type="primary", key="scan_existing_archive"):
                    
                    # Контейнери для прогресу
                    progress_container = st.container()
                    results_container = st.container()
                    
                    with progress_container:
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        file_counter = st.empty()
                    
                    # Функція для відображення прогресу
                    def progress_callback(processed_count, current_file):
                        status_text.text(f"Сканування: {current_file}")
                        file_counter.text(f"Оброблено файлів: {processed_count}")
                        if scan_limit > 0:
                            progress_bar.progress(min(processed_count / scan_limit, 1.0))
                    
                    # Запуск сканування
                    try:
                        found_files, errors = agent.scan_existing_archive(
                            existing_archive_path,
                            progress_callback=progress_callback,
                            file_limit=scan_limit,
                            skip_large_files=skip_large
                        )
                        
                        progress_bar.progress(1.0)
                        status_text.text("Сканування завершено!")
                        
                        with results_container:
                            if found_files:
                                st.success(f"✅ Знайдено {len(found_files)} файлів для індексування")
                                
                                # Статистика по типах файлів
                                file_stats = {}
                                total_size = 0
                                
                                for file_info in found_files:
                                    ext = file_info['file_ext']
                                    file_stats[ext] = file_stats.get(ext, 0) + 1
                                    total_size += file_info.get('file_size', 0)
                                
                                col1, col2, col3 = st.columns(3)
                                with col1:
                                    st.metric("Всього файлів", len(found_files))
                                with col2:
                                    st.metric("Загальний розмір", f"{total_size / (1024**2):.1f} MB")
                                with col3:
                                    st.write("**По типах:**")
                                    for ext, count in file_stats.items():
                                        st.write(f"• {ext}: {count}")
                                
                                # Попередній перегляд файлів
                                with st.expander("📋 Попередній перегляд файлів (перші 20)", expanded=False):
                                    for i, file_info in enumerate(found_files[:20]):
                                        rel_path = file_info['relative_path']
                                        size_mb = file_info['file_size'] / (1024**2)
                                        st.write(f"{i+1}. `{rel_path}` ({size_mb:.1f} MB)")
                                    
                                    if len(found_files) > 20:
                                        st.write(f"... та ще {len(found_files) - 20} файлів")
                                
                                # Кнопка індексування
                                st.markdown("---")
                                st.write("**Готово до індексування в базу даних**")
                                
                                col1, col2 = st.columns([1, 2])
                                with col1:
                                    start_indexing = st.button(
                                        "🚀 Почати індексування", 
                                        type="primary", 
                                        key="start_batch_indexing"
                                    )
                                with col2:
                                    st.warning("⚠️ Процес може зайняти кілька хвилин для великих архівів")
                                
                                # Процес індексування
                                if start_indexing:
                                    st.markdown("---")
                                    st.subheader("📤 Індексування архіву")
                                    
                                    index_progress = st.progress(0)
                                    index_status = st.empty()
                                    
                                    # Функція прогресу для індексування
                                    def index_progress_callback(current, total, filename):
                                        index_status.text(f"Індексування {current}/{total}: {filename}")
                                        index_progress.progress(current / total)
                                    
                                    # Запуск індексування
                                    results = agent.index_existing_archive_batch(
                                        existing_archive_path,
                                        progress_callback=index_progress_callback
                                    )
                                    
                                    # Підрахунок результатів
                                    success_count = sum(1 for r in results if r['success'])
                                    error_count = len(results) - success_count
                                    
                                    index_progress.progress(1.0)
                                    index_status.text("Індексування завершено!")
                                    
                                    # Результати
                                    col1, col2, col3 = st.columns(3)
                                    with col1:
                                        st.success(f"**Успішно:** {success_count}")
                                    with col2:
                                        if error_count > 0:
                                            st.error(f"**Помилок:** {error_count}")
                                        else:
                                            st.info("**Помилок:** 0")
                                    with col3:
                                        st.info(f"**Всього:** {len(results)}")
                                    
                                    # Деталі помилок
                                    if error_count > 0:
                                        with st.expander("❌ Деталі помилок"):
                                            for result in results:
                                                if not result['success']:
                                                    st.text(f"• {result['file_path']}: {result['message']}")
                                    
                                    if success_count > 0:
                                        st.balloons()
                            
                            else:
                                st.warning("Файли для індексування не знайдено")
                            
                            # Показуємо помилки сканування
                            if errors:
                                with st.expander(f"⚠️ Помилки сканування ({len(errors)})"):
                                    for error in errors:
                                        st.text(f"• {error}")
                    
                    except Exception as e:
                        st.error(f"Помилка сканування архіву: {str(e)}")
    
    with tab4:  # Вкладка утиліт для режиму індексування
        st.header("🔧 Утиліти та оптимізація")
        
        # Секція оптимізації бази даних
        st.subheader("💾 Оптимізація бази даних")
        
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("🚀 Оптимізувати базу даних", key="optimize_db"):
                with st.spinner("Оптимізація..."):
                    success, message = agent.optimize_database()
                if success:
                    st.success(message)
                else:
                    st.error(message)
        
        with col2:
            if st.button("🧹 Очистити кеш", key="cleanup_cache"):
                cleaned = agent.cleanup_cache()
                st.success(f"Видалено {cleaned} застарілих файлів кешу")
        
        # Інформація про продуктивність
        st.subheader("📊 Моніторинг продуктивності")
        
        # Розмір бази даних
        if os.path.exists(agent.db_path):
            db_size = os.path.getsize(agent.db_path) / (1024 * 1024)  # MB
            st.metric("💾 Розмір бази даних", f"{db_size:.2f} MB")
        
        # Інформація про кеш
        cache_files = 0
        cache_size = 0
        if os.path.exists(CACHE_DIR):
            for file in os.listdir(CACHE_DIR):
                file_path = os.path.join(CACHE_DIR, file)
                if os.path.isfile(file_path):
                    cache_files += 1
                    cache_size += os.path.getsize(file_path)
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("📋 Файлів кешу", cache_files)
        with col2:
            st.metric("💿 Розмір кешу", f"{cache_size / (1024 * 1024):.2f} MB")
        
        # Використання пам'яті (якщо доступне)
        memory_info = agent.get_memory_usage_info()
        if memory_info:
            st.subheader("🧠 Використання пам'яті")
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.metric("RAM (фізична)", f"{memory_info['rss_mb']:.1f} MB")
            with col2:
                st.metric("RAM (віртуальна)", f"{memory_info['vms_mb']:.1f} MB")
            with col3:
                st.metric("Процент від системи", f"{memory_info['percent']:.1f}%")
        
        # Секція налаштувань
        st.subheader("⚙️ Налаштування")
        
        # Налаштування лімітів
        new_max_file_size = st.slider(
            "Максимальний розмір файлу (MB):",
            min_value=10,
            max_value=500,
            value=MAX_FILE_SIZE_MB,
            key="max_file_size_setting"
        )
        
        if new_max_file_size != MAX_FILE_SIZE_MB:
            st.info(f"Новий ліміт: {new_max_file_size} MB (застосується після перезапуску)")
        
        # Налаштування кешу
        cache_ttl_hours = st.slider(
            "Час життя кешу (години):",
            min_value=1,
            max_value=168,  # Тиждень
            value=int(FILE_SCAN_CACHE_TTL / 3600),
            key="cache_ttl_setting"
        )
        
        # Експорт/імпорт налаштувань
        st.subheader("📤 Експорт/Імпорт")
        
        if st.button("📤 Експортувати список файлів", key="export_files"):
            try:
                # Експорт списку всіх файлів з бази
                df = agent.search_documents(limit=10000)
                if not df.empty:
                    csv = df.to_csv(index=False)
                    st.download_button(
                        label="💾 Завантажити CSV",
                        data=csv.encode('utf-8'),
                        file_name=f"archive_files_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.warning("Немає даних для експорту")
            except Exception as e:
                st.error(f"Помилка експорту: {str(e)}")

    
    with tab4:
        st.header("Управління архівом")                             
        
        # Інформація про режим роботи
        if existing_archive_path:
            st.info(f"🔗 **Режим:** Підключений архів ({existing_archive_path})")
            if index_only:
                st.info("📋 **Тип:** Тільки індексування (файли не копіюються)")
            else:
                st.info("📁 **Тип:** Копіювання та індексування")
        else:
            st.info("🆕 **Режим:** Новий архів")
        
        # Кнопки управління кешем
        st.subheader("🗄️ Управління кешем")
        col1, col2, col3 = st.columns(3)
        
        with col1:
            if st.button("🧹 Очистити кеш пошуку", key="clear_search_cache"):
                try:
                    cache_files = [f for f in os.listdir(CACHE_DIR) if f.startswith('search_')]
                    for cache_file in cache_files:
                        os.remove(os.path.join(CACHE_DIR, cache_file))
                    st.success(f"Видалено {len(cache_files)} файлів кешу пошуку")
                except Exception as e:
                    st.error(f"Помилка очистки кешу: {e}")
        
        with col2:
            if st.button("🗂️ Очистити кеш сканування", key="clear_scan_cache"):
                try:
                    cache_files = [f for f in os.listdir(CACHE_DIR) if f.startswith('file_scan_')]
                    for cache_file in cache_files:
                        os.remove(os.path.join(CACHE_DIR, cache_file))
                    st.success(f"Видалено {len(cache_files)} файлів кешу сканування")
                except Exception as e:
                    st.error(f"Помилка очистки кешу: {e}")
        
        with col3:
            if st.button("💫 Очистити весь кеш", key="clear_all_cache"):
                try:
                    if os.path.exists(CACHE_DIR):
                        import shutil
                        shutil.rmtree(CACHE_DIR)
                        os.makedirs(CACHE_DIR)
                    st.success("Весь кеш очищено")
                    st.rerun()
                except Exception as e:
                    st.error(f"Помилка очистки кешу: {e}")
        
        # Статистика
        total_docs, by_type, by_year = agent.get_archive_statistics()
        
        # Основна статистика
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.metric("📄 Всього експертиз", total_docs)
        
        with col2:
            current_year = datetime.now().year
            current_year_count = by_year[by_year['expertise_year'] == current_year]['count'].sum() if not by_year.empty and any(by_year['expertise_year'] == current_year) else 0
            st.metric(f"📅 За {current_year} рік", int(current_year_count))
        
        with col3:
            if not by_type.empty:
                most_common_type = by_type.iloc[0]['expertise_type']
                most_common_count = by_type.iloc[0]['count']
                st.metric("🔝 Найчастіший тип", f"{most_common_type} ({most_common_count})")
        
        # Детальна статистика
        col1, col2 = st.columns(2)
        
        with col1:
            if not by_type.empty:
                st.subheader("📊 За типами експертиз:")
                for _, row in by_type.iterrows():
                    percentage = (row['count'] / total_docs * 100) if total_docs > 0 else 0
                    st.write(f"• **{row['expertise_type']}**: {row['count']} ({percentage:.1f}%)")
            else:
                st.info("Немає даних про типи експертиз")
        
        with col2:
            if not by_year.empty:
                st.subheader("📅 За роками:")
                for _, row in by_year.head(10).iterrows():  # Показуємо останні 10 років
                    percentage = (row['count'] / total_docs * 100) if total_docs > 0 else 0
                    st.write(f"• **{int(row['expertise_year'])}**: {row['count']} ({percentage:.1f}%)")
            else:
                st.info("Немає даних про роки")
        
        st.markdown("---")
        
        # Структура архіву
        st.subheader("📁 Структура архіву")
        st.code(f"""
archive/
├── почерк та ТЕД/    # Почеркознавчі дослідження
├── балісти/            # Дослідження зброї
├── трасологія/          # Трасологічні дослідження
├── дактилоскопія/        # Дактилоскопічні дослідження
└── бал. облік/         # Балістичний облік

Кожен сектор містить:
├── 2024/
│   ├── експертиза_1/
│   ├── експертиза_2/
│   └── ...
├── 2023/
└── ...
        """)
        
        # Налаштування
        st.markdown("---")
        st.subheader("⚙️ Налаштування")
        
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("🗂️ Перевірити структуру архіву", key="check_structure"):
                try:
                    agent.create_archive_structure()
                    st.success("Структура архіву перевірена та створена")
                except Exception as e:
                    st.error(f"Помилка: {str(e)}")
        
        with col2:
            if st.button("📊 Оновити статистику", key="refresh_stats"):
                st.rerun()
        
        # Інформація про базу даних
        st.markdown("---")
        st.subheader("💾 Інформація про базу даних")
        
        try:
            db_size = os.path.getsize(agent.db_path) if os.path.exists(agent.db_path) else 0
            db_size_mb = db_size / (1024 * 1024)
            st.write(f"• **Розмір бази даних**: {db_size_mb:.2f} MB")
            st.write(f"• **Шлях до бази**: {agent.db_path}")
            st.write(f"• **Шлях до архіву**: {agent.archive_folder}")
        except Exception as e:
            st.error(f"Помилка отримання інформації про базу: {str(e)}")

if __name__ == "__main__":
    main()    
