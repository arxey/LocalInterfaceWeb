# LocalInterfaceWeb — фотоархив

Курсовой проект, тема 12 «Система хранения графических изображений». ТИ НИЯУ МИФИ, кафедра ИТПМ. Мордовский Андрей Кириллович, ИВТ-43Д; руководитель Ваганов Д. А.

[Пояснительная записка Word](docs/poyasnitelnaya_zapiska_tema_12.docx) · [Матрица требований](docs/requirements.md) · [Проверки и ревью](docs/review.md) · [GitHub Actions](https://github.com/arxey/LocalInterfaceWeb/actions)

Загрузка JPEG/PNG/WebP с названием, таблица миниатюр, просмотр оригинала, поиск по названию в PostgreSQL, редактирование и удаление. Просмотр доступен гостю; изменение коллекции — после входа. Сервер: Python/Django, клиент: React/TypeScript, БД: PostgreSQL.

## Запуск в Windows без установки программ

1. На странице [последнего релиза](https://github.com/arxey/LocalInterfaceWeb/releases/latest) скачайте **`LocalInterfaceWeb-Windows-x64.zip`** из раздела **Assets**. Если компьютер без интернета, скачайте архив заранее на другом компьютере и перенесите на флешке.
2. **Полностью распакуйте** архив в доступную для записи папку, например в «Документы». Не запускайте файлы внутри открытого ZIP и не используйте `Program Files`.
3. Дважды нажмите **`start-portable.cmd`**. Дождитесь строки `Ready` и открытия браузера. Оставьте окно запуска открытым.

Подходит Windows 10/11 x64 и обычная учётная запись пользователя. Docker, WSL, права администратора, отдельная установка Python, Node.js, PostgreSQL, pgAdmin, VS Code или PyCharm для запуска не нужны. После получения полного архива запуск и работа выполняются **без интернета**: необходимые среды, библиотеки, собранный интерфейс и учебное изображение уже внутри.

Сервис работает только на этом компьютере: обычно [http://localhost:8080](http://localhost:8080). Если порт занят, запуск выберет свободный; точный адрес будет в окне запуска. Там же показаны имя `admin` и автоматически созданный случайный пароль для входа. Гостю доступен просмотр фотографий. Уже изменённый пароль учётной записи при перезапуске не сбрасывается.

Для остановки дважды нажмите **`stop-portable.cmd`** или нажмите **Ctrl+C** в окне запуска и дождитесь завершения. Для переноса сначала остановите сервис, затем скопируйте **всю распакованную папку вместе с `data/`** на флешку или другой компьютер с Windows x64. Фотографии, база и настройки останутся вместе с программой. Свежий архив релиза не содержит пользовательских данных или готовых паролей.

**GitHub → Code → Download ZIP**, а также архивы **Source code** в релизе содержат исходники: в них нет `runtime/` и собранного `frontend/dist/`. Они **не подходят для запуска офлайн двойным щелчком**. Для этого нужен именно файл `LocalInterfaceWeb-Windows-x64.zip`.

Если выполнение программ запрещено политиками учреждения (например, AppLocker), требуется разрешение ответственного администратора.

Подробности, работа с БД и решение проблем — [инструкция переносной версии](docs/portable.md).

## Дополнительный вариант: Docker

Этот способ нужен для контейнерного окружения и запуска на macOS/Linux; для переносной Windows-версии он не требуется.

1. Установите и запустите [Docker Desktop](https://docs.docker.com/desktop/) с Linux containers. На Linux подходит Docker Engine с Compose 2.24+. Первый запуск требует интернет для образов и пакетов, свободного порта 8080 и нескольких гигабайт места. На Windows Docker Desktop может потребовать WSL2/виртуализацию и перезагрузку.
2. Скачайте [исходники ZIP](https://github.com/arxey/LocalInterfaceWeb/archive/refs/heads/main.zip), распакуйте. В Windows запустите **`start.cmd`**; в macOS/Linux выполните **`sh start.sh`** в папке проекта.

После сборки откроется [http://localhost:8080](http://localhost:8080). Скрипт показывает первоначальные имя и пароль администратора. Пароли и секрет Django создаются автоматически в `.env.docker`. Отдельные Python, Node.js и PostgreSQL на компьютере для этого способа не нужны.

Остановка: **`stop.cmd`** или **`sh stop.sh`**. Закрытие стартового окна не останавливает контейнеры. Данные остаются в именованных томах Docker. Вторая копия исходников с тем же Compose project name использует ту же установку. Не удаляйте тома с нужными фотографиями. Данные Docker и папка `data/` переносной версии — разные хранилища; копирование исходников не переносит Docker-тома.

## PostgreSQL и pgAdmin

**В переносной версии** PostgreSQL уже включён и работает локальным процессом, обычно на `127.0.0.1:55432`; занятый порт заменяется свободным. pgAdmin для работы фотоархива не нужен. Для необязательного подключения имеющегося pgAdmin или встроенного `psql` используйте параметры из [инструкции](docs/portable.md#подключение-к-базе-по-желанию); выбранный порт и локальные секреты находятся в `data/config.json`. Этот файл нельзя публиковать в Git или отправлять вместе с отчётом.

**В Docker-варианте** после старта можно включить pgAdmin:

```sh
docker compose --env-file .env.docker --profile tools up -d pgadmin
```

Откройте [http://localhost:5050](http://localhost:5050). Учетные данные находятся в `.env.docker` (`PGADMIN_DEFAULT_EMAIL`, `PGADMIN_DEFAULT_PASSWORD`). При регистрации сервера: Host `db`, Port `5432`, Maintenance database — `POSTGRES_DB`, Username — `POSTGRES_USER`, Password — `POSTGRES_PASSWORD`. Это адреса внутри Compose; порт БД не выставлен на компьютер.

```sql
SELECT id, title, search_title, image, thumbnail, created_at, updated_at
FROM photos_photo ORDER BY created_at DESC, id DESC;
```

Схема — [docs/database.md](docs/database.md). В обоих способах миграции и учебное изображение применяются автоматически. Демоданные добавляются при первичной настройке администратора; удалённая пользователем демофотография после перезапуска не восстанавливается.

## Файлы проекта

| Каталог или файл | Содержимое |
|---|---|
| `start-portable.cmd`, `stop-portable.cmd`, `portable/` | Запуск и остановка переносной Windows-версии |
| `runtime/` | Встроенные Python и PostgreSQL; только в готовом релизе |
| `data/` | Создаваемые локально база, фото, настройки и журналы переносной версии |
| `backend/` | Django API, авторизация, обработка изображений, миграции, тесты |
| `frontend/` | React/TypeScript, интерфейс и клиентские тесты; `dist/` включён в готовый релиз |
| `deployment/`, `compose.yaml`, `start.cmd`, `start.sh` | Необязательный контейнерный запуск |
| `scripts/` | Сборка и проверки |
| `demo/`, `docs/` | Учебное изображение и документация |

## Разработка в PyCharm или VS Code

Редактор нужен только для изменения кода. Для серверной разработки установите Python 3.12+, PostgreSQL и создайте виртуальное окружение. Создайте локальный `.env` по `.env.example`, задайте собственный `DJANGO_SECRET_KEY` и параметры тестовой БД. Секрет можно создать командой `python -c "import secrets; print(secrets.token_urlsafe(64))"`. Реальные настройки не включаются в Git.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
cd backend
..\.venv\Scripts\python.exe manage.py migrate
..\.venv\Scripts\python.exe manage.py createsuperuser
..\.venv\Scripts\python.exe manage.py load_demo
..\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Для разработки клиента установите Node.js 24 и версию pnpm из `frontend/package.json`. В `frontend`: `pnpm install --frozen-lockfile`, затем `pnpm dev`. Клиент открывается на [http://127.0.0.1:5173](http://127.0.0.1:5173); Vite проксирует `/api/`, `/media/`, `/admin/` и `/static/` в Django. Для раздачи файлов через Django в этом режиме установите `DJANGO_DEBUG=1`; переносная версия использует Waitress и `DEBUG=0`, контейнерная — Gunicorn/Nginx. В PyCharm выберите `.venv/Scripts/python.exe` (Windows), `backend/manage.py` и рабочий каталог `backend`.

## Тестирование и ревью

Для запущенного контейнерного проекта:

```sh
docker compose --env-file .env.docker exec backend python manage.py test
docker compose --env-file .env.docker exec backend python manage.py check
```

Клиентские проверки из `frontend`: `pnpm test`, `pnpm build`, `pnpm audit`. CI проверяет контейнерный и переносной запуск, основные операции с фото и сохранность данных после перезапуска. Реально выполненные проверки и границы ревью — [docs/review.md](docs/review.md), соответствие методичке — [docs/requirements.md](docs/requirements.md).

## Сохранение данных и публикация

Для переносной версии: остановите сервис и скопируйте всю папку, включая `data/`. Не переносите работающий PostgreSQL и не копируйте только фотографии без базы и `config.json`. Подробности обновления и резервного копирования — [docs/portable.md](docs/portable.md).

Для Docker: остановите изменения (`docker compose --env-file .env.docker stop web backend`), сохраните дамп PostgreSQL, том media и `.env.docker`, затем снова выполните стартовый скрипт. Имена томов смотрите в `docker volume ls`.

GitHub содержит **исходники и готовый Windows-архив в Releases**; само приложение работает локально. Оба способа по умолчанию слушают только 127.0.0.1. Для внешнего сервера требуются домен, HTTPS, точные ALLOWED_HOSTS/CSRF origins, Secure cookies и политика доступа к фотографиям. Правила — [SECURITY.md](SECURITY.md).
