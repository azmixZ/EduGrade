# backend/periods.py
"""Периоды аттестации учебного года.

oct — текущая аттестация (октябрь), s1 — итоги 1 семестра, mar — текущая аттестация (март), s2 — итоги 2 семестра.
Итоги семестра у обычных групп называются «промежуточная аттестация», у выпускных — «итоговая аттестация».

Каждая ведомость (нагрузка) хранит по одной итоговой колонке (lessons) на каждый период: lessons.period.
Преподаватель всегда работает в периоде, который сейчас идёт по календарю; куратор и администратор могут смотреть
любой период, а исправлять оценки прошлых периодов — только администратор.
"""
from datetime import date
from typing import Optional

from fastapi import HTTPException

PERIODS = ["oct", "s1", "mar", "s2"]
TITLES = {
    "oct": "Текущая аттестация (октябрь)",
    "s1": "Итоги 1 семестра",
    "mar": "Текущая аттестация (март)",
    "s2": "Итоги 2 семестра",
}
COLUMN = {"oct": "Октябрь", "s1": "Итоги 1 сем.", "mar": "Март", "s2": "Итоги 2 сем."}   # подпись колонки в Excel
SEMESTER_END = {"s1", "s2"}

# Когда какой период открыт преподавателям (месяц -> период)
BY_MONTH = {9: "oct", 10: "oct", 11: "oct",
            12: "s1", 1: "s1",
            2: "mar", 3: "mar", 4: "mar",
            5: "s2", 6: "s2", 7: "s2", 8: "s2"}
WINDOWS = {"oct": "1 сентября – 30 ноября", "s1": "1 декабря – 31 января",
           "mar": "1 февраля – 30 апреля", "s2": "1 мая – 31 августа"}


def current(today: Optional[date] = None) -> str:
    return BY_MONTH[(today or date.today()).month]


def kind(period: str, graduating: bool = False) -> str:
    """Вид аттестации по нормативам."""
    if period in SEMESTER_END:
        return "итоговая аттестация" if graduating else "промежуточная аттестация"
    return "текущая аттестация"


def title(period: str, graduating: Optional[bool] = None) -> str:
    """«Итоги 1 семестра — промежуточная аттестация». graduating=None — без уточнения (для всего колледжа)."""
    t = TITLES[period]
    if period in SEMESTER_END:
        if graduating is None:
            return t + " (промежуточная / итоговая у выпускных)"
        return f"{t} — {kind(period, graduating)}"
    return t


def resolve(period: Optional[str]) -> str:
    """Период из запроса: пусто — текущий по календарю."""
    p = (period or "").strip()
    if not p:
        return current()
    if p not in PERIODS:
        raise HTTPException(status_code=400, detail="Неизвестный период аттестации")
    return p


def info() -> dict:
    cur = current()
    return {"current": cur, "periods": [{"code": p, "title": TITLES[p], "window": WINDOWS[p], "current": p == cur}
                                        for p in PERIODS]}


_DEPT_CTES = [
    "groups AS (SELECT * FROM main.groups WHERE specialty_id IN "
    "(SELECT id FROM main.specialties WHERE department_id = :_dept))",
    "students AS (SELECT * FROM main.students WHERE group_id IN (SELECT id FROM groups))",
    "workloads AS (SELECT * FROM main.workloads WHERE group_id IN (SELECT id FROM groups))",
]


def scoped_sql(sql: str, period: bool = True, dept: bool = False) -> str:
    """Подставляет в запрос вместо таблиц их срезы: за один период (:_period) и/или по одному отделению (:_dept).
    Имена CTE в SQLite перекрывают имена таблиц, поэтому сами запросы в отчётах менять не нужно."""
    ctes = list(_DEPT_CTES) if dept else []
    only_dept = " AND gr.student_id IN (SELECT id FROM students)" if dept else ""
    if period:
        ctes += ["lessons AS (SELECT * FROM main.lessons WHERE period = :_period)",
                 "grades AS (SELECT gr.* FROM main.grades gr JOIN main.lessons pl ON pl.id = gr.lesson_id "
                 "WHERE pl.period = :_period" + only_dept + ")"]
    elif dept:
        ctes.append("grades AS (SELECT gr.* FROM main.grades gr WHERE 1 = 1" + only_dept + ")")
    return ("WITH " + ", ".join(ctes) + " " + sql) if ctes else sql


def runner(db, period: Optional[str], dept: Optional[int] = None):
    """q(sql, params) -> строки: только оценки указанного периода (None — все периоды)
    и, если задано отделение, только его группы, студенты и ведомости."""
    from sqlalchemy import text

    def q(sql, params=None):
        p = dict(params or {})
        if period:
            p["_period"] = period
        if dept is not None:
            p["_dept"] = dept
        return db.execute(text(scoped_sql(sql, bool(period), dept is not None)), p).fetchall()
    return q
