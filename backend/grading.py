# backend/grading.py
"""100-балльная система (только стандартная библиотека).
90–100 = «5», 70–89 = «4», 50–69 = «3», меньше 50 = неаттестация; «Н» = 0 баллов."""
import math
import re
from datetime import date, timedelta
from typing import Optional

GRADE_ORDER = ["Н", "<50", "3", "4", "5"]    # корзины статистики (порядок важен для графиков)


def normalize_grade(raw: Optional[str]) -> Optional[str]:
    """'' -> '' (удалить оценку); 'Н' или целое 0–100 -> нормализованная строка; иначе None."""
    v = (raw or "").strip().upper().replace(" ", "")
    if v == "":
        return ""
    if v in ("Н", "H", "N", "НБ", "Н/Б"):      # кириллическая Н, латинские H/N и «н/б» -> «Н»
        return "Н"
    if re.fullmatch(r"[0-9]{1,3}", v) and int(v) <= 100:
        return str(int(v))
    return None


def points(value: Optional[str]) -> Optional[int]:
    """Баллы за оценку: «Н» = 0. None — значение нечитаемо (мусор из старых данных)."""
    v = (value or "").strip().upper()
    if v == "Н":
        return 0
    return int(v) if re.fullmatch(r"[0-9]{1,3}", v) else None


def bucket(value: Optional[str]) -> Optional[str]:
    """Корзина статистики: «Н», «<50» (неаттестация), «3», «4», «5»."""
    if (value or "").strip().upper() == "Н":
        return "Н"
    p = points(value)
    if p is None:
        return None
    return "<50" if p < 50 else "3" if p < 70 else "4" if p < 90 else "5"


def round_half_up(x: float) -> int:
    """Округление как в Excel и JavaScript (0.5 -> вверх), а не «банковское» round() из Python."""
    return int(math.floor(x + 0.5))


def final_grade(pts: float) -> str:
    """Итоговая оценка по среднему баллу."""
    return "5" if pts >= 90 else "4" if pts >= 70 else "3" if pts >= 50 else "неатт."


def calc_metrics(dist: dict, points_sum: int) -> dict:
    """Успеваемость = доля оценок от 50 баллов; средний балл по 100-балльной шкале (Н = 0)."""
    total = sum(dist.values())
    if total == 0:
        return {"uspev": 0, "avg": 0}
    passed = dist["3"] + dist["4"] + dist["5"]
    return {"uspev": round(passed * 100 / total, 1), "avg": round(points_sum / total, 1)}


_LESSON_DATE_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\b")


def lesson_date(date_str: Optional[str], today: Optional[date] = None) -> Optional[date]:
    """'12.10 Октябрь' -> date. Год определяется по учебному году (сентябрь–декабрь = год начала, январь–август = следующий);
    дата, уходящая в будущее больше чем на месяц, относится к прошлому году. 'ЭКЗАМЕН' и мусор -> None."""
    m = _LESSON_DATE_RE.match(date_str or "")
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    today = today or date.today()
    start = today.year if today.month >= 9 else today.year - 1
    year = start if month >= 9 else start + 1
    try:
        d = date(year, month, day)
        if d > today + timedelta(days=31):
            d = date(year - 1, month, day)
        return d
    except ValueError:
        return None
