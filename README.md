# LocalInterfaceWeb — фотоархив

Курсовой проект, тема 12 «Система хранения графических изображений». ТИ НИЯУ МИФИ, кафедра ИТПМ. Мордовский Андрей Кириллович, ИВТ-43Д; руководитель Ваганов Д. А.

Загрузка JPEG/PNG/WebP с названием, таблица миниатюр, просмотр оригинала, поиск по названию в PostgreSQL, редактирование и удаление. Просмотр доступен гостю; изменение коллекции — после входа. Сервер: Python/Django, клиент: React/TypeScript, БД: PostgreSQL; pgAdmin подключается по желанию.

## Запуск за два действия

1. Установите и запустите [Docker Desktop](https://docs.docker.com/desktop/) с Linux containers. На Linux подходит Docker Engine с плагином Compose v2. Первый запуск требует интернет для загрузки образов и пакетов, свободного порта 8080 и нескольких гигабайт места. На Windows Docker Desktop может потребовать включить WSL2/виртуализацию и перезагрузить компьютер.
2. Скачайте [ZIP проекта](https://github.com/arxey/LocalInterfaceWeb/archive/refs/heads/main.zip), **распакуйте** в доступную для записи папку. В Windows дважды нажмите `start.cmd`. В macOS/Linux откройте терминал в этой папке и выполните `sh start.sh`.

После сборки откроется **http://localhost:8080**. Первый запуск занимает больше времени; последующие используют готовые слои. Скрипт показывает имя и случайный пароль администратора. Введите их в форме входа на странице. Пароли и секрет Django создаются автоматически и сохраняются локально в `.env.docker`. Python, Node.js, npm, PostgreSQL и настройка PATH на компьютере для этого способа не нужны.

Чтобы остановить: `stop.cmd` или `sh stop.sh`. Закрытие окна стартового скрипта не останавливает контейнеры. Данные остаются в именованных томах Docker после остановки, пересборки и повторного запуска. Вторая копия проекта не является независимой установкой, если использует то же Compose project name. Не удаляйте тома, если фотографии нужны.

## pgAdmin и база данных

После обычного старта включите pgAdmin:

```sh
docker compose --env-file .env.docker --profile tools up -d pgadmin
```

Откройте http://localhost:5050. Учетные данные находятся в `.env.docker` (`PGADMIN_DEFAULT_EMAIL`, `PGADMIN_DEFAULT_PASSWORD`). При регистрации сервера: Host `db`, Port `5432`, Maintenance database — значение `POSTGRES_DB`, Username — `POSTGRES_USER`, Password — `POSTGRES_PASSWORD`. Это адреса внутри сети Compose; порт PostgreSQL не выставлен на компьютер.

```sql
SELECT id, title, search_title, image, thumbnail, created_at, updated_at
FROM photos_photo ORDER BY created_at DESC, id DESC;
```

Схема и связи описаны в [docs/database.md](docs/database.md). Миграции и учебное изображение применяются автоматически. Демоданные добавляются при первичной настройке администратора; удаленная пользователем демофотография после перезапуска не восстанавливается.

## Файлы проекта

| Каталог | Содержимое |
|---|---|
| `backend/` | Django API, авторизация, обработка изображений, миграции, тесты |
| `frontend/` | React/TypeScript, интерфейс, клиентские тесты |
| `deployment/`, `compose.yaml` | Контейнеры и Nginx |
| `scripts/` | Автоматизация запуска и интеграционной проверки |
| `demo/` | Учебное изображение проекта |
| `docs/` | Требования, модель БД, проверка и пояснительная записка |

## Разработка в PyCharm

Для изменения серверного кода: Python 3.12+, PostgreSQL и виртуальное окружение. Создайте локальный `.env` по `.env.example`, задайте собственный `DJANGO_SECRET_KEY` и параметры вашей тестовой БД. Создать секрет можно командой `python -c "import secrets; print(secrets.token_urlsafe(64))"`. Реальные настройки не включаются в Git.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
cd backend
..\.venv\Scripts\python.exe manage.py migrate
..\.venv\Scripts\python.exe manage.py createsuperuser
..\.venv\Scripts\python.exe manage.py load_demo
..\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Для локальной разработки клиента установите Node.js 24 и версию pnpm из `frontend/package.json`. В каталоге `frontend`: `pnpm install --frozen-lockfile`, затем `pnpm dev`. Клиент открывается на http://127.0.0.1:5173, Vite проксирует `/api/` и `/media/` в Django. Для раздачи файлов через Django в этом режиме установите `DJANGO_DEBUG=1`; контейнерный запуск использует `DEBUG=0` и Nginx. В PyCharm выберите `.venv/Scripts/python.exe` (Windows), `backend/manage.py` и рабочий каталог `backend`.

## Тестирование и ревью

С запущенным контейнерным проектом:

```sh
docker compose --env-file .env.docker exec backend python manage.py test
docker compose --env-file .env.docker exec backend python manage.py check
```

Клиентские проверки из `frontend`: `pnpm test`, `pnpm build`, `pnpm audit`. CI запускает проверки и сценарий загрузки, поиска, изменения, удаления и сохранности файлов после перезапуска. Полный перечень реально выполненных проверок и границы ревью — [docs/review.md](docs/review.md), соответствие методичке — [docs/requirements.md](docs/requirements.md).

## Сохранение данных и публикация

Рабочие данные — PostgreSQL и media вместе. Для резервной копии сначала остановите изменения коллекции (например, `docker compose --env-file .env.docker stop web backend`), затем сохраните дамп и том media. После копирования запустите `sh start.sh` или `start.cmd`. Имена томов смотрите в `docker volume ls`. `.env.docker` храните отдельно в защищенном месте; он тоже нужен для восстановления. Простая копия исходников не содержит ваших фотографий или базы.

GitHub публикует **исходники**; запущенное приложение работает локально. Compose по умолчанию привязывает веб-порт к 127.0.0.1. Для размещения на внешнем сервере требуются домен, HTTPS, точные ALLOWED_HOSTS/CSRF origins, Secure cookies и политика доступа к фотографиям. Правила и ограничения — [SECURITY.md](SECURITY.md).
