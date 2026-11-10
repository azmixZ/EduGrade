# backend/convert_to_finals.py
"""
Разовый перевод существующей базы на ИТОГОВЫЕ оценки.

    python convert_to_finals.py            # посчитать и показать, что изменится (ничего не пишет)
    python convert_to_finals.py --apply    # сделать резервную копию kkci.db и выполнить перевод

Для каждой ведомости (преподаватель + предмет + группа) все колонки с оценками за пары/экзамен заменяются одной
колонкой «ИТОГ». Итог студента = среднее его баллов, округлённое «от 0,5 вверх» (так же считали отчёты); если у студента
были только «Н» — итог «Н». Пустые колонки удаляются. Журнал действий не трогается. Повторный запуск безопасен.
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

from grading import points, round_half_up

HERE = os.path.dirname(os.path.abspath(__file__))


def plan(con):
    cur = con.cursor()
    todo = []
    for wid, in cur.execute("SELECT id FROM workloads").fetchall():
        lessons = cur.execute("SELECT id, COALESCE(lesson_type,'pair') FROM lessons WHERE workload_id=?", (wid,)).fetchall()
        if len(lessons) == 1 and lessons[0][1] == "final":
            continue                                    # уже итоговая
        if not lessons:
            continue
        per_student = {}
        for sid, val in cur.execute("SELECT g.student_id, g.value FROM grades g JOIN lessons l ON l.id=g.lesson_id "
                                    "WHERE l.workload_id=?", (wid,)):
            p = points(val)
            if p is not None:
                per_student.setdefault(sid, []).append((p, val))
        finals = {}
        for sid, lst in per_student.items():
            if all(v == "Н" for _, v in lst):
                finals[sid] = "Н"
            else:
                finals[sid] = str(round_half_up(sum(p for p, _ in lst) / len(lst)))
        todo.append((wid, [l[0] for l in lessons], finals))
    return todo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(HERE, "kkci.db"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not os.path.exists(args.db):
        print("❌ База не найдена:", args.db)
        return 1
    con = sqlite3.connect(args.db)
    try:
        todo = plan(con)
        n_les = sum(len(t[1]) for t in todo)
        n_fin = sum(len(t[2]) for t in todo)
        print(f"Ведомостей к переводу: {len(todo)} · колонок будет удалено: {n_les} · итоговых оценок будет создано: {n_fin}")
        if not todo:
            print("Нечего переводить — база уже в итоговом формате.")
            return 0
        if not args.apply:
            print("Это пробный запуск. Для выполнения добавьте --apply (перед этим создаётся резервная копия).")
            return 0
        backup = args.db + f".before_finals_{datetime.now():%Y%m%d_%H%M%S}.bak"
        shutil.copyfile(args.db, backup)
        print("💾 Резервная копия:", backup)
        cur = con.cursor()
        for wid, lesson_ids, finals in todo:
            marks = ",".join("?" * len(lesson_ids))
            cur.execute(f"DELETE FROM grades WHERE lesson_id IN ({marks})", lesson_ids)
            cur.execute(f"DELETE FROM lessons WHERE id IN ({marks})", lesson_ids)
            cur.execute("INSERT INTO lessons (workload_id, date_str, lesson_type) VALUES (?, 'ИТОГ', 'final')", (wid,))
            lid = cur.lastrowid
            cur.executemany("INSERT INTO grades (student_id, lesson_id, value) VALUES (?, ?, ?)",
                            [(sid, lid, v) for sid, v in finals.items()])
        con.commit()
        print("✅ Готово.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
