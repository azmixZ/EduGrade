# run.py — единый запуск проекта (лежит в корне проекта, рядом с папками backend и frontend).
"""
    python run.py              меню (то же, что двойной щелчок по start.bat)
    python run.py start        запустить сайт (при необходимости создаст базу и поставит зависимости)
    python run.py clear        удалить все оценки и журнал действий (преподаватели, группы, пароли останутся)
    python run.py new          создать базу с нуля, БЕЗ оценок (преподаватели получат новые пароли)
    python run.py demo         удалить оценки и проставить демонстрационные итоговые
    добавьте --yes, чтобы не спрашивать подтверждение
Перед любой разрушающей операцией автоматически делается копия базы в папку backups/.
"""
import argparse
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(ROOT, "backend")
DB = os.path.join(BACKEND, "kkci.db")
API_PORT, WEB_PORT = 8000, 5500
ENV = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")

NEEDED = {"fastapi": "fastapi", "uvicorn": "uvicorn", "sqlalchemy": "sqlalchemy", "jose": "python-jose[cryptography]",
          "bcrypt": "bcrypt", "multipart": "python-multipart", "openpyxl": "openpyxl", "xlrd": "xlrd"}


def frontend_dir():
    for d in (os.path.join(ROOT, "frontend"), ROOT):
        if os.path.exists(os.path.join(d, "index.html")):
            return d
    return None


def say(text=""):
    print(text, flush=True)


def ask_yes(question, yes):
    if yes:
        return True
    return input(f"{question} Введите «да»: ").strip().lower() in ("да", "yes", "y")


def run_script(name, *args):
    return subprocess.call([sys.executable, os.path.join(BACKEND, name), *args], cwd=BACKEND, env=ENV)


def port_busy(port):
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def backup_db():
    if not os.path.exists(DB):
        return None
    folder = os.path.join(ROOT, "backups")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"kkci_{datetime.now():%Y%m%d_%H%M%S}.db")
    shutil.copyfile(DB, path)
    return path


# ---------------------------------------------------------------- подготовка
def ensure_deps():
    missing = [pkg for mod, pkg in NEEDED.items() if importlib.util.find_spec(mod) is None]
    if not missing:
        return True
    say(f"Устанавливаю недостающие библиотеки: {', '.join(missing)} …")
    req = os.path.join(BACKEND, "requirements.txt")
    cmd = [sys.executable, "-m", "pip", "install", "-r", req] if os.path.exists(req) else [sys.executable, "-m", "pip", "install", *missing]
    if subprocess.call(cmd, env=ENV) != 0:
        say("Не удалось установить библиотеки (проверьте интернет) и запустите снова.")
        return False
    return True


def ensure_db():
    if os.path.exists(DB):
        return True
    say("Базы данных ещё нет — создаю (группы, студенты, преподаватели, администратор)…")
    if run_script("setup_db.py", "--yes") != 0:
        say("Не удалось создать базу. Текст ошибки выше.")
        return False
    return True


# ---------------------------------------------------------------- действия
def start():
    if not ensure_deps() or not ensure_db():
        return 1
    if port_busy(API_PORT):
        say(f"Порт {API_PORT} уже занят — вероятно, сайт уже запущен в другом окне. Закройте его и повторите.")
        return 1

    web = None
    fdir = frontend_dir()
    if fdir and not port_busy(WEB_PORT):
        class Quiet(SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def end_headers(self):                       # без кэша: правки в JS/CSS видны сразу после F5
                self.send_header("Cache-Control", "no-store")
                super().end_headers()
        web = ThreadingHTTPServer(("127.0.0.1", WEB_PORT), partial(Quiet, directory=fdir))
        threading.Thread(target=web.serve_forever, daemon=True).start()

    say(f"\nСервер запускается… (остановить: Ctrl+C или закрыть окно)")
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(API_PORT)],
                           cwd=BACKEND, env=ENV)
    for _ in range(60):                                  # ждём, пока сервер начнёт отвечать
        if api.poll() is not None:
            say("Сервер остановился с ошибкой — текст выше.")
            return 1
        if port_busy(API_PORT):
            break
        time.sleep(0.5)
    url = f"http://127.0.0.1:{WEB_PORT}/index.html" if web else None
    if url:
        say(f"\n✅ Сайт: {url}\n   Вход: admin (при первом входе система попросит сменить пароль)")
        webbrowser.open(url)
    else:
        say(f"\n✅ API работает на http://127.0.0.1:{API_PORT}. Откройте frontend/index.html.")
    try:
        api.wait()
    except KeyboardInterrupt:
        say("\nОстанавливаю…")
        api.terminate()
        try:
            api.wait(5)
        except subprocess.TimeoutExpired:
            api.kill()
    return 0


def clear(yes):
    if not os.path.exists(DB):
        say("Базы ещё нет — очищать нечего.")
        return 0
    if not ask_yes("Удалить ВСЕ оценки и журнал действий? Преподаватели, группы, студенты и пароли останутся.", yes):
        say("Отменено.")
        return 0
    say(f"💾 Копия базы: {backup_db()}")
    return run_script("demo_fill.py", "--clear", "--yes")


def new_db(yes):
    if not ask_yes("Создать базу С НУЛЯ? Старая будет заменена, преподаватели получат НОВЫЕ пароли.", yes):
        say("Отменено.")
        return 0
    if not ensure_deps():
        return 1
    if os.path.exists(DB):
        say(f"💾 Копия старой базы: {backup_db()}")
    code = run_script("setup_db.py", "--fresh", "--yes")
    if code == 0:
        say("\nГотово: оценок нет. Пароли преподавателей — в backend/teachers_new.csv (или выдайте новые в админке).")
    return code


def demo(yes):
    if not ensure_db():
        return 1
    if not ask_yes("Заменить все оценки демонстрационными итоговыми (с «мёртвыми душами» для показа)?", yes):
        say("Отменено.")
        return 0
    say(f"💾 Копия базы: {backup_db()}")
    return run_script("demo_fill.py", "--reset", "--yes", "--dead", "2")


def menu():
    while True:
        say("\n=== EduGrade ===")
        say(" 1. Запустить сайт  (по умолчанию — просто Enter)")
        say(" 2. Очистить все оценки (преподаватели и группы остаются)")
        say(" 3. Создать базу с нуля, без оценок")
        say(" 4. Демо: проставить тестовые итоговые оценки")
        say(" 0. Выход")
        choice = input("Выбор: ").strip() or "1"
        if choice == "1":
            return start()
        if choice == "2":
            clear(False)
        elif choice == "3":
            new_db(False)
        elif choice == "4":
            demo(False)
        elif choice == "0":
            return 0


def main():
    ap = argparse.ArgumentParser(description="Запуск EduGrade")
    ap.add_argument("action", nargs="?", choices=["start", "clear", "new", "demo"])
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    if not os.path.isdir(BACKEND):
        say(f"Не найдена папка backend рядом с run.py: {BACKEND}")
        return 1
    if a.action is None:
        return menu()
    return {"start": start, "clear": lambda: clear(a.yes), "new": lambda: new_db(a.yes), "demo": lambda: demo(a.yes)}[a.action]()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
