# backend/migrate.py
"""Создание таблиц и безопасное добавление новых колонок в существующую kkci.db."""
from sqlalchemy import inspect, text

import models
from database import engine
from grading import lesson_date


def ensure_schema():
    wanted = {
        "lessons": {"lesson_type": "VARCHAR DEFAULT 'pair'", "date": "DATE", "period": "VARCHAR"},
        "audit_logs": {
            "group_name": "VARCHAR", "student_name": "VARCHAR",
            "subject_name": "VARCHAR", "month": "VARCHAR", "ip": "VARCHAR",
        },
        "groups": {"curator_id": "INTEGER", "contingent": "INTEGER", "graduating": "BOOLEAN NOT NULL DEFAULT 0"},
        "subjects": {"kind": "VARCHAR DEFAULT 'subject'"},
        "users": {
            "is_active": "BOOLEAN NOT NULL DEFAULT 1",
            "token_version": "INTEGER NOT NULL DEFAULT 0",
            "last_login": "VARCHAR",
            "must_change_password": "BOOLEAN NOT NULL DEFAULT 0",
            "seen_news": "VARCHAR",
            "head_department_id": "INTEGER",
        },
    }
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in wanted.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        try:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_grade_student_lesson "
                "ON grades(student_id, lesson_id)"
            ))
        except Exception as e:  # если в старой базе уже есть дубли оценок
            print("⚠️ Не удалось создать уникальный индекс оценок:", e)
        # выборки оценок по колонке (ведомости, отчёты, аналитика) без индекса сканируют всю таблицу
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_grades_lesson_id ON grades(lesson_id)"))
        # всё, что выставлено до появления периодов, — первая (октябрьская) аттестация
        conn.execute(text("UPDATE lessons SET period = 'oct' WHERE period IS NULL OR period = ''"))
        conn.execute(text("UPDATE lessons SET date_str = 'Октябрь' WHERE period = 'oct' AND lesson_type = 'final' AND date_str = 'ИТОГ'"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_lessons_workload_period ON lessons(workload_id, period)"))

    backfill_lesson_dates()


def backfill_lesson_dates():
    """Заполняет lessons.date из строки «12.10 Октябрь» там, где дата ещё не задана."""
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id, date_str FROM lessons WHERE date IS NULL AND date_str IS NOT NULL")).fetchall()
        for lid, ds in rows:
            d = lesson_date(ds)
            if d:
                conn.execute(text("UPDATE lessons SET date = :d WHERE id = :i"), {"d": d.isoformat(), "i": lid})


def init_db():
    """Создать недостающие таблицы и колонки. Безопасно вызывать сколько угодно раз."""
    models.Base.metadata.create_all(bind=engine)
    ensure_schema()
