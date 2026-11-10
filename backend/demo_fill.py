# backend/demo_fill.py
"""
Заполнение ведомостей демонстрационными ИТОГОВЫМИ оценками (для показа системы).

    python demo_fill.py                         # заполнить все ведомости, в которых ещё нет колонок
    python demo_fill.py --teachers nazhikanov   # только у этих преподавателей (через запятую)
    python demo_fill.py --groups ПР-1-25,ЗУ-1-24  # только эти группы
    python demo_fill.py --dead 3                # «подсадить» ~3% студентов-«мёртвых душ» (для показа модуля «Группа риска»)
    python demo_fill.py --reset                 # СНАЧАЛА удалить все колонки/оценки/журнал, потом заполнить
    python demo_fill.py --clear                 # только удалить все колонки, оценки и журнал действий

Оценки — по 100-балльной шкале; «Н» = 0 баллов. У каждого студента свой «уровень», поэтому в графиках
получаются живые, разные группы. Пользователей, группы, студентов и нагрузку скрипт не трогает.

Система хранит только итоговые оценки: в каждой ведомости одна колонка «ИТОГ», у каждого студента — балл 0–100 или «Н».
"""
import argparse
import os
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
MONTHS = {1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель", 5: "Май", 6: "Июнь",
          7: "Июль", 8: "Август", 9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"}


# ----------------------------------------------------------------------
# Генерация (чистая логика, без БД)
# ----------------------------------------------------------------------
def bucket(value):
    if value == "Н":
        return "Н"
    p = int(value)
    return "<50" if p < 50 else "3" if p < 70 else "4" if p < 90 else "5"


def lesson_dates(rng, n, today=None):
    """n случайных учебных дней (пн–пт) с 1 сентября текущего учебного года до сегодняшнего дня."""
    today = today or date.today()
    start = date(today.year if today.month >= 9 else today.year - 1, 9, 1)
    days = [start + timedelta(d) for d in range((today - start).days + 1)]
    days = [d for d in days if d.weekday() < 5]
    n = min(n, len(days))
    return sorted(rng.sample(days, n))


def date_label(d):
    return f"{d.day:02d}.{d.month:02d} {MONTHS[d.month]}"


def make_student_profiles(rng, student_ids, group_mu):
    """Для каждого студента: средний уровень (баллы) и вероятность пропуска."""
    prof = {}
    for sid in student_ids:
        level = min(97, max(28, rng.gauss(group_mu, 13)))
        absence = rng.choices([0.02, 0.05, 0.12, 0.25], weights=[55, 30, 11, 4])[0]
        prof[sid] = (level, absence)
    return prof


def draw_final(rng, level, offset=0.0, sd=6.0):
    """Итоговый балл за предмет (0–100): уровень студента + сложность предмета + небольшой разброс."""
    return str(int(min(100, max(0, round(rng.gauss(level + offset, sd))))))


# ----------------------------------------------------------------------
# Работа с БД (sqlite3)
# ----------------------------------------------------------------------
def ensure_columns(con):
    """Добавляет в старую базу колонки, в которые пишет этот скрипт (на новой базе ничего не делает)."""
    wanted = {
        "lessons": {"lesson_type": "VARCHAR DEFAULT 'pair'"},
        "audit_logs": {"group_name": "VARCHAR", "student_name": "VARCHAR",
                       "subject_name": "VARCHAR", "month": "VARCHAR", "ip": "VARCHAR"},
    }
    cur = con.cursor()
    for table, cols in wanted.items():
        have = {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}
        if not have:
            raise SystemExit(f"❌ В базе нет таблицы {table}. Сначала выполните: python setup_db.py")
        for name, ddl in cols.items():
            if name not in have:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    con.commit()


def clear_all(con):
    cur = con.cursor()
    counts = {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("grades", "lessons", "audit_logs")}
    for t in ("grades", "lessons", "audit_logs"):
        cur.execute(f"DELETE FROM {t}")
    con.commit()
    return counts


def fill(con, args):
    rng = random.Random(args.seed)
    cur = con.cursor()

    sql = ("SELECT w.id, w.group_id, g.name, s.name, u.username, u.full_name "
           "FROM workloads w JOIN groups g ON g.id = w.group_id "
           "JOIN subjects s ON s.id = w.subject_id JOIN users u ON u.id = w.teacher_id "
           "WHERE u.role = 'teacher' ORDER BY g.name, s.name")
    workloads = cur.execute(sql).fetchall()

    if args.teachers:
        wanted = {x.strip().lower() for x in args.teachers.split(",") if x.strip()}
        workloads = [w for w in workloads if w[4].lower() in wanted]
    if args.groups:
        wanted = {x.strip().lower() for x in args.groups.split(",") if x.strip()}
        workloads = [w for w in workloads if w[2].lower() in wanted]

    busy = {r[0] for r in cur.execute("SELECT DISTINCT workload_id FROM lessons")}
    skipped = sum(1 for w in workloads if w[0] in busy)
    workloads = [w for w in workloads if w[0] not in busy]
    if not workloads:
        print("Нечего заполнять: все выбранные ведомости уже содержат колонки "
              "(добавьте --reset, чтобы очистить и заполнить заново), либо фильтр ничего не нашёл.")
        return 0

    students = {}
    for sid, gid, name in cur.execute("SELECT id, group_id, full_name FROM students ORDER BY id"):
        students.setdefault(gid, []).append((sid, name))

    group_mu, profiles = {}, {}
    for gid, lst in students.items():
        group_mu[gid] = rng.gauss(72, 5)
        profiles[gid] = make_student_profiles(rng, [s[0] for s in lst], group_mu[gid])

    # «мёртвые души» для демонстрации: 1/3 — ни одной итоговой, 2/3 — почти везде «Н»
    dead_mode = {}
    if args.dead > 0:
        n_dead = 0
        for gid, lst in students.items():
            k = max(1, round(len(lst) * args.dead / 100))
            for sid, _ in rng.sample(lst, min(k, len(lst))):
                dead_mode[sid] = "ghost" if n_dead % 3 == 0 else "absent"     # чередуем по всей базе
                n_dead += 1

    stats = {"Н": 0, "<50": 0, "3": 0, "4": 0, "5": 0}
    n_lessons = n_grades = 0
    candidates = []                         # для правдоподобного журнала действий
    for k, (wid, gid, gname, subject, username, teacher_name) in enumerate(workloads, 1):
        group_students = students.get(gid, [])
        if not group_students:
            continue
        subject_offset = rng.gauss(0, 4)   # одни предметы «сложнее» других
        cur.execute("INSERT INTO lessons (workload_id, date_str, lesson_type) VALUES (?, 'ИТОГ', 'final')", (wid,))
        lesson_id = cur.lastrowid
        n_lessons += 1
        rows = []
        for sid, sname in group_students:
            level, _absence = profiles[gid][sid]
            mode = dead_mode.get(sid)
            if mode == "ghost":
                continue                                            # итоговых нет вообще
            if mode == "absent":
                val = "Н" if rng.random() < 0.7 else None           # почти везде «Н», часть пустая
            else:
                val = None if rng.random() < 0.02 else draw_final(rng, level, subject_offset)
            if val is None:
                continue
            rows.append((sid, lesson_id, val))
            stats[bucket(val)] += 1
            if rng.random() < 0.01:
                candidates.append((teacher_name, gname, sname, subject, "ИТОГ", val))
        cur.executemany("INSERT INTO grades (student_id, lesson_id, value) VALUES (?, ?, ?)", rows)
        n_grades += len(rows)
        if k % 100 == 0:
            con.commit()
            print(f"   ... {k}/{len(workloads)} ведомостей")

    # журнал действий: правдоподобные записи за последние дни
    n_logs = min(args.logs, len(candidates))
    now = datetime.now()
    stamps = sorted(now - timedelta(minutes=rng.randint(5, 60 * 24 * 4)) for _ in range(n_logs))
    for (teacher_name, gname, sname, subject, label, val), ts in zip(rng.sample(candidates, n_logs), stamps):
        month = ""
        cur.execute(
            "INSERT INTO audit_logs (time, teacher, action, group_name, student_name, subject_name, month, ip) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts.strftime("%d.%m.%Y %H:%M"), teacher_name, f"Выставил итоговую оценку {val}", gname, sname, subject,
             month, f"192.168.{rng.randint(0, 3)}.{rng.randint(10, 250)}"))
    con.commit()

    total = sum(stats.values()) or 1
    print(f"\n✅ Заполнено ведомостей: {len(workloads)} (пропущено уже заполненных: {skipped})")
    print(f"   Итоговых оценок: {n_grades}, записей в журнале действий: {n_logs}")
    print("   Распределение оценок:  "
          f"5 (90–100): {stats['5'] * 100 // total}%  |  4 (70–89): {stats['4'] * 100 // total}%  |  "
          f"3 (50–69): {stats['3'] * 100 // total}%  |  неаттестация (<50): {stats['<50'] * 100 // total}%  |  "
          f"Н: {stats['Н'] * 100 // total}%")
    return n_grades


def main():
    ap = argparse.ArgumentParser(description="Демо-оценки для показа системы")
    ap.add_argument("--db", default=os.path.join(HERE, "kkci.db"), help="путь к базе (по умолчанию kkci.db рядом со скриптом)")
    ap.add_argument("--teachers", default="", help="логины преподавателей через запятую")
    ap.add_argument("--groups", default="", help="названия групп через запятую")
    ap.add_argument("--logs", type=int, default=80, help="сколько записей добавить в журнал действий")
    ap.add_argument("--dead", type=float, default=0.0, help="процент студентов-«мёртвых душ» для демонстрации (0 = нет)")
    ap.add_argument("--seed", type=int, default=2026, help="число для воспроизводимости")
    ap.add_argument("--clear", action="store_true", help="только удалить все колонки, оценки и журнал действий")
    ap.add_argument("--reset", action="store_true", help="удалить всё и заполнить заново")
    ap.add_argument("--yes", action="store_true", help="не спрашивать подтверждение")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"❌ База не найдена: {args.db}. Сначала выполните: python setup_db.py")
        return 1

    con = sqlite3.connect(args.db)
    try:
        ensure_columns(con)
        if args.clear or args.reset:
            if not args.yes:
                if input("Удалить ВСЕ колонки, оценки и журнал действий? Введите «да»: ").strip().lower() != "да":
                    print("Отменено.")
                    return 0
            c = clear_all(con)
            print(f"🧹 Удалено: колонок {c['lessons']}, оценок {c['grades']}, записей журнала {c['audit_logs']}")
            if args.clear:
                return 0
        fill(con, args)
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
