"""Browser integration against the running stack; credentials are never printed."""
import argparse
from pathlib import Path
import uuid

from playwright.sync_api import expect, sync_playwright


def exercise(browser, base_url, credentials, name):
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.goto(base_url)
    expect(page.get_by_role("heading", name="Фотографии", exact=True)).to_be_visible()
    expect(page.get_by_text("Осеннее озеро в горах", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Добавить фотографию", exact=True)).to_have_count(0)
    page.get_by_role("button", name="Просмотреть Осеннее озеро в горах", exact=True).click()
    page.wait_for_function("document.querySelector('.preview-frame img')?.naturalWidth > 0")
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    page.get_by_role("button", name="Войти", exact=True).click()
    page.get_by_label("Имя пользователя", exact=True).fill(credentials["ADMIN_USERNAME"])
    page.get_by_label("Пароль", exact=True).fill(credentials["ADMIN_PASSWORD"])
    page.get_by_role("button", name="Войти в архив", exact=True).click()
    expect(page.get_by_role("button", name="Выйти", exact=True)).to_be_visible()
    title = f"Проверка {name} {uuid.uuid4().hex[:8]}"
    page.get_by_role("button", name="Добавить фотографию", exact=True).click()
    page.get_by_label("Название фотографии", exact=True).fill(title)
    page.locator("#photo-image").set_input_files(str(Path(__file__).resolve().parents[1] / "demo/ural-lake.png"))
    page.get_by_role("button", name="Сохранить", exact=True).click()
    expect(page.get_by_text(title, exact=True)).to_be_visible(timeout=20000)
    page.get_by_label("Поиск по названию", exact=True).fill(title.upper())
    page.get_by_role("button", name="Найти", exact=True).click()
    expect(page.get_by_text(title, exact=True)).to_be_visible()
    page.get_by_role("button", name=f"Редактировать {title}", exact=True).click()
    page.wait_for_function("document.querySelector('.preview-frame img')?.naturalWidth > 0")
    page.get_by_label("Название фотографии", exact=True).fill(title + " изменена")
    page.get_by_role("button", name="Сохранить", exact=True).click()
    expect(page.get_by_text(title + " изменена", exact=True)).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    if page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"):
        raise RuntimeError(f"{name}: mobile document overflows")
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.on("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name=f"Удалить {title} изменена", exact=True).click()
    expect(page.get_by_text(title + " изменена", exact=True)).to_have_count(0)
    page.get_by_role("button", name="Выйти", exact=True).click()
    expect(page.get_by_role("button", name="Войти", exact=True)).to_be_visible()
    if errors:
        raise RuntimeError(f"{name}: browser errors: {errors}")
    print(f"Browser PASS: {name} {browser.version}; guest/original/login/upload/search/edit/delete/logout/mobile")
    page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--env-file", default=".env.docker", type=Path)
    parser.add_argument("--engines", nargs="+", default=["chromium", "firefox", "webkit"],
                        choices=["chromium", "firefox", "webkit"])
    args = parser.parse_args()
    credentials = dict(line.split("=", 1) for line in args.env_file.read_text(encoding="utf-8-sig").splitlines()
                       if line and not line.startswith("#") and "=" in line)
    with sync_playwright() as playwright:
        for name in args.engines:
            browser = getattr(playwright, name).launch()
            try:
                exercise(browser, args.base_url, credentials, name)
            finally:
                browser.close()


if __name__ == "__main__":
    main()
