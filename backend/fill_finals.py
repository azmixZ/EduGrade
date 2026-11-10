# backend/fill_finals.py
"""
Быстро проставляет «типа итоговые» оценки всем студентам во всех ведомостях (для показа/проверки системы).

    python fill_finals.py                  # заполнить все пустые ведомости
    python fill_finals.py --groups ПР-1-25,ЗУ-1-24      # только эти группы
    python fill_finals.py --teachers nazhikanov         # только у этих преподавателей (логины)
    python fill_finals.py --dead 2         # + около 2% студентов «мёртвых душ» (для вкладки «Группа риска»)
    python fill_finals.py --overwrite      # перезаписать итоговые, даже если они уже стоят
    python fill_finals.py --clear          # удалить ВСЕ итоговые оценки

Ведомости, в которых уже есть итоговые оценки, пропускаются (если не указан --overwrite). Баллы 0–100, у каждого
студента свой уровень, у каждого предмета своя сложность, поэтому группы получаются разными. Пользователей,
группы, студентов и нагрузку скрипт не трогает.
"""
import argparse
import os
import random
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FINAL = "ИТОГ"


def final_lesson(cur, wid):
    """id итоговой колонки ведомости (создаёт, если её нет)."""
    row = cur.execute("SELECT id FROM lessons WHERE workload_id=? AND lesson_type='final' ORDER BY id LIMIT 1", (wid,)).fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO lessons (workload_id, date_str, lesson_type) VALUES (?, ?, 'final')", (wid, FINAL))
    return cur.lastrowid


def main():
    ap = argparse.ArgumentParser(description="Проставить итоговые оценки (демо)")
    ap.add_argument("--db", default=os.path.join(HERE, "kkci.db"))
    ap.add_argument("--groups", default="")
    ap.add_argument("--teachers", default="")
    ap.add_argument("--dead", type=float, default=0.0, help="процент «мёртвых душ» (0 = нет)")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--yes", action="store_true", help="не спрашивать подтверждение")
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print("❌ База не найдена:", args.db, "— сначала: python setup_db.py")
        return 1
    con = sqlite3.connect(args.db)
    cur = con.cursor()
    try:
        have = {r[1] for r in cur.execute("PRAGMA table_info(lessons)")}
        if "lesson_type" not in have:
            print("❌ В базе нет колонки lessons.lesson_type — запустите сервер один раз или python setup_db.py")
            return 1

        if args.clear:
            if not args.yes and input("Удалить ВСЕ итоговые оценки? Введите «да»: ").strip().lower() != "да":
                print("Отменено.")
                return 0
            ids = [r[0] for r in cur.execute("SELECT id FROM lessons WHERE lesson_type='final'")]
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                cur.execute(f"DELETE FROM grades WHERE lesson_id IN ({','.join('?' * len(chunk))})", chunk)
            con.commit()
            print(f"🧹 Итоговые оценки удалены (ведомостей затронуто: {len(ids)}).")
            return 0

        rng = random.Random(args.seed)
        sql = ("SELECT w.id, w.group_id, g.name, u.username FROM workloads w JOIN groups g ON g.id = w.group_id "
               "JOIN users u ON u.id = w.teacher_id WHERE u.role = 'teacher' ORDER BY g.name, w.id")
        wls = cur.execute(sql).fetchall()
        if args.groups:
            want = {x.strip().lower() for x in args.groups.split(",") if x.strip()}
            wls = [w for w in wls if w[2].lower() in want]
        if args.teachers:
            want = {x.strip().lower() for x in args.teachers.split(",") if x.strip()}
            wls = [w for w in wls if w[3].lower() in want]
        if not wls:
            print("Нечего заполнять: нет подходящей нагрузки (проверьте фильтры и что загружена нагрузка преподавателей).")
            return 0

        students = {}
        for sid, gid in cur.execute("SELECT id, group_id FROM students ORDER BY id"):
            students.setdefault(gid, []).append(sid)

        level, mode = {}, {}
        for gid, ids in students.items():
            mu = rng.gauss(72, 5)                                   # «сила» группы
            for sid in ids:
                level[sid] = min(97, max(28, rng.gauss(mu, 13)))    # уровень студента
        if args.dead > 0:
            n = 0
            for ids in students.values():
                for sid in rng.sample(ids, min(len(ids), max(1, round(len(ids) * args.dead / 100)))):
                    mode[sid] = "ghost" if n % 3 == 0 else "absent"
                    n += 1

        done = skipped = n_grades = 0
        for wid, gid, gname, _ in wls:
            lid = final_lesson(cur, wid)
            has = cur.execute("SELECT COUNT(*) FROM grades WHERE lesson_id=?", (lid,)).fetchone()[0]
            if has and not args.overwrite:
                skipped += 1
                continue
            if has:
                cur.execute("DELETE FROM grades WHERE lesson_id=?", (lid,))
            off = rng.gauss(0, 4)                                   # сложность предмета
            rows = []
            for sid in students.get(gid, []):
                m = mode.get(sid)
                if m == "ghost":
                    continue                                        # итоговых нет вообще
                if m == "absent":
                    val = "Н" if rng.random() < 0.7 else None
                else:
                    val = None if rng.random() < 0.02 else str(int(min(100, max(0, round(rng.gauss(level[sid] + off, 6))))))
                if val is not None:
                    rows.append((sid, lid, val))
            cur.executemany("INSERT INTO grades (student_id, lesson_id, value) VALUES (?, ?, ?)", rows)
            n_grades += len(rows)
            done += 1
        con.commit()
        print(f"✅ Заполнено ведомостей: {done} (пропущено уже заполненных: {skipped}) · итоговых оценок: {n_grades}")
        if skipped and not args.overwrite:
            print("   Чтобы перезаписать заполненные, добавьте --overwrite.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
