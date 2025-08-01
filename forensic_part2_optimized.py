# =============================================================================
    # МЕТОДИ РОБОТИ З ДОКУМЕНТАМИ (ОПТИМІЗОВАНІ)
    # =============================================================================
    
    @lru_cache(maxsize=500)
    def _get_file_hash(self, file_path, file_size):
        """Оптимізоване обчислення хешу файлу з кешуванням"""
        try:
            hash_md5 = hashlib.md5()
            with open(file_path, "rb") as f:
                # Читаємо файл блоками для економії пам'яті
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            print(f"Помилка обчислення хешу для {file_path}: {e}")
            return None
    
    def extract_docx_content(self, file_path, max_size_mb=MAX_FILE_SIZE_MB):
        """
        Оптимізоване витягування тексту з DOCX файлів
        
        Args:
            file_path: Шлях до файлу
            max_size_mb: Максимальний розмір файлу для обробки
            
        Returns:
            tuple: (успіх: bool, текст: str, помилка: str)
        """
        try:
            # Перевірка розміру файлу
            file_size_mb = get_file_size_mb(file_path)
            if file_size_mb > max_size_mb:
                return False, "", f"Файл занадто великий: {file_size_mb:.1f}MB > {max_size_mb}MB"
            
            # Перевірка кешу за хешем файлу
            file_hash = self._get_file_hash(file_path, int(file_size_mb * 1024 * 1024))
            if file_hash:
                cache_key = f"docx_content_{file_hash}"
                cached_content = self._load_content_cache(cache_key)
                if cached_content is not None:
                    return True, cached_content, ""
            
            # Витягування тексту
            doc = Document(file_path)
            
            # Оптимізований збір тексту з усіх параграфів
            paragraphs = []
            for paragraph in doc.paragraphs:
                text = paragraph.text.strip()
                if text:  # Пропускаємо порожні параграфи
                    paragraphs.append(text)
            
            # Об'єднання тексту з оптимізацією пам'яті
            content = '\n'.join(paragraphs)
            
            # Збереження в кеш якщо є хеш
            if file_hash and content:
                self._save_content_cache(cache_key, content)
            
            return True, content, ""
            
        except Exception as e:
            error_msg = f"Помилка читання DOCX {file_path}: {str(e)}"
            print(error_msg)
            return False, "", error_msg
    
    def extract_pdf_content(self, file_path, max_size_mb=MAX_FILE_SIZE_MB):
        """
        Оптимізоване витягування тексту з PDF файлів
        Використовує pdfplumber як основний метод, PyPDF2 як резервний
        
        Args:
            file_path: Шлях до файлу
            max_size_mb: Максимальний розмір файлу для обробки
            
        Returns:
            tuple: (успіх: bool, текст: str, помилка: str)
        """
        if not PDF_AVAILABLE:
            return False, "", "PDF бібліотеки не встановлені"
        
        try:
            # Перевірка розміру файлу
            file_size_mb = get_file_size_mb(file_path)
            if file_size_mb > max_size_mb:
                return False, "", f"PDF файл занадто великий: {file_size_mb:.1f}MB > {max_size_mb}MB"
            
            # Перевірка кешу
            file_hash = self._get_file_hash(file_path, int(file_size_mb * 1024 * 1024))
            if file_hash:
                cache_key = f"pdf_content_{file_hash}"
                cached_content = self._load_content_cache(cache_key)
                if cached_content is not None:
                    return True, cached_content, ""
            
            # Спробуємо pdfplumber (краще якість тексту)
            success, content, error = self._extract_pdf_pdfplumber(file_path)
            
            # Якщо pdfplumber не спрацював, спробуємо PyPDF2
            if not success:
                success, content, error = self._extract_pdf_pypdf2(file_path)
            
            # Збереження в кеш при успіху
            if success and file_hash and content:
                self._save_content_cache(cache_key, content)
            
            return success, content, error
            
        except Exception as e:
            error_msg = f"Критична помилка читання PDF {file_path}: {str(e)}"
            print(error_msg)
            return False, "", error_msg
    
    def _extract_pdf_pdfplumber(self, file_path):
        """Витягування тексту за допомогою pdfplumber"""
        try:
            import pdfplumber
            
            pages_text = []
            with pdfplumber.open(file_path) as pdf:
                # Обмежуємо кількість сторінок для великих файлів
                max_pages = min(len(pdf.pages), 100)  
                
                for i, page in enumerate(pdf.pages[:max_pages]):
                    try:
                        text = page.extract_text()
                        if text and text.strip():
                            pages_text.append(text.strip())
                    except Exception as page_error:
                        print(f"Помилка читання сторінки {i+1}: {page_error}")
                        continue
            
            content = '\n\n'.join(pages_text)
            return True, content, ""
            
        except Exception as e:
            return False, "", f"pdfplumber помилка: {str(e)}"
    
    def _extract_pdf_pypdf2(self, file_path):
        """Резервне витягування тексту за допомогою PyPDF2"""
        try:
            import PyPDF2
            
            pages_text = []
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                
                # Обмежуємо кількість сторінок
                max_pages = min(len(pdf_reader.pages), 100)
                
                for i in range(max_pages):
                    try:
                        page = pdf_reader.pages[i]
                        text = page.extract_text()
                        if text and text.strip():
                            pages_text.append(text.strip())
                    except Exception as page_error:
                        print(f"PyPDF2: помилка сторінки {i+1}: {page_error}")
                        continue
            
            content = '\n\n'.join(pages_text)
            return True, content, ""
            
        except Exception as e:
            return False, "", f"PyPDF2 помилка: {str(e)}"
    
    def _save_content_cache(self, cache_key, content):
        """Збереження контенту в кеш з компресією"""
        if not self._cache_initialized:
            return False
            
        cache_file = os.path.join(CACHE_DIR, f"content_{cache_key}.pkl")
        try:
            # Компресуємо великі тексти
            if len(content) > 10000:
                import gzip
                with gzip.open(cache_file + '.gz', 'wb') as f:
                    pickle.dump(content, f, protocol=pickle.HIGHEST_PROTOCOL)
            else:
                with open(cache_file, 'wb') as f:
                    pickle.dump(content, f, protocol=pickle.HIGHEST_PROTOCOL)
            
            self.cache_timestamps[cache_key] = time.time()
            return True
        except Exception as e:
            print(f"Помилка збереження контенту в кеш: {e}")
            return False
    
    def _load_content_cache(self, cache_key):
        """Завантаження контенту з кешу з розпаковкою"""
        if not self._cache_initialized:
            return None
            
        cache_file = os.path.join(CACHE_DIR, f"content_{cache_key}.pkl")
        cache_file_gz = cache_file + '.gz'
        
        try:
            # Перевірка актуальності кешу
            if not self.is_cache_valid(cache_key):
                return None
            
            # Спробуємо завантажити стиснений файл
            if os.path.exists(cache_file_gz):
                import gzip
                with gzip.open(cache_file_gz, 'rb') as f:
                    return pickle.load(f)
            
            # Або звичайний файл
            if os.path.exists(cache_file):
                with open(cache_file, 'rb') as f:
                    return pickle.load(f)
                    
            return None
            
        except Exception as e:
            print(f"Помилка завантаження контенту з кешу: {e}")
            # Видаляємо пошкоджені файли
            for f in [cache_file, cache_file_gz]:
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except:
                    pass
            return None

    # =============================================================================
    # ПАРСИНГ ЕКСПЕРТИЗ (ОПТИМІЗОВАНИЙ)
    # =============================================================================
    
    def parse_expertise_document(self, file_path, content=None):
        """
        Оптимізований парсинг документа експертизи з покращеним розпізнаванням
        
        Args:
            file_path: Шлях до файлу
            content: Готовий текст (опціонально, для уникнення повторного читання)
            
        Returns:
            dict: Словник з розпізнаними даними
        """
        try:
            # Отримання контенту якщо не передано
            if content is None:
                content = self._extract_document_content(file_path)
                if not content:
                    return self._get_default_expertise_data(file_path)
            
            # Базова структура результату
            result = self._get_default_expertise_data(file_path)
            
            # Покращене розпізнавання з множинними патернами
            result.update(self._parse_erddr_patterns(content))
            result.update(self._parse_expertise_number_patterns(content))
            result.update(self._parse_date_patterns(content))
            result.update(self._parse_expert_patterns(content))
            result.update(self._determine_expertise_type(content))
            result.update(self._determine_sector(content, result.get('expertise_type', '')))
            
            # Валідація та очищення даних
            result = self._validate_and_clean_expertise_data(result)
            
            return result
            
        except Exception as e:
            print(f"Помилка парсингу {file_path}: {e}")
            return self._get_default_expertise_data(file_path)
    
    def _extract_document_content(self, file_path):
        """Витягування контенту документа з автоматичним визначенням типу"""
        file_ext = Path(file_path).suffix.lower()
        
        if file_ext == '.docx':
            success, content, _ = self.extract_docx_content(file_path)
            return content if success else ""
        elif file_ext == '.pdf':
            success, content, _ = self.extract_pdf_content(file_path)
            return content if success else ""
        else:
            return ""
    
    def _get_default_expertise_data(self, file_path):
        """Базова структура даних експертизи"""
        return {
            'erddr_number': None,
            'expertise_number': None,
            'expertise_date': None,
            'expertise_year': None,
            'expertise_type': 'невизначено',
            'expert_name': 'Невідомий_експерт',
            'sector': 'почерк та ТЕД',
            'source_file': os.path.basename(file_path),
            'file_path': file_path,
            'file_size': int(get_file_size_mb(file_path) * 1024 * 1024)
        }
    
    def _parse_erddr_patterns(self, content):
        """Покращене розпізнавання номерів ЄРДР з множинними патернами"""
        patterns = [
            r'ЄРДР\s*[№#]\s*(\d{11,12})',
            r'ЄРДР\s*(\d{11,12})',
            r'№\s*(\d{11,12})',
            r'справ[аі]\s*№\s*(\d{11,12})',
            r'провадженн[яі]\s*№\s*(\d{11,12})',
            r'(\d{11,12})\s*від',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                erddr = match.group(1)
                # Валідація довжини ЄРДР
                if 11 <= len(erddr) <= 12:
                    return {'erddr_number': erddr}
        
        return {'erddr_number': None}
    
    def _parse_expertise_number_patterns(self, content):
        """Покращене розпізнавання номерів експертиз"""
        patterns = [
            r'Експертиз[аі]\s*№\s*(\d+(?:/\d+)*)',
            r'Висновок\s*експерт[аи]\s*№\s*(\d+(?:/\d+)*)',
            r'№\s*(\d+(?:/\d+)*)\s*від\s*\d+\.\d+\.\d+',
            r'експертиз[аі]\s*(\d+(?:/\d+)*)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                number = match.group(1)
                return {'expertise_number': number}
        
        return {'expertise_number': None}
    
    def _parse_date_patterns(self, content):
        """Покращене розпізнавання дат з множинними форматами"""
        date_patterns = [
            r'від\s*(\d{1,2})\.(\d{1,2})\.(\d{4})',
            r'(\d{1,2})\.(\d{1,2})\.(\d{4})\s*р\.?',
            r'(\d{4})\s*рок[иу]',
            r'(\d{1,2})\s+(січня|лютого|березня|квітня|травня|червня|липня|серпня|вересня|жовтня|листопада|грудня)\s+(\d{4})',
        ]
        
        months_ua = {
            'січня': '01', 'лютого': '02', 'березня': '03', 'квітня': '04',
            'травня': '05', 'червня': '06', 'липня': '07', 'серпня': '08',
            'вересня': '09', 'жовтня': '10', 'листопада': '11', 'грудня': '12'
        }
        
        for pattern in date_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                groups = match.groups()
                
                if len(groups) == 3:
                    if groups[1] in months_ua:  # Українські назви місяців
                        day, month_name, year = groups
                        month = months_ua[groups[1]]
                        try:
                            date_str = f"{day.zfill(2)}.{month}.{year}"
                            return {
                                'expertise_date': date_str,
                                'expertise_year': int(year)
                            }
                        except:
                            continue
                    else:  # Цифровий формат
                        try:
                            day, month, year = groups
                            date_str = f"{day.zfill(2)}.{month.zfill(2)}.{year}"
                            return {
                                'expertise_date': date_str,
                                'expertise_year': int(year)
                            }
                        except:
                            continue
        
        return {'expertise_date': None, 'expertise_year': None}
    
    def _parse_expert_patterns(self, content):
        """Покращене розпізнавання імен експертів"""
        patterns = [
            r'Експерт[:\s]+([А-ЯІЇЄҐ][а-яіїєґ]+\s+[А-ЯІЇЄҐ]\.[А-ЯІЇЄҐ]\.)',
            r'([А-ЯІЇЄҐ][а-яіїєґ]+\s+[А-ЯІЇЄҐ]\.[А-ЯІЇЄҐ]\.)\s*(?:експерт|підпис)',
            r'Виконав[:\s]+([А-ЯІЇЄҐ][а-яіїєґ]+\s+[А-ЯІЇЄҐ]\.[А-ЯІЇЄҐ]\.)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                expert_name = match.group(1).strip()
                # Очищення та форматування імені
                expert_name = re.sub(r'\s+', '_', expert_name)
                return {'expert_name': expert_name}
        
        return {'expert_name': 'Невідомий_експерт'}
    
    def _determine_expertise_type(self, content):
        """Покращене визначення типу експертизи з вагами"""
        content_lower = content.lower()
        
        # Словник з типами експертиз та їх ключовими словами з вагами
        expertise_weights = {}
        
        for expertise_type, keywords in self.expertise_keywords.items():
            weight = 0
            for keyword in keywords:
                # Підрахунок кількості входжень з врахуванням контексту
                count = content_lower.count(keyword.lower())
                # Додаткові ваги для ключових контекстів
                if re.search(rf'{keyword.lower()}\s*(експертиз|дослідженн|висновок)', content_lower):
                    weight += count * 3
                else:
                    weight += count
            
            if weight > 0:
                expertise_weights[expertise_type] = weight
        
        if expertise_weights:
            # Повертаємо тип з найбільшою вагою
            best_type = max(expertise_weights, key=expertise_weights.get)
            return {'expertise_type': best_type}
        
        return {'expertise_type': 'невизначено'}
    
    def _determine_sector(self, content, expertise_type):
        """Визначення сектору на основі типу експертизи"""
        # Спочатку намагаємося визначити за типом експертизи
        type_to_sector = {
            'почеркознавча': 'почерк та ТЕД',
            'зброї': 'балісти',
            'трасологічна': 'трасологія',
            'дактилоскопічна': 'дактилоскопія',
            'бал. облік': 'бал. облік'
        }
        
        sector = type_to_sector.get(expertise_type, 'почерк та ТЕД')
        
        return {'sector': sector}
    
    def _validate_and_clean_expertise_data(self, data):
        """Валідація та очищення розпізнаних даних"""
        # Очищення ЄРДР
        if data.get('erddr_number'):
            erddr = re.sub(r'[^\d]', '', str(data['erddr_number']))
            if not (11 <= len(erddr) <= 12):
                data['erddr_number'] = None
            else:
                data['erddr_number'] = erddr
        
        # Очищення номера експертизи
        if data.get('expertise_number'):
            number = str(data['expertise_number']).strip()
            if not re.match(r'^\d+(/\d+)*$', number):
                data['expertise_number'] = None
        
        # Валідація року
        if data.get('expertise_year'):
            try:
                year = int(data['expertise_year'])
                current_year = datetime.now().year
                if not (2000 <= year <= current_year + 1):
                    data['expertise_year'] = None
                    data['expertise_date'] = None
            except (ValueError, TypeError):
                data['expertise_year'] = None
                data['expertise_date'] = None
        
        # Очищення імені експерта
        if data.get('expert_name'):
            name = str(data['expert_name']).strip()
            if len(name) < 3:
                data['expert_name'] = 'Невідомий_експерт'
        
        return data

    # =============================================================================
    # ПОШУК ДОКУМЕНТІВ (ОПТИМІЗОВАНИЙ)
    # =============================================================================
    
    def _search_documents_impl(self, erddr_number=None, expertise_number=None, 
                              expertise_date=None, expertise_year=None, 
                              expert_name=None, sector=None, expertise_type=None,
                              limit=1000, use_cache=True):
        """
        Оптимізований пошук документів з використанням кешу та індексів
        
        Args:
            erddr_number: Номер ЄРДР
            expertise_number: Номер експертизи
            expertise_date: Дата експертизи
            expertise_year: Рік експертизи
            expert_name: Ім'я експерта
            sector: Сектор
            expertise_type: Тип експертизи
            limit: Обмеження кількості результатів
            use_cache: Чи використовувати кеш
            
        Returns:
            pd.DataFrame: Результати пошуку
        """
        try:
            # Генерація ключа кешу
            cache_key = None
            if use_cache:
                cache_key = self.get_cache_key(
                    erddr=erddr_number, number=expertise_number, 
                    date=expertise_date, year=expertise_year,
                    expert=expert_name, sector=sector, type=expertise_type,
                    limit=limit
                )
                
                # Спроба завантаження з кешу
                cached_results = self.load_search_cache(cache_key)
                if cached_results is not None:
                    print(f"Результати завантажені з кешу ({len(cached_results)} записів)")
                    return cached_results
            
            # Побудова SQL запиту з оптимізацією
            query, params = self._build_optimized_search_query(
                erddr_number, expertise_number, expertise_date, expertise_year,
                expert_name, sector, expertise_type, limit
            )
            
            # Виконання запиту
            conn = sqlite3.connect(self.db_path)
            
            # Налаштування оптимізації для читання
            conn.execute("PRAGMA temp_store = memory")
            conn.execute("PRAGMA mmap_size = 268435456")  # 256MB
            
            results_df = pd.read_sql_query(query, conn, params=params)
            conn.close()
            
            print(f"Знайдено {len(results_df)} документів")
            
            # Збереження в кеш
            if use_cache and cache_key and not results_df.empty:
                self.save_search_cache(cache_key, results_df)
            
            return results_df
            
        except Exception as e:
            print(f"Помилка пошуку документів: {str(e)}")
            return pd.DataFrame()
    
    def _build_optimized_search_query(self, erddr_number, expertise_number, 
                                    expertise_date, expertise_year, expert_name, 
                                    sector, expertise_type, limit):
        """
        Побудова оптимізованого SQL запиту з використанням індексів
        """
        # Базовий запит
        base_query = """
            SELECT id, erddr_number, expertise_number, expertise_date, 
                   expertise_year, expertise_type, expert_name, sector,
                   source_file, file_path, file_size, created_at
            FROM expertise_cases
        """
        
        conditions = []
        params = {}
        
        # Умови пошуку з оптимізацією індексів
        if erddr_number:
            conditions.append("erddr_number = :erddr")
            params['erddr'] = erddr_number
        
        if expertise_number:
            conditions.append("expertise_number = :number")
            params['number'] = expertise_number
        
        if expertise_date:
            conditions.append("expertise_date = :date")
            params['date'] = expertise_date
        
        if expertise_year:
            conditions.append("expertise_year = :year")
            params['year'] = expertise_year
        
        if expert_name:
            conditions.append("expert_name LIKE :expert")
            params['expert'] = f"%{expert_name}%"
        
        if sector:
            conditions.append("sector = :sector")
            params['sector'] = sector
        
        if expertise_type and expertise_type != 'невизначено':
            conditions.append("expertise_type = :type")
            params['type'] = expertise_type
        
        # Збірка запиту
        if conditions:
            query = base_query + " WHERE " + " AND ".join(conditions)
        else:
            query = base_query
        
        # Сортування та обмеження
        query += " ORDER BY expertise_year DESC, expertise_date DESC, id DESC"
        
        if limit:
            query += f" LIMIT {limit}"
        
        return query, params
    
    def get_search_statistics(self):
        """Отримання статистики бази даних для оптимізації пошуку"""
        try:
            self._ensure_database_initialized()
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            stats = {}
            
            # Загальна кількість записів
            cursor.execute("SELECT COUNT(*) FROM expertise_cases")
            stats['total_records'] = cursor.fetchone()[0]
            
            # Статистика по роках
            cursor.execute("""
                SELECT expertise_year, COUNT(*) 
                FROM expertise_cases 
                WHERE expertise_year IS NOT NULL 
                GROUP BY expertise_year 
                ORDER BY expertise_year DESC
            """)
            stats['by_year'] = dict(cursor.fetchall())
            
            # Статистика по експертам
            cursor.execute("""
                SELECT expert_name, COUNT(*) 
                FROM expertise_cases 
                GROUP BY expert_name 
                ORDER BY COUNT(*) DESC 
                LIMIT 10
            """)
            stats['by_expert'] = dict(cursor.fetchall())
            
            # Статистика по секторам
            cursor.execute("""
                SELECT sector, COUNT(*) 
                FROM expertise_cases 
                GROUP BY sector 
                ORDER BY COUNT(*) DESC
            """)
            stats['by_sector'] = dict(cursor.fetchall())
            
            # Статистика по типам експертиз
            cursor.execute("""
                SELECT expertise_type, COUNT(*) 
                FROM expertise_cases 
                GROUP BY expertise_type 
                ORDER BY COUNT(*) DESC
            """)
            stats['by_type'] = dict(cursor.fetchall())
            
            conn.close()
            return stats
            
        except Exception as e:
            print(f"Помилка отримання статистики: {e}")
            return {}

    # =============================================================================
    # ДОДАВАННЯ ДОКУМЕНТІВ (ОПТИМІЗОВАНІ МЕТОДИ)
    # =============================================================================
    
    def _add_document_impl(self, file_path, force_reparse=False, **override_data):
        """
        Оптимізоване додавання документа в архів з перевіркою дублікатів
        
        Args:
            file_path: Шлях до файлу
            force_reparse: Примусовий повторний парсинг
            **override_data: Дані для перевизначення автоматично розпізнаних
            
        Returns:
            dict: Результат операції
        """
        try:
            # Перевірка існування файлу
            if not os.path.exists(file_path):
                return {'success': False, 'error': f'Файл не знайдено: {file_path}'}
            
            # Перевірка формату файлу
            if not file_path.lower().endswith(SUPPORTED_EXTENSIONS):
                return {'success': False, 'error': f'Непідтримуваний формат файлу'}
            
            # Обчислення хешу для перевірки дублікатів
            file_size = int(get_file_size_mb(file_path) * 1024 * 1024)
            file_hash = self._get_file_hash(file_path, file_size)
            
            if not force_reparse and file_hash:
                # Перевірка чи файл вже існує в базі
                existing_doc = self._check_duplicate_document(file_hash, file_path)
                if existing_doc:
                    return {
                        'success': False, 
                        'error': f'Файл вже існує в базі (ID: {existing_doc["id"]})',
                        'existing_id': existing_doc['id']
                    }
            
            # Парсинг документа
            parsed_data = self.parse_expertise_document(file_path)
            
            # Застосування перевизначених даних
            parsed_data.update(override_data)
            
            # Додавання хешу
            parsed_data['file_hash'] = file_hash
            
            # Копіювання файлу в архів (якщо не в режимі індексування)
            archive_path = file_path  # За замовчуванням залишаємо оригінальний шлях
            
            if not self.index_only_mode:
                copy_result = self._copy_file_to_archive(file_path, parsed_data)
                if copy_result['success']:
                    archive_path = copy_result['archive_path']
                else:
                    return copy_result  # Повертаємо помилку копіювання
            
            # Оновлення шляху файлу
            parsed_data['file_path'] = archive_path
            
            # Збереження в базу даних
            doc_id = self._save_document_to_database(parsed_data)
            
            if doc_id:
                # Очищення кешу пошуку після додавання
                self._invalidate_search_cache()
                
                return {
                    'success': True,
                    'document_id': doc_id,
                    'archive_path': archive_path,
                    'parsed_data': parsed_data
                }
            else:
                return {'success': False, 'error': 'Помилка збереження в базу даних'}
                
        except Exception as e:
            error_msg = f"Помилка додавання документа {file_path}: {str(e)}"
            print(error_msg)
            return {'success': False, 'error': error_msg}
    
    def _check_duplicate_document(self, file_hash, file_path):
        """Перевірка існування документа за хешем або шляхом"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Пошук за хешем (найнадійніший метод)
            if file_hash:
                cursor.execute(
                    "SELECT id, file_path, source_file FROM expertise_cases WHERE file_hash = ?",
                    (file_hash,)
                )
                result = cursor.fetchone()
                if result:
                    conn.close()
                    return {'id': result[0], 'file_path': result[1], 'source_file': result[2]}
            
            # Додатковий пошук за назвою файлу (менш надійний)
            filename = os.path.basename(file_path)
            cursor.execute(
                "SELECT id, file_path, source_file FROM expertise_cases WHERE source_file = ?",
                (filename,)
            )
            result = cursor.fetchone()
            conn.close()
            
            if result:
                return {'id': result[0], 'file_path': result[1], 'source_file': result[2]}
            
            return None
            
        except Exception as e:
            print(f"Помилка перевірки дублікатів: {e}")
            return None
    
    def _copy_file_to_archive(self, source_path, parsed_data):
        """
        Копіювання файлу в структуру архіву з генерацією унікального імені
        """
        try:
            # Визначення сектору та експерта
            sector = parsed_data.get('sector', 'почерк та ТЕД')
            expert_name = parsed_data.get('expert_name', 'Невідомий_експерт')
            expertise_year = parsed_data.get('expertise_year')
            
            # Побудова шляху до архіву
            sector_path = os.path.join(self.archive_folder, sector)
            
            # Створення папки експерта
            expert_path = os.path.join(sector_path, expert_name)
            if not ensure_directory_exists(expert_path):
                return {'success': False, 'error': f'Не вдалося створити папку експерта: {expert_path}'}
            
            # Створення папки року (якщо рік визначено)
            if expertise_year:
                year_path = os.path.join(expert_path, str(expertise_year))
                if not ensure_directory_exists(year_path):
                    print(f"Попередження: не вдалося створити папку року {expertise_year}")
                    target_dir = expert_path
                else:
                    target_dir = year_path
            else:
                target_dir = expert_path
            
            # Генерація унікального імені файлу
            original_filename = os.path.basename(source_path)
            name, ext = os.path.splitext(original_filename)
            
            # Додавання префіксу з номером експертизи якщо є
            expertise_number = parsed_data.get('expertise_number')
            if expertise_number:
                name = f"{expertise_number}_{name}"
            
            target_filename = f"{name}{ext}"
            target_path = os.path.join(target_dir, target_filename)
            
            # Обробка конфліктів імен файлів
            counter = 1
            while os.path.exists(target_path):
                target_filename = f"{name}_{counter}{ext}"
                target_path = os.path.join(target_dir, target_filename)
                counter += 1
            
            # Копіювання файлу
            shutil.copy2(source_path, target_path)
            
            return {
                'success': True,
                'archive_path': target_path,
                'relative_path': os.path.relpath(target_path, self.archive_folder)
            }
            
        except Exception as e:
            error_msg = f"Помилка копіювання файлу: {str(e)}"
            print(error_msg)
            return {'success': False, 'error': error_msg}
    
    def _save_document_to_database(self, data):
        """Збереження документа в базу даних з оптимізацією"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Підготовка даних для вставки
            insert_data = {
                'erddr_number': data.get('erddr_number'),
                'expertise_number': data.get('expertise_number'),
                'expertise_date': data.get('expertise_date'),
                'expertise_year': data.get('expertise_year'),
                'expertise_type': data.get('expertise_type'),
                'expert_name': data.get('expert_name'),
                'sector': data.get('sector'),
                'source_file': data.get('source_file'),
                'file_path': data.get('file_path'),
                'file_size': data.get('file_size', 0),
                'file_hash': data.get('file_hash'),
            }
            
            # SQL запит для вставки
            cursor.execute('''
                INSERT INTO expertise_cases (
                    erddr_number, expertise_number, expertise_date, expertise_year,
                    expertise_type, expert_name, sector, source_file, file_path,
                    file_size, file_hash, created_at, updated_at
                ) VALUES (
                    :erddr_number, :expertise_number, :expertise_date, :expertise_year,
                    :expertise_type, :expert_name, :sector, :source_file, :file_path,
                    :file_size, :file_hash, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            ''', insert_data)
            
            doc_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            print(f"Документ збережено в базу даних з ID: {doc_id}")
            return doc_id
            
        except sqlite3.IntegrityError as e:
            print(f"Помилка унікальності: {e}")
            return None
        except Exception as e:
            print(f"Помилка збереження в базу даних: {e}")
            return None
    
    def _invalidate_search_cache(self):
        """Очищення кешу пошуку після змін у базі даних"""
        try:
            if not self._cache_initialized:
                return
            
            # Очищення кешу у пам'яті
            self.search_cache.clear()
            
            # Очищення файлів кешу пошуку
            if os.path.exists(CACHE_DIR):
                for filename in os.listdir(CACHE_DIR):
                    if filename.startswith('search_') and filename.endswith('.pkl'):
                        try:
                            os.remove(os.path.join(CACHE_DIR, filename))
                        except:
                            pass
            
            print("Кеш пошуку очищено")
            
        except Exception as e:
            print(f"Помилка очищення кешу: {e}")

    # =============================================================================
    # СКАНУВАННЯ ІСНУЮЧОГО АРХІВУ (ОПТИМІЗОВАНІ МЕТОДИ)
    # =============================================================================
    
    def _scan_existing_archive_impl(self, archive_path, progress_callback=None, 
                                   batch_size=50, skip_existing=True):
        """
        Оптимізоване сканування існуючого архіву з батчевою обробкою
        
        Args:
            archive_path: Шлях до архіву
            progress_callback: Функція для відстеження прогресу
            batch_size: Розмір батчу для обробки
            skip_existing: Пропускати вже існуючі файли
            
        Returns:
            dict: Результати сканування
        """
        try:
            if not os.path.exists(archive_path):
                return {'success': False, 'error': f'Архів не знайдено: {archive_path}'}
            
            print(f"🔍 Початок сканування архіву: {archive_path}")
            
            # Збір списку файлів для обробки
            files_to_scan = self._collect_files_for_scanning(archive_path, skip_existing)
            
            if not files_to_scan:
                return {
                    'success': True,
                    'message': 'Немає файлів для сканування',
                    'processed': 0,
                    'errors': 0
                }
            
            print(f"📄 Знайдено {len(files_to_scan)} файлів для обробки")
            
            # Батчева обробка файлів
            results = self._process_files_in_batches(
                files_to_scan, batch_size, progress_callback
            )
            
            # Очищення кешу після сканування
            self._invalidate_search_cache()
            
            return results
            
        except Exception as e:
            error_msg = f"Критична помилка сканування: {str(e)}"
            print(error_msg)
            return {'success': False, 'error': error_msg}
    
    def _collect_files_for_scanning(self, archive_path, skip_existing=True):
        """Збір списку файлів для сканування з фільтрацією"""
        files_to_scan = []
        
        try:
            # Отримання списку існуючих файлів якщо потрібно пропускати
            existing_hashes = set()
            existing_paths = set()
            
            if skip_existing:
                existing_hashes, existing_paths = self._get_existing_files_data()
            
            # Рекурсивний обхід директорій
            for root, dirs, files in os.walk(archive_path):
                # Пропуск системних директорій
                dirs[:] = [d for d in dirs if not is_system_directory(d)]
                
                for file in files:
                    if file.lower().endswith(SUPPORTED_EXTENSIONS):
                        file_path = os.path.join(root, file)
                        
                        # Перевірка розміру файлу
                        if get_file_size_mb(file_path) > MAX_FILE_SIZE_MB:
                            print(f"⚠️ Пропуск великого файлу: {file_path}")
                            continue
                        
                        # Перевірка існування файлу
                        if skip_existing:
                            if file_path in existing_paths:
                                continue
                            
                            file_size = int(get_file_size_mb(file_path) * 1024 * 1024)
                            file_hash = self._get_file_hash(file_path, file_size)
                            if file_hash and file_hash in existing_hashes:
                                continue
                        
                        files_to_scan.append(file_path)
            
            return files_to_scan
            
        except Exception as e:
            print(f"Помилка збору файлів: {e}")
            return []
    
    def _get_existing_files_data(self):
        """Отримання даних про існуючі файли з бази"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("SELECT file_hash, file_path FROM expertise_cases WHERE file_hash IS NOT NULL")
            data = cursor.fetchall()
            conn.close()
            
            hashes = {row[0] for row in data if row[0]}
            paths = {row[1] for row in data if row[1]}
            
            return hashes, paths
            
        except Exception as e:
            print(f"Помилка отримання існуючих файлів: {e}")
            return set(), set()
    
    def _process_files_in_batches(self, files_list, batch_size, progress_callback=None):
        """Батчева обробка файлів з оптимізацією"""
        total_files = len(files_list)
        processed = 0
        errors = 0
        error_details = []
        
        # Підготовка для батчевої вставки
        batch_data = []
        
        try:
            for i, file_path in enumerate(files_list):
                try:
                    # Парсинг документа
                    parsed_data = self.parse_expertise_document(file_path)
                    
                    # Додавання хешу
                    file_size = int(get_file_size_mb(file_path) * 1024 * 1024)
                    parsed_data['file_hash'] = self._get_file_hash(file_path, file_size)
                    
                    batch_data.append(parsed_data)
                    
                    # Обробка батчу при досягненні розміру
                    if len(batch_data) >= batch_size:
                        batch_processed, batch_errors = self._save_batch_to_database(batch_data)
                        processed += batch_processed
                        errors += batch_errors
                        batch_data = []
                    
                except Exception as e:
                    errors += 1
                    error_msg = f"Помилка обробки {file_path}: {str(e)}"
                    error_details.append(error_msg)
                    print(f"❌ {error_msg}")
                
                # Оновлення прогресу
                if progress_callback and (i + 1) % 10 == 0:
                    progress = (i + 1) / total_files
                    progress_callback(progress, f"Оброблено {i + 1}/{total_files} файлів")
            
            # Обробка останнього батчу
            if batch_data:
                batch_processed, batch_errors = self._save_batch_to_database(batch_data)
                processed += batch_processed
                errors += batch_errors
            
            return {
                'success': True,
                'total_files': total_files,
                'processed': processed,
                'errors': errors,
                'error_details': error_details[:10]  # Перші 10 помилок
            }
            
        except Exception as e:
            return {'success': False, 'error': f"Критична помилка батчевої обробки: {str(e)}"}
    
    def _save_batch_to_database(self, batch_data):
        """Оптимізоване збереження батчу в базу даних"""
        processed = 0
        errors = 0
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Підготовка SQL для батчевої вставки
            insert_sql = '''
                INSERT OR IGNORE INTO expertise_cases (
                    erddr_number, expertise_number, expertise_date, expertise_year,
                    expertise_type, expert_name, sector, source_file, file_path,
                    file_size, file_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            '''
            
            # Підготовка даних для вставки
            insert_values = []
            for data in batch_data:
                values = (
                    data.get('erddr_number'),
                    data.get('expertise_number'),
                    data.get('expertise_date'),
                    data.get('expertise_year'),
                    data.get('expertise_type'),
                    data.get('expert_name'),
                    data.get('sector'),
                    data.get('source_file'),
                    data.get('file_path'),
                    data.get('file_size', 0),
                    data.get('file_hash'),
                )
                insert_values.append(values)
            
            # Батчева вставка
            cursor.executemany(insert_sql, insert_values)
            processed = cursor.rowcount
            
            conn.commit()
            conn.close()
            
            print(f"✅ Батч збережено: {processed} записів")
            
        except Exception as e:
            errors = len(batch_data)
            print(f"❌ Помилка збереження батчу: {e}")
        
        return processed, errors
