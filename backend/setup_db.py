# backend/setup_db.py
"""
Создание базы данных «с нуля» одной командой (вместо запуска seed.py и остальных скриптов по очереди).

    python setup_db.py                   # создать/дополнить базу (безопасно запускать повторно)
    python setup_db.py --fresh           # УДАЛИТЬ kkci.db и собрать заново
    python setup_db.py --demo            # + заполнить журналы демо-оценками (для показа)
    python setup_db.py --fresh --demo --yes

Что делает по порядку:
  1. (--fresh) удаляет старую kkci.db
  2. создаёт таблицы и недостающие колонки
  3. seed.py                 — отделения, специальности, 29 групп и студенты
  4. seed_students_fix.py    — ещё 9 групп и исправление ошибки со списками
  5. seed_workload.py        — нагрузка из Excel-планов (папка plans/) и аккаунты преподавателей
  6. создаёт администратора admin / admin, если его нет
  7. (--demo) demo_fill.py   — демонстрационные оценки

Файлы .secret_key и teachers_new.csv скрипт не удаляет.
"""
import argparse
import os
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(HERE, "kkci.db")


def run_step(title, script, *args, required=True):
    """Запускает скрипт проекта. Обязательный шаг при ошибке останавливает установку."""
    print(f"\n━━ {title}")
    if not os.path.exists(os.path.join(HERE, script)):
        print(f"   {script} не найден — шаг пропущен.")
        return False
    env =dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    code = subprocess.call([sys.executable, os.path.join(HERE, script), *args], cwd=HERE, env=env)
    if code == 0:
        return True
    if required:
        print(f"\n❌ Шаг «{title}» завершился с ошибкой (код {code}). Установка остановлена.")
        sys.exit(code or 1)
    print(f"⚠️ Шаг «{title}» не выполнен (код {code}) — установка продолжается.")
    return False


def init_schema():
    sys.path.insert(0, HERE)
    try:
        from migrate import init_db
    except ImportError as e:
        print(f"❌ Не удалось загрузить проект ({e}). Установите зависимости: pip install -r requirements.txt")
        sys.exit(1)
    init_db()
    print("Таблицы и колонки готовы.")


def ensure_admin():
    sys.path.insert(0, HERE)
    try:
        from database import SessionLocal
        import models
        from auth import get_password_hash
    except ImportError as e:
        print(f"⚠️ Не удалось проверить администратора: {e}")
        return
    db = SessionLocal()
    try:
        if db.query(models.User).filter(models.User.username == "admin").first():
            print("Администратор уже существует.")
        else:
            db.add(models.User(username="admin", password_hash=get_password_hash("admin"),
                               full_name="Администратор ИС", role="admin",
                               must_change_password=True))
            db.commit()
            print("Создан администратор: admin / admin  (система потребует сменить пароль при первом входе)")
    finally:
        db.close()


def summary():
    if not os.path.exists(DB_FILE):
        return
    con = sqlite3.connect(DB_FILE)
    try:
        q = lambda sql: con.execute(sql).fetchone()[0]
        no_load = [r[0] for r in con.execute(
            "SELECT name FROM groups g WHERE NOT EXISTS (SELECT 1 FROM workloads w WHERE w.group_id = g.id) ORDER BY name")]
        print("\n" + "═" * 56)
        print("✅ БАЗА ГОТОВА")
        print(f"   Групп: {q('SELECT COUNT(*) FROM groups')} | студентов: {q('SELECT COUNT(*) FROM students')}")
        teachers = q("SELECT COUNT(*) FROM users WHERE role = 'teacher'")
        admins = q("SELECT COUNT(*) FROM users WHERE role = 'admin'")
        print(f"   Преподавателей: {teachers} | администраторов: {admins}")
        print(f"   Записей нагрузки: {q('SELECT COUNT(*) FROM workloads')} | колонок в журналах: "
              f"{q('SELECT COUNT(*) FROM lessons')} | оценок: {q('SELECT COUNT(*) FROM grades')}")
        if no_load:
            print(f"   Группы без нагрузки: {', '.join(no_load)}")
        print("═" * 56)
        print("Запуск сервера:  uvicorn main:app --reload   (из этой же папки)")
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description="Создание базы данных ИС Аттестация ККЦИ")
    ap.add_argument("--fresh", action="store_true", help="удалить kkci.db и собрать заново")
    ap.add_argument("--demo", action="store_true", help="заполнить пустые журналы демо-оценками")
    ap.add_argument("--plans", default="plans", help="папка с Excel-планами (по умолчанию plans)")
    ap.add_argument("--yes", action="store_true", help="не спрашивать подтверждение")
    args = ap.parse_args()
    os.chdir(HERE)      # база создаётся рядом со скриптами, откуда бы вы ни запустили

    if args.fresh and os.path.exists(DB_FILE):
        if not args.yes:
            ans = input("⚠️ Будет удалена kkci.db: ВСЕ оценки, колонки и журнал действий пропадут. Введите «да»: ")
            if ans.strip().lower() != "да":
                print("Отменено, база не тронута.")
                return 0
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                os.remove(DB_FILE + suffix)
            except FileNotFoundError:
                pass
        print("🗑  Старая база удалена.")

    print("\n━━ Создание таблиц")
    init_schema()
    run_step("Группы и студенты (seed.py)", "seed.py")
    run_step("Недостающие группы и исправление списков (seed_students_fix.py)", "seed_students_fix.py")
    run_step("Нагрузка преподавателей из Excel-планов (seed_workload.py)", "seed_workload.py",
             "--reset", "--dir", args.plans, required=False)
    print("\n━━ Администратор")
    ensure_admin()
    if args.demo:
        run_step("Демо-оценки (demo_fill.py)", "demo_fill.py", "--yes", required=False)
    summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
