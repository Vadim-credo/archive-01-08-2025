# =============================================================================
    # STREAMLIT ІНТЕРФЕЙС (ОПТИМІЗОВАНИЙ)
    # =============================================================================
    
    @staticmethod
    @st.cache_resource
    def get_agent_instance(**kwargs):
        """Кешований singleton агента для Streamlit"""
        return ForensicArchiveAgent(**kwargs)
    
    @staticmethod
    @st.cache_data(ttl=300)  # Кеш на 5 хвилин
    def get_cached_statistics(agent):
        """Кешована статистика бази даних"""
        return agent.get_search_statistics()
    
    @staticmethod
    @st.cache_data(ttl=60)  # Кеш на 1 хвилину
    def get_cached_search_results(agent, **search_params):
        """Кешовані результати пошуку"""
        return agent.search_documents(**search_params)

def create_streamlit_interface():
    """
    Створення оптимізованого Streamlit інтерфейсу
    """
    # Налаштування сторінки
    st.set_page_config(
        page_title="🔍 Архів судових експертиз",
        page_icon="🔍",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # Заголовок з інформацією
    st.title("🔍 AI-Агент для організації архіву судових експертиз")
    st.markdown("---")
    
    # Ініціалізація стану сесії
    initialize_session_state()
    
    # Сайдбар з налаштуваннями
    setup_sidebar()
    
    # Основний інтерфейс
    main_interface()

def initialize_session_state():
    """Ініціалізація стану сесії з оптимізацією"""
    
    # Основні налаштування
    if 'agent' not in st.session_state:
        st.session_state.agent = None
    
    if 'current_mode' not in st.session_state:
        st.session_state.current_mode = "🔍 Пошук документів"
    
    if 'search_results' not in st.session_state:
        st.session_state.search_results = None
    
    if 'last_search_params' not in st.session_state:
        st.session_state.last_search_params = {}
    
    # Налаштування архіву
    if 'archive_settings' not in st.session_state:
        st.session_state.archive_settings = {
            'db_path': 'forensic_archive.db',
            'archive_folder': 'archive',
            'existing_archive_path': '',
            'index_only_mode': False
        }
    
    # Стан операцій
    if 'operation_in_progress' not in st.session_state:
        st.session_state.operation_in_progress = False
    
    if 'operation_results' not in st.session_state:
        st.session_state.operation_results = {}

def setup_sidebar():
    """Налаштування сайдбару з оптимізованими контролами"""
    
    with st.sidebar:
        st.header("⚙️ Налаштування")
        
        # Режим роботи
        st.session_state.current_mode = st.selectbox(
            "Режим роботи:",
            [
                "🔍 Пошук документів",
                "📄 Додавання документів", 
                "📂 Сканування архіву",
                "📊 Статистика та аналіз",
                "🛠️ Налаштування системи"
            ],
            index=0
        )
        
        st.markdown("---")
        
        # Налаштування архіву
        setup_archive_settings()
        
        # Ініціалізація агента
        setup_agent_initialization()
        
        # Системна інформація
        display_system_info()

def setup_archive_settings():
    """Налаштування параметрів архіву"""
    
    st.subheader("🗂️ Параметри архіву")
    
    # База даних
    st.session_state.archive_settings['db_path'] = st.text_input(
        "База даних:",
        value=st.session_state.archive_settings['db_path'],
        help="Шлях до файлу бази даних SQLite"
    )
    
    # Папка архіву
    st.session_state.archive_settings['archive_folder'] = st.text_input(
        "Папка архіву:",
        value=st.session_state.archive_settings['archive_folder'],
        help="Папка для організованого архіву"
    )
    
    # Існуючий архів
    st.session_state.archive_settings['existing_archive_path'] = st.text_input(
        "Існуючий архів:",
        value=st.session_state.archive_settings['existing_archive_path'],
        help="Шлях до існуючого архіву для індексування"
    )
    
    # Режим тільки індексування
    st.session_state.archive_settings['index_only_mode'] = st.checkbox(
        "Тільки індексування (без копіювання файлів)",
        value=st.session_state.archive_settings['index_only_mode'],
        help="Індексувати файли без копіювання в нову структуру"
    )

def setup_agent_initialization():
    """Ініціалізація агента з перевіркою параметрів"""
    
    st.subheader("🤖 Стан агента")
    
    # Кнопка ініціалізації/переініціалізації
    if st.button("🔄 Ініціалізувати агента", use_container_width=True):
        with st.spinner("Ініціалізація агента..."):
            try:
                # Створення нового агента з поточними налаштуваннями
                st.session_state.agent = ForensicArchiveAgent.get_agent_instance(
                    **st.session_state.archive_settings
                )
                st.success("✅ Агент успішно ініціалізований!")
                
                # Очищення кешу результатів
                if 'search_results' in st.session_state:
                    st.session_state.search_results = None
                
            except Exception as e:
                st.error(f"❌ Помилка ініціалізації: {str(e)}")
                st.session_state.agent = None
    
    # Індикатор стану агента
    if st.session_state.agent is not None:
        st.success("🟢 Агент готовий до роботи")
        
        # Швидка статистика
        if st.button("📊 Швидка статистика", use_container_width=True):
            display_quick_stats()
    else:
        st.warning("🟡 Агент не ініціалізований")

def display_system_info():
    """Відображення системної інформації"""
    
    st.markdown("---")
    st.subheader("💻 Системна інформація")
    
    # Інформація про доступні бібліотеки
    libs_status = []
    if WIN32_AVAILABLE:
        libs_status.append("✅ WIN32COM")
    else:
        libs_status.append("❌ WIN32COM")
    
    if PDF_AVAILABLE:
        libs_status.append("✅ PDF")
    else:
        libs_status.append("❌ PDF")
    
    if PSUTIL_AVAILABLE:
        libs_status.append("✅ PSUTIL")
    else:
        libs_status.append("❌ PSUTIL")
    
    st.write("**Бібліотеки:**")
    for status in libs_status:
        st.write(status)
    
    # Інформація про кеш
    if os.path.exists(CACHE_DIR):
        cache_files = len([f for f in os.listdir(CACHE_DIR) if f.endswith('.pkl')])
        st.write(f"**Файлів кешу:** {cache_files}")
    
    # Кнопка очищення кешу
    if st.button("🗑️ Очистити кеш", use_container_width=True):
        clear_cache()

def clear_cache():
    """Очищення всіх кешів"""
    try:
        # Очищення Streamlit кешу
        st.cache_data.clear()
        st.cache_resource.clear()
        
        # Очищення файлового кешу
        if os.path.exists(CACHE_DIR):
            import shutil
            shutil.rmtree(CACHE_DIR)
            os.makedirs(CACHE_DIR, exist_ok=True)
        
        # Очищення кешу агента
        if st.session_state.agent:
            st.session_state.agent.search_cache.clear()
            st.session_state.agent.file_scan_cache.clear()
            st.session_state.agent.cache_timestamps.clear()
        
        st.success("✅ Кеш очищено!")
        
    except Exception as e:
        st.error(f"❌ Помилка очищення кешу: {e}")

def display_quick_stats():
    """Швидка статистика бази даних"""
    if not st.session_state.agent:
        return
    
    try:
        stats = ForensicArchiveAgent.get_cached_statistics(st.session_state.agent)
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.metric("📄 Всього документів", stats.get('total_records', 0))
        
        with col2:
            if stats.get('by_year'):
                latest_year = max(stats['by_year'].keys())
                st.metric("📅 Останній рік", latest_year)
        
    except Exception as e:
        st.error(f"Помилка отримання статистики: {e}")

def main_interface():
    """Основний інтерфейс залежно від режиму"""
    
    if not st.session_state.agent:
        st.warning("⚠️ Спочатку ініціалізуйте агента в сайдбарі")
        return
    
    mode = st.session_state.current_mode
    
    if mode == "🔍 Пошук документів":
        search_interface()
    elif mode == "📄 Додавання документів":
        add_documents_interface()
    elif mode == "📂 Сканування архіву":
        scan_archive_interface()
    elif mode == "📊 Статистика та аналіз":
        statistics_interface()
    elif mode == "🛠️ Налаштування системи":
        settings_interface()

def search_interface():
    """Інтерфейс пошуку документів"""
    
    st.header("🔍 Пошук документів")
    
    # Форма пошуку
    with st.form("search_form"):
        col1, col2, col3 = st.columns(3)
        
        with col1:
            erddr_number = st.text_input("№ ЄРДР", help="11-12 цифр")
            expertise_number = st.text_input("№ Експертизи", help="Наприклад: 123/2024")
            expertise_date = st.date_input("Дата експертизи", value=None)
        
        with col2:
            expertise_year = st.number_input(
                "Рік", 
                min_value=2000, 
                max_value=datetime.now().year + 1,
                value=None,
                step=1
            )
            expert_name = st.text_input("Ім'я експерта", help="Частковий пошук")
            expertise_type = st.selectbox(
                "Тип експертизи",
                ["Всі", "почеркознавча", "зброї", "трасологічна", "дактилоскопічна", "бал. облік"],
                index=0
            )
        
        with col3:
            sector = st.selectbox(
                "Сектор",
                ["Всі", "почерк та ТЕД", "балісти", "трасологія", "дактилоскопія", "бал. облік"],
                index=0
            )
            limit = st.number_input("Максимум результатів", min_value=10, max_value=10000, value=1000)
            use_cache = st.checkbox("Використовувати кеш", value=True)
        
        search_button = st.form_submit_button("🔍 Шукати", use_container_width=True)
    
    # Виконання пошуку
    if search_button:
        search_params = {
            'erddr_number': erddr_number if erddr_number else None,
            'expertise_number': expertise_number if expertise_number else None,
            'expertise_date': expertise_date.strftime('%d.%m.%Y') if expertise_date else None,
            'expertise_year': expertise_year if expertise_year else None,
            'expert_name': expert_name if expert_name else None,
            'expertise_type': expertise_type if expertise_type != "Всі" else None,
            'sector': sector if sector != "Всі" else None,
            'limit': limit,
            'use_cache': use_cache
        }
        
        with st.spinner("🔍 Виконання пошуку..."):
            try:
                results = ForensicArchiveAgent.get_cached_search_results(
                    st.session_state.agent, **search_params
                )
                st.session_state.search_results = results
                st.session_state.last_search_params = search_params
                
            except Exception as e:
                st.error(f"❌ Помилка пошуку: {str(e)}")
                st.session_state.search_results = None
    
    # Відображення результатів
    display_search_results()

def display_search_results():
    """Відображення результатів пошуку з оптимізацією"""
    
    if st.session_state.search_results is None:
        return
    
    results = st.session_state.search_results
    
    if results.empty:
        st.info("ℹ️ За вашим запитом документи не знайдені")
        return
    
    st.markdown("---")
    st.subheader(f"📋 Результати пошуку ({len(results)} документів)")
    
    # Фільтри для результатів
    with st.expander("🔧 Додаткові фільтри результатів"):
        col1, col2, col3 = st.columns(3)
        
        with col1:
            # Фільтр по експерту
            experts = ["Всі"] + sorted(results['expert_name'].dropna().unique().tolist())
            selected_expert = st.selectbox("Експерт:", experts)
        
        with col2:
            # Фільтр по сектору
            sectors = ["Всі"] + sorted(results['sector'].dropna().unique().tolist())
            selected_sector = st.selectbox("Сектор:", sectors)
        
        with col3:
            # Фільтр по року
            years = ["Всі"] + sorted(results['expertise_year'].dropna().unique().tolist(), reverse=True)
            selected_year = st.selectbox("Рік:", [str(y) if isinstance(y, int) else y for y in years])
    
    # Застосування фільтрів
    filtered_results = results.copy()
    
    if selected_expert != "Всі":
        filtered_results = filtered_results[filtered_results['expert_name'] == selected_expert]
    
    if selected_sector != "Всі":
        filtered_results = filtered_results[filtered_results['sector'] == selected_sector]
    
    if selected_year != "Всі":
        filtered_results = filtered_results[filtered_results['expertise_year'] == int(selected_year)]
    
    # Пагінація результатів
    display_paginated_results(filtered_results)

def display_paginated_results(results):
    """Відображення результатів з пагінацією"""
    
    if results.empty:
        st.info("ℹ️ Немає результатів після фільтрації")
        return
    
    # Налаштування пагінації
    items_per_page = st.selectbox("Результатів на сторінці:", [10, 25, 50, 100], index=1)
    total_pages = (len(results) - 1) // items_per_page + 1
    
    if 'current_page' not in st.session_state:
        st.session_state.current_page = 1
    
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col1:
        if st.button("⬅️ Попередня", disabled=st.session_state.current_page <= 1):
            st.session_state.current_page -= 1
    
    with col2:
        st.session_state.current_page = st.number_input(
            f"Сторінка (з {total_pages}):",
            min_value=1,
            max_value=total_pages,
            value=st.session_state.current_page,
            step=1
        )
    
    with col3:
        if st.button("Наступна ➡️", disabled=st.session_state.current_page >= total_pages):
            st.session_state.current_page += 1
    
    # Відображення поточної сторінки
    start_idx = (st.session_state.current_page - 1) * items_per_page
    end_idx = start_idx + items_per_page
    page_results = results.iloc[start_idx:end_idx]
    
    # Вибір формату відображення
    display_format = st.radio(
        "Формат відображення:",
        ["📋 Таблиця", "📄 Картки", "📊 Детальний вигляд"],
        horizontal=True
    )
    
    if display_format == "📋 Таблиця":
        display_table_format(page_results)
    elif display_format == "📄 Картки":
        display_card_format(page_results)
    else:
        display_detailed_format(page_results)
    
    # Кнопки експорту
    st.markdown("---")
    display_export_options(results)

def display_table_format(results):
    """Відображення у форматі таблиці"""
    
    # Вибір колонок для відображення
    available_columns = [
        'erddr_number', 'expertise_number', 'expertise_date', 'expertise_year',
        'expertise_type', 'expert_name', 'sector', 'source_file'
    ]
    
    selected_columns = st.multiselect(
        "Колонки для відображення:",
        available_columns,
        default=['erddr_number', 'expertise_number', 'expertise_date', 'expert_name', 'sector'],
        help="Виберіть колонки які хочете бачити в таблиці"
    )
    
    if selected_columns:
        display_df = results[selected_columns].copy()
        
        # Форматування даних для кращого відображення
        if 'expertise_date' in display_df.columns:
            display_df['expertise_date'] = display_df['expertise_date'].fillna('Не вказано')
        
        # Відображення таблиці з можливістю сортування
        st.dataframe(
            display_df,
            use_container_width=True,
            height=600,
            column_config={
                "erddr_number": st.column_config.TextColumn("№ ЄРДР"),
                "expertise_number": st.column_config.TextColumn("№ Експертизи"),
                "expertise_date": st.column_config.TextColumn("Дата"),
                "expertise_year": st.column_config.NumberColumn("Рік"),
                "expertise_type": st.column_config.TextColumn("Тип"),
                "expert_name": st.column_config.TextColumn("Експерт"),
                "sector": st.column_config.TextColumn("Сектор"),
                "source_file": st.column_config.TextColumn("Файл")
            }
        )

def display_card_format(results):
    """Відображення у форматі карток"""
    
    for idx, row in results.iterrows():
        with st.container():
            col1, col2 = st.columns([3, 1])
            
            with col1:
                # Заголовок картки
                title = f"📄 {safe_get_value(row, 'source_file')}"
                st.subheader(title)
                
                # Основна інформація
                col_info1, col_info2 = st.columns(2)
                
                with col_info1:
                    st.write(f"**№ ЄРДР:** {safe_get_value(row, 'erddr_number')}")
                    st.write(f"**№ Експертизи:** {safe_get_value(row, 'expertise_number')}")
                    st.write(f"**Дата:** {safe_get_value(row, 'expertise_date')}")
                
                with col_info2:
                    st.write(f"**Тип:** {safe_get_value(row, 'expertise_type')}")
                    st.write(f"**Експерт:** {safe_get_value(row, 'expert_name')}")
                    st.write(f"**Сектор:** {safe_get_value(row, 'sector')}")
            
            with col2:
                # Кнопки дій
                if st.button(f"👁️ Деталі", key=f"details_{idx}"):
                    display_document_details(row)
                
                if st.button(f"📁 Відкрити", key=f"open_{idx}"):
                    open_document_file(row)
            
            st.markdown("---")

def display_detailed_format(results):
    """Детальне відображення документів"""
    
    selected_doc = st.selectbox(
        "Виберіть документ для детального перегляду:",
        range(len(results)),
        format_func=lambda x: f"{safe_get_value(results.iloc[x], 'source_file')} - {safe_get_value(results.iloc[x], 'expertise_number')}"
    )
    
    if selected_doc is not None:
        document = results.iloc[selected_doc]
        display_document_details(document)

def display_document_details(document):
    """Відображення деталей документа"""
    
    st.markdown("### 📋 Детальна інформація про документ")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.write(f"**ID:** {safe_get_value(document, 'id')}")
        st.write(f"**Файл:** {safe_get_value(document, 'source_file')}")
        st.write(f"**Шлях:** {safe_get_value(document, 'file_path')}")
        st.write(f"**Розмір:** {safe_get_value(document, 'file_size')} байт")
        st.write(f"**№ ЄРДР:** {safe_get_value(document, 'erddr_number')}")
        st.write(f"**№ Експертизи:** {safe_get_value(document, 'expertise_number')}")
    
    with col2:
        st.write(f"**Дата:** {safe_get_value(document, 'expertise_date')}")
        st.write(f"**Рік:** {safe_get_value(document, 'expertise_year')}")
        st.write(f"**Тип:** {safe_get_value(document, 'expertise_type')}")
        st.write(f"**Експерт:** {safe_get_value(document, 'expert_name')}")
        st.write(f"**Сектор:** {safe_get_value(document, 'sector')}")
        st.write(f"**Створено:** {safe_get_value(document, 'created_at')}")
    
    # Кнопки дій
    col_btn1, col_btn2, col_btn3 = st.columns(3)
    
    with col_btn1:
        if st.button("📁 Відкрити файл", use_container_width=True):
            open_document_file(document)
    
    with col_btn2:
        if st.button("📂 Показати в провіднику", use_container_width=True):
            show_in_explorer(document)
    
    with col_btn3:
        if st.button("✏️ Редагувати", use_container_width=True):
            edit_document(document)

def display_export_options(results):
    """Опції експорту результатів"""
    
    st.subheader("📤 Експорт результатів")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("📊 Експорт в Excel", use_container_width=True):
            export_to_excel(results)
    
    with col2:
        if st.button("📄 Експорт в CSV", use_container_width=True):
            export_to_csv(results)
    
    with col3:
        if st.button("📋 Копіювати в буфер", use_container_width=True):
            copy_to_clipboard(results)

def open_document_file(document):
    """Відкриття файлу документа"""
    file_path = safe_get_value(document, 'file_path')
    
    if file_path and file_path != "Не вказано":
        try:
            if os.path.exists(file_path):
                import subprocess
                if os.name == 'nt':  # Windows
                    subprocess.run(['start', '', file_path], shell=True, check=True)
                else:  # Linux/Mac
                    subprocess.run(['xdg-open', file_path], check=True)
                st.success(f"✅ Файл відкрито: {os.path.basename(file_path)}")
            else:
                st.error(f"❌ Файл не знайдено: {file_path}")
        except Exception as e:
            st.error(f"❌ Помилка відкриття файлу: {str(e)}")
    else:
        st.warning("⚠️ Шлях до файлу не вказано")

def show_in_explorer(document):
    """Показати файл в провіднику"""
    file_path = safe_get_value(document, 'file_path')
    
    if file_path and file_path != "Не вказано":
        try:
            if os.path.exists(file_path):
                import subprocess
                if os.name == 'nt':  # Windows
                    subprocess.run(['explorer', '/select,', file_path], check=True)
                else:  # Linux/Mac
                    folder_path = os.path.dirname(file_path)
                    subprocess.run(['xdg-open', folder_path], check=True)
                st.success("✅ Файл показано в провіднику")
            else:
                st.error(f"❌ Файл не знайдено: {file_path}")
        except Exception as e:
            st.error(f"❌ Помилка: {str(e)}")
    else:
        st.warning("⚠️ Шлях до файлу не вказано")

def edit_document(document):
    """Редагування документа"""
    st.info("🔧 Функція редагування буде додана в наступній версії")

def export_to_excel(results):
    """Експорт результатів в Excel"""
    try:
        from io import BytesIO
        import pandas as pd
        
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            results.to_excel(writer, sheet_name='Результати пошуку', index=False)
        
        excel_data = output.getvalue()
        
        st.download_button(
            label="💾 Завантажити Excel файл",
            data=excel_data,
            file_name=f"forensic_search_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
    except Exception as e:
        st.error(f"❌ Помилка експорту в Excel: {str(e)}")

def export_to_csv(results):
    """Експорт результатів в CSV"""
    try:
        csv_data = results.to_csv(index=False, encoding='utf-8-sig')
        
        st.download_button(
            label="💾 Завантажити CSV файл",
            data=csv_data,
            file_name=f"forensic_search_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv"
        )
        
    except Exception as e:
        st.error(f"❌ Помилка експорту в CSV: {str(e