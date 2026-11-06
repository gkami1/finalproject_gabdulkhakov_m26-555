# finalproject_gabdulkhakov_m26-555

# ValutaTrade Hub

Консольная платформа для отслеживания курсов и симуляции торговли фиатными и криптовалютами.
Пользователь регистрируется, ведёт виртуальный портфель (USD, EUR, BTC, ETH…), покупает и
продаёт валюту по актуальным курсам. Курсы собирает отдельный **Parser Service** из публичных API.

Проект состоит из двух сервисов:

- **Core Service** — CLI, пользователи, портфели, сделки. Работает с локальным кешем курсов
  `data/rates.json` и не обращается к внешним API напрямую.
- **Parser Service** — по запросу или по расписанию опрашивает
  [CoinGecko](https://www.coingecko.com/en/api) (криптовалюты) и
  [ExchangeRate-API](https://www.exchangerate-api.com/) (фиат), обновляет кеш `rates.json`
  и ведёт историю замеров в `exchange_rates.json`.

## Технологии

Python 3.12, [uv](https://docs.astral.sh/uv/) (зависимости и сборка), Ruff (PEP8-линтер и
форматтер), prettytable (таблицы), requests (HTTP). Хранилище — JSON-файлы.

## Структура проекта

```
finalproject_gabdulkhakov_m26-555/
├── data/
│   ├── users.json             # пользователи (хеш пароля + соль)
│   ├── portfolios.json        # портфели и кошельки
│   ├── rates.json             # кеш последних курсов для Core Service
│   └── exchange_rates.json    # история замеров Parser Service
├── logs/                      # actions.log, parser.log (создаются при запуске)
├── valutatrade_hub/
│   ├── logging_config.py      # формат логов, ротация
│   ├── decorators.py          # @log_action — логирование операций
│   ├── core/
│   │   ├── models.py          # User, Wallet, Portfolio
│   │   ├── currencies.py      # Currency (ABC), FiatCurrency, CryptoCurrency, get_currency()
│   │   ├── exceptions.py      # доменные исключения
│   │   ├── usecases.py        # бизнес-логика (register, login, buy, sell, get_rate, ...)
│   │   └── utils.py           # валидация, конвертация, форматирование курса
│   ├── infra/
│   │   ├── settings.py        # SettingsLoader (Singleton) — конфигурация
│   │   └── database.py        # DatabaseManager (Singleton) — JSON-хранилище
│   ├── parser_service/
│   │   ├── config.py          # ParserConfig: API, списки валют, таймауты, пути
│   │   ├── api_clients.py     # BaseApiClient, CoinGeckoClient, ExchangeRateApiClient
│   │   ├── storage.py         # запись rates.json и exchange_rates.json
│   │   ├── updater.py         # RatesUpdater — координация обновления
│   │   └── scheduler.py       # периодическое обновление
│   └── cli/
│       └── interface.py       # интерактивная оболочка и команды
├── main.py
├── Makefile
├── pyproject.toml
├── uv.lock
└── .env.example
```

Слои разделены: CLI только разбирает аргументы, вызывает функции `core/usecases.py` и
печатает результат. Вся бизнес-логика находится в usecases.

## Установка

```bash
git clone git@github.com:gkami1/finalproject_gabdulkhakov_m26-555.git
cd finalproject_gabdulkhakov_m26-555
uv sync            # или: make install
```

Установка как самостоятельного пакета:

```bash
make build             # uv build → dist/*.whl
make package-install   # uv tool install dist/*.whl
```

## Запуск

```bash
uv run project         # или: make project / uv run valutatrade / python main.py
```

Откроется интерактивная оболочка с приглашением `>`. Список команд выводит `help`,
справку по одной команде — `help <команда>`, выход — `exit`.

> Запускайте из корня проекта (или из любой его подпапки): пути к `data/` и `logs/`
> считаются от каталога с `pyproject.toml`.

## Команды CLI

| Команда | Описание |
|---|---|
| `register --username <имя> --password <пароль>` | регистрация (пароль ≥ 4 символов) |
| `login --username <имя> --password <пароль>` | вход, фиксирует сессию |
| `logout` | выход из учётной записи |
| `show-portfolio [--base <код>]` | кошельки и итоговая стоимость (по умолчанию в USD) |
| `buy --currency <код> --amount <кол-во>` | покупка; стоимость списывается с USD-кошелька |
| `sell --currency <код> --amount <кол-во>` | продажа; выручка зачисляется на USD-кошелёк |
| `get-rate --from <код> --to <код>` | текущий курс (с учётом TTL кеша) |
| `update-rates [--source coingecko\|exchangerate]` | немедленно обновить курсы из API |
| `show-rates [--currency <код>] [--top <N>] [--base <код>]` | курсы из локального кеша |
| `currencies` | список поддерживаемых валют |
| `help [команда]`, `exit` | справка, выход |

Команды `show-portfolio`, `buy` и `sell` доступны только после `login`.

### Логика сделок

- `buy --currency BTC --amount 0.05` покупает 0.05 BTC: стоимость `amount × курс` списывается
  с USD-кошелька. Если кошелька BTC ещё нет, он создаётся автоматически.
- `sell` уменьшает баланс валюты и начисляет выручку в USD.
- `buy --currency USD --amount 1000` пополняет USD-кошелёк. `sell --currency USD` выводит
  средства. Новый портфель пуст, поэтому сначала нужно пополнить USD.

### Примеры

```
> register --username alice --password 1234
Пользователь 'alice' зарегистрирован (id=1). Войдите: login --username alice --password ****

> login --username alice --password 1234
Вы вошли как 'alice'

> buy --currency USD --amount 5000
Пополнение выполнено: 5000.00 USD
Изменения в портфеле:
- USD: было 0.00 → стало 5000.00

> buy --currency BTC --amount 0.05
Покупка выполнена: 0.0500 BTC по курсу 59337.21 USD/BTC
Изменения в портфеле:
- BTC: было 0.0000 → стало 0.0500
- USD: было 5000.00 → стало 2033.14
Оценочная стоимость покупки: 2,966.86 USD

> sell --currency BTC --amount 0.01
Продажа выполнена: 0.0100 BTC по курсу 59337.21 USD/BTC
Изменения в портфеле:
- BTC: было 0.0500 → стало 0.0400
- USD: было 2033.14 → стало 2626.51
Оценочная выручка: 593.37 USD

> show-portfolio
Портфель пользователя 'alice' (база: USD):
- USD: 2626.51  → 2626.51 USD
- BTC: 0.0400  → 2373.49 USD
---------------------------------
ИТОГО: 5,000.00 USD

> get-rate --from USD --to BTC
Курс USD→BTC: 0.00001685 (обновлено: 2025-10-09 00:03:22)
Обратный курс BTC→USD: 59337.21

> update-rates
INFO: Starting rates update...
INFO: Fetching from CoinGecko... OK (3 rates)
INFO: Fetching from ExchangeRate-API... OK (3 rates)
INFO: Writing 6 rates to data/rates.json...
INFO: Update successful: 6 rates
Update successful. Total rates updated: 6. Last refresh: 2025-10-10T15:30:00Z

> show-rates --top 2
Rates from cache (updated at 2025-10-10T15:30:00):
+---------+----------+---------------------+-----------+
|   Пара  |     Курс |      Обновлено      |  Источник |
+---------+----------+---------------------+-----------+
| BTC_USD | 59337.21 | 2025-10-10T15:30:00 | CoinGecko |
| ETH_USD |  3720.00 | 2025-10-10T15:30:00 | CoinGecko |
+---------+----------+---------------------+-----------+
```

### Обработка ошибок

Ошибки ядра выбрасываются как доменные исключения (`core/exceptions.py`). CLI перехватывает
их и печатает понятное сообщение:

| Ситуация | Сообщение |
|---|---|
| имя занято | `Имя пользователя 'alice' уже занято` |
| короткий пароль | `Пароль должен быть не короче 4 символов` |
| нет пользователя / неверный пароль | `Пользователь 'bob' не найден` / `Неверный пароль` |
| не выполнен вход | `Сначала выполните login` |
| некорректная сумма | `'amount' должен быть положительным числом` |
| `InsufficientFundsError` | `Недостаточно средств: доступно 0.0400 BTC, требуется 0.0500 BTC` |
| `CurrencyNotFoundError` | `Неизвестная валюта 'XYZ'` + список поддерживаемых кодов |
| неизвестная база | `Неизвестная базовая валюта 'ABC'` |
| нет кошелька | `У вас нет кошелька 'ETH'. Добавьте валюту: она создаётся автоматически при первой покупке.` |
| `ApiRequestError` | `Ошибка при обращении к внешнему API: <причина>` + совет повторить позже |
| пустой кеш | `Локальный кеш курсов пуст. Выполните 'update-rates', чтобы загрузить данные.` |

## Кеш курсов и TTL

`data/rates.json` хранит снимок последних курсов:

```json
{
  "pairs": {
    "EUR_USD": { "rate": 1.0786, "updated_at": "2025-10-10T12:00:00Z", "source": "ExchangeRate-API" },
    "BTC_USD": { "rate": 59337.21, "updated_at": "2025-10-10T12:00:00Z", "source": "CoinGecko" }
  },
  "last_refresh": "2025-10-10T12:00:01Z"
}
```

- Курс пары `X_USD` означает, сколько USD стоит 1 X. Обратные и кросс-курсы (например,
  EUR→RUB через USD) вычисляются автоматически.
- **TTL** — `rates_ttl_seconds` (по умолчанию 300 с). Если курс в кеше моложе TTL,
  `get-rate`/`buy`/`sell`/`show-portfolio` берут его из кеша. Если старше или курса нет,
  Core запускает обновление через Parser Service. Если ни один API не ответил, выводится
  `ApiRequestError` с предложением повторить позже.
- `show-rates` читает только кеш и предупреждает, если данные старше TTL.

## Parser Service

### API-ключ ExchangeRate-API

CoinGecko работает без ключа. Для фиатных курсов нужен бесплатный ключ ExchangeRate-API:

1. Зарегистрируйтесь на https://www.exchangerate-api.com/ и скопируйте ключ.
2. Сохраните ключ одним из способов:
   - файл `.env` в корне проекта (он в `.gitignore`, в git не попадёт):
     ```bash
     cp .env.example .env
     # затем впишите ключ: EXCHANGERATE_API_KEY=ваш_ключ
     ```
   - переменная окружения: `export EXCHANGERATE_API_KEY=ваш_ключ`
     (PowerShell: `$env:EXCHANGERATE_API_KEY="ваш_ключ"`).

**Никогда не храните ключ в коде.** Без ключа обновляются только криптовалюты, а
`update-rates` сообщит об ошибке источника ExchangeRate-API.

### Запуск

- Из CLI: `update-rates` (все источники) или `update-rates --source coingecko`.
- Отдельным процессом:
  ```bash
  uv run valutatrade-parser --once                  # одно обновление
  uv run valutatrade-parser                          # каждые rates_update_interval_seconds
  uv run valutatrade-parser --interval 60 --source coingecko
  ```
- Автоматически: Core сам обновляет кеш, когда курс старше TTL.

### Как работает

- `CoinGeckoClient` запрашивает `simple/price` по ID монет (`CRYPTO_ID_MAP`:
  BTC→bitcoin, ETH→ethereum, SOL→solana).
- `ExchangeRateApiClient` запрашивает `/v6/<KEY>/latest/USD`. API возвращает «1 USD = x EUR»,
  поэтому сохраняется обратное значение `EUR_USD = 1/x`: так формат совпадает с `rates.json`.
- Таймаут, нет сети, HTTP 401/403 (ключ), 429 (лимит), 5xx и некорректный ответ
  превращаются в `ApiRequestError` с понятной причиной.
- `RatesUpdater.run_update()` опрашивает все источники. Если один упал, ошибка логируется,
  а данные остальных всё равно сохраняются.
- Запись атомарная (временный файл → rename). В `rates.json` обновляется только более свежее
  значение пары. В `exchange_rates.json` каждый замер добавляется в историю с уникальным
  `id` = `BTC_USD_2025-10-10T12:00:00Z` и метаданными (`raw_id`, `request_ms`,
  `status_code`, `etag`).

Списки отслеживаемых валют, URL и таймаут задаются в `parser_service/config.py`.

## Конфигурация

Параметры находятся в `pyproject.toml`, секция `[tool.valutatrade]`, и читаются синглтоном
`SettingsLoader`:

```toml
[tool.valutatrade]
rates_ttl_seconds = 300               # срок свежести кеша курсов
rates_update_interval_seconds = 300   # период планировщика
default_base_currency = "USD"
users_file = "data/users.json"        # ... и остальные пути к данным
actions_log_file = "logs/actions.log"
parser_log_file = "logs/parser.log"
log_level = "INFO"
log_max_bytes = 1048576               # ротация: 1 МБ
log_backup_count = 5                  # хранить 5 архивов
```

Любой ключ можно переопределить переменной окружения `VALUTATRADE_<KEY>`, например
`VALUTATRADE_LOG_LEVEL=DEBUG` для отладки.

## Логирование

- `logs/actions.log` — операции Core. Декоратор `@log_action` пишет по строке на
  register/login/buy/sell, для buy/sell добавляет состояние кошельков «было→стало».
  Исключения записываются в лог и пробрасываются дальше. Пароли в лог не попадают.
  ```
  INFO 2025-10-09T12:05:22 BUY user='alice' currency='BTC' amount=0.0500 rate=59337.21 base='USD' changes='BTC 0.0000→0.0500; USD 5000.0000→2033.1395' result=OK
  INFO 2025-10-09T12:06:10 SELL user_id=1 currency='BTC' amount=1.0000 result=ERROR error_type=InsufficientFundsError error_message='Недостаточно средств: доступно 0.0400 BTC, требуется 1.0000 BTC'
  ```
- `logs/parser.log` — шаги Parser Service: старт, результат по каждому источнику, запись.
- Ротация по размеру (`RotatingFileHandler`), уровень по умолчанию INFO.

## Разработка

```bash
make lint      # uv run ruff check .
make format    # uv run ruff format .
```

## Демо

[![asciicast](https://asciinema.org/a/0cExDHfn798kiQyS.svg)](https://asciinema.org/a/0cExDHfn798kiQyS)