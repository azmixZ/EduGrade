# backend/risk.py
"""
Модуль «Группа риска»: слабые группы, слабые студенты, «мёртвые души».

Работает по той же схеме, что и reports.py: на вход получает функцию q(sql, params) -> список строк,
поэтому от FastAPI не зависит и легко проверяется отдельно.

Шкала 100-балльная: «Н» = 0 баллов, ниже 50 = неаттестация (как в grading.py / app.js).
"""
from collections import defaultdict
from dataclasses import dataclass, asdict
from io import BytesIO
from statistics import mean

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from grading import round_half_up

PASS_POINTS = 50            # ниже — неаттестация
MIN_SUBJECT_GRADES = 1      # итоговая оценка по предмету одна, поэтому достаточно её одной
UNIFORM_MIN_GRADES = 15     # «одинаковые оценки» проверяем, если оценок не меньше
UNIFORM_SHARE = 0.9         # ...и одно и то же значение занимает не менее 90% оценок

# Баллы в SQL: «Н» = 0, число = число, всё остальное (мусор) игнорируется
PTS = "CASE WHEN g.value = 'Н' THEN 0 WHEN g.value GLOB '[0-9]*' THEN CAST(g.value AS INTEGER) END"


@dataclass
class Params:
    """Пороги. Все можно переопределять из интерфейса (query-параметры)."""
    weak_avg: float = 55.0        # студент слабый, если средний балл ниже
    fail_subjects: int = 3        # ...или не аттестован минимум по стольким предметам
    abs_warn: float = 0.25        # ...или доля «Н» не ниже
    dead_abs: float = 0.70        # «мёртвая душа»: доля «Н» не ниже
    dead_cov: float = 0.40        # «мёртвая душа»: заполнено меньше этой доли ячеек группы
    group_avg: float = 60.0       # группа слабая, если средний балл ниже
    group_unatt: float = 0.15     # ...или доля неаттестованных студентов не ниже
    group_weak: float = 0.35      # ...или доля слабых студентов не ниже

    @classmethod
    def from_query(cls, **kw) -> "Params":
        """Берёт только переданные значения, ограничивает диапазоны, мусор заменяет значением по умолчанию."""
        d = cls()
        rng = {
            "weak_avg": (0, 100), "fail_subjects": (1, 30), "abs_warn": (0, 1), "dead_abs": (0, 1),
            "dead_cov": (0, 1), "group_avg": (0, 100), "group_unatt": (0, 1), "group_weak": (0, 1),
        }
        for k, (lo, hi) in rng.items():
            v = kw.get(k)
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            v = max(lo, min(hi, v))
            setattr(d, k, int(v) if k == "fail_subjects" else v)
        return d


def _fmt_date(iso):
    """'2026-10-12' -> '12.10.2026'; пусто -> '—'."""
    if not iso:
        return "—"
    y, m, d = str(iso)[:10].split("-")
    return f"{d}.{m}.{y}"


def _pct(x: float) -> float:
    return round(x * 100, 1)


# ==========================================================
# РАСЧЁТ
# ==========================================================
def compute(q, p: Params) -> dict:
    # --- справочник групп
    groups_raw = q("""
        SELECT g.id, g.name, COALESCE(d.name, 'Без отделения'), COALESCE(sp.code, '')
        FROM groups g
        LEFT JOIN specialties sp ON sp.id = g.specialty_id
        LEFT JOIN departments d ON d.id = sp.department_id
        ORDER BY g.name""")
    groups = {r[0]: {"id": r[0], "name": r[1], "department": r[2], "specialty": r[3]} for r in groups_raw}

    # --- студенты
    students = {r[0]: {"id": r[0], "name": r[1], "group_id": r[2]}
                for r in q("SELECT id, full_name, group_id FROM students")}

    # --- сколько всего «ячеек» оценок ожидается у студента группы (колонки всех ведомостей группы)
    group_workloads = dict(q("SELECT group_id, COUNT(*) FROM workloads GROUP BY group_id"))
    group_cells = dict(q("""
        SELECT w.group_id, COUNT(l.id) FROM workloads w JOIN lessons l ON l.workload_id = w.id
        GROUP BY w.group_id"""))

    # --- распределение значений по студенту: считаем баллы, «Н», самое частое значение
    st = defaultdict(lambda: {"n": 0, "pts": 0, "abs": 0, "top": 0})
    for sid, val, cnt, pts_sum in q(f"""
        SELECT g.student_id, g.value, COUNT(*), SUM({PTS})
        FROM grades g WHERE ({PTS}) IS NOT NULL
        GROUP BY g.student_id, g.value"""):
        s = st[sid]
        s["n"] += cnt
        s["pts"] += pts_sum or 0
        if val == "Н":
            s["abs"] += cnt
        s["top"] = max(s["top"], cnt)

    # --- дата последней реальной оценки (не «Н») — по lessons.date; в старой базе без этой колонки просто пусто
    last_grade = {}
    try:
        last_grade = {sid: str(d) for sid, d in q(
            "SELECT g.student_id, MAX(l.date) FROM grades g JOIN lessons l ON l.id = g.lesson_id "
            "WHERE l.date IS NOT NULL AND g.value <> 'Н' GROUP BY g.student_id") if d}
    except Exception:
        pass

    # --- предметы: средний балл студента по каждому предмету
    subj = defaultdict(list)       # student_id -> [(предмет, n, avg)]
    for sid, subj_name, cnt, pts_sum in q(f"""
        SELECT g.student_id, sb.name, COUNT(*), SUM({PTS})
        FROM grades g
        JOIN lessons l ON l.id = g.lesson_id
        JOIN workloads w ON w.id = l.workload_id
        JOIN subjects sb ON sb.id = w.subject_id
        WHERE ({PTS}) IS NOT NULL
        GROUP BY g.student_id, w.id"""):
        subj[sid].append((subj_name, cnt, (pts_sum or 0) / cnt))

    # группа «с данными» — если в ней есть хотя бы одна оценка
    group_has_data = defaultdict(bool)
    for sid, s in st.items():
        stu = students.get(sid)
        if stu and s["n"]:
            group_has_data[stu["group_id"]] = True

    weak_students, dead_souls = [], []
    per_group = defaultdict(lambda: {"students": 0, "graded": 0, "pts": 0, "n": 0, "abs": 0,
                                     "unatt": 0, "weak": 0, "dead": 0})

    for sid, stu in students.items():
        gid = stu["group_id"]
        grp = groups.get(gid)
        if not grp:
            continue
        pg = per_group[gid]
        pg["students"] += 1
        s = st.get(sid)

        if not group_has_data[gid]:
            continue                          # группа без данных — отдельный список

        cells = group_cells.get(gid, 0)
        n = s["n"] if s else 0

        # ---------- «мёртвые души»
        reasons, hard = [], False
        if n == 0:
            reasons.append("Нет ни одной оценки при заполненных ведомостях группы")
            hard = True
        else:
            abs_share = s["abs"] / n
            cov = n / cells if cells else 1.0
            if abs_share >= p.dead_abs:
                reasons.append(f"«Н» (не аттестован) по {_pct(abs_share)}% предметов")
                hard = True
            elif abs_share >= p.dead_abs * 0.6:                    # зона наблюдения
                reasons.append(f"Много «Н»: {_pct(abs_share)}% предметов")
            if cov < p.dead_cov:
                reasons.append(f"Итоговые выставлены лишь по {_pct(cov)}% предметов группы")
                hard = True
            elif cov < min(1.0, p.dead_cov * 1.5):                 # зона наблюдения
                reasons.append(f"Мало итоговых оценок: {_pct(cov)}% предметов группы")
            if n >= UNIFORM_MIN_GRADES and s["top"] / n >= UNIFORM_SHARE and s["abs"] != s["top"]:
                reasons.append(f"{_pct(s['top'] / n)}% оценок одинаковые (возможна формальная проставка)")
        if reasons:
            pg["dead"] += 1 if hard else 0
            dead_souls.append({
                "id": sid, "name": stu["name"], "group_id": gid, "group": grp["name"],
                "department": grp["department"],
                "grades": n, "cells": cells,
                "coverage": _pct(n / cells) if cells and n else 0.0,
                "abs_share": _pct(s["abs"] / n) if n else 0.0,
                "last_grade": _fmt_date(last_grade.get(sid)),
                "level": "high" if hard else "watch",
                "reasons": reasons,
            })

        if not n:
            pg["graded"] += 0
            continue

        # ---------- слабые студенты
        # Как в отчёте по группе (reports.py): итог по предмету = среднее, округлённое «от 0,5 вверх»;
        # итог студента = среднее итогов по предметам (предмет с 30 парами не «весит» больше предмета с 5).
        abs_share = s["abs"] / n
        subjects = subj.get(sid, [])
        finals = [round_half_up(a) for _, _, a in subjects]
        avg = round_half_up(mean(finals)) if finals else round_half_up(s["pts"] / n)
        failed = sorted(((nm, round_half_up(a)) for nm, c, a in subjects
                         if c >= MIN_SUBJECT_GRADES and round_half_up(a) < PASS_POINTS),
                        key=lambda x: x[1])

        pg["graded"] += 1
        pg["pts"] += s["pts"]
        pg["n"] += n
        pg["abs"] += s["abs"]
        if avg < PASS_POINTS:
            pg["unatt"] += 1

        flags = []
        if avg < p.weak_avg:
            flags.append(f"Итоговый балл {avg} (порог {p.weak_avg:g})")
        if len(failed) >= p.fail_subjects:
            flags.append(f"Не аттестован по {len(failed)} предм.")
        if abs_share >= p.abs_warn:
            flags.append(f"Пропуски {_pct(abs_share)}%")
        if flags:
            pg["weak"] += 1
            if avg < 40 or len(failed) >= 8:
                level = "critical"
            elif avg < PASS_POINTS or len(failed) >= p.fail_subjects:
                level = "high"
            else:
                level = "watch"
            weak_students.append({
                "id": sid, "name": stu["name"], "group_id": gid, "group": grp["name"],
                "department": grp["department"],
                "grades": n, "avg": avg, "abs_share": _pct(abs_share),
                "subjects": len(subjects), "failed": len(failed),
                "worst": [f"{nm[:50]}{'…' if len(nm) > 50 else ''} ({a:.0f})" for nm, a in failed[:3]],
                "level": level, "flags": flags,
            })

    # ---------- группы
    weak_groups, no_data_groups, all_groups = [], [], []
    for gid, grp in groups.items():
        pg = per_group[gid]
        if pg["students"] == 0:
            continue
        if not group_has_data[gid]:
            has_wl = group_workloads.get(gid, 0) > 0
            no_data_groups.append({**grp, "students": pg["students"], "workloads": group_workloads.get(gid, 0),
                                   "reason": "Ведомости не заполнены" if has_wl
                                   else "Нет учебной нагрузки (не загружен учебный план)"})
            continue
        graded = pg["graded"] or 1
        avg = pg["pts"] / pg["n"] if pg["n"] else 0.0
        unatt_share = pg["unatt"] / graded
        weak_share = pg["weak"] / graded
        flags = []
        if avg < p.group_avg:
            flags.append(f"Средний балл {avg:.1f} (порог {p.group_avg:g})")
        if unatt_share >= p.group_unatt:
            flags.append(f"Неаттестованных {_pct(unatt_share)}% (порог {_pct(p.group_unatt):g}%)")
        if weak_share >= p.group_weak:
            flags.append(f"Слабых студентов {_pct(weak_share)}% (порог {_pct(p.group_weak):g}%)")
        row = {
            **grp, "students": pg["students"], "graded_students": pg["graded"],
            "avg": round(avg, 1),
            "attested": _pct(1 - unatt_share),              # % студентов с итогом ≥ 50 (НЕ равно «успеваемости» из аналитики)
            "unattested": pg["unatt"], "weak_students": pg["weak"], "dead_souls": pg["dead"],
            "abs_share": _pct(pg["abs"] / pg["n"]) if pg["n"] else 0.0,
            "level": "critical" if len(flags) >= 2 else ("weak" if flags else "ok"),
            "flags": flags,
        }
        all_groups.append(row)
        if flags:
            weak_groups.append(row)

    weak_groups.sort(key=lambda r: (-len(r["flags"]), r["avg"]))
    weak_students.sort(key=lambda r: ({"critical": 0, "high": 1, "watch": 2}[r["level"]], r["avg"]))
    dead_souls.sort(key=lambda r: (0 if r["level"] == "high" else 1, r["coverage"], -r["abs_share"]))
    all_groups.sort(key=lambda r: r["avg"])

    # ---------- сводка по отделениям
    dep = defaultdict(lambda: {"groups": 0, "weak_groups": 0, "students": 0, "weak_students": 0, "avgs": []})
    for g in all_groups:
        d = dep[g["department"]]
        d["groups"] += 1
        d["weak_groups"] += 1 if g["flags"] else 0
        d["students"] += g["graded_students"]
        d["weak_students"] += g["weak_students"]
        d["avgs"].append(g["avg"])
    departments = [{"name": n, "groups": d["groups"], "weak_groups": d["weak_groups"],
                    "students": d["students"], "weak_students": d["weak_students"],
                    "avg": round(mean(d["avgs"]), 1) if d["avgs"] else 0.0}
                   for n, d in sorted(dep.items())]

    total_graded = sum(g["graded_students"] for g in all_groups)
    summary = {
        "students_total": len(students),
        "students_analyzed": total_graded,
        "groups_total": len(groups),
        "groups_analyzed": len(all_groups),
        "groups_no_data": len(no_data_groups),
        "students_no_data": sum(g["students"] for g in no_data_groups),
        "weak_groups": len(weak_groups),
        "critical_groups": sum(1 for g in weak_groups if g["level"] == "critical"),
        "weak_students": len(weak_students),
        "critical_students": sum(1 for s in weak_students if s["level"] == "critical"),
        "unattested_students": sum(g["unattested"] for g in all_groups),
        "dead_souls_high": sum(1 for s in dead_souls if s["level"] == "high"),
        "dead_souls_watch": sum(1 for s in dead_souls if s["level"] == "watch"),
        "college_avg": round(mean([g["avg"] for g in all_groups]), 1) if all_groups else 0.0,
    }
    return {
        "params": asdict(p), "summary": summary, "departments": departments,
        "groups": all_groups, "weak_groups": weak_groups, "weak_students": weak_students,
        "dead_souls": dead_souls, "no_data_groups": no_data_groups,
    }


# ==========================================================
# EXCEL
# ==========================================================
LEVEL_RU = {"critical": "Критический", "high": "Высокий", "watch": "Наблюдение",
            "weak": "Слабая", "ok": "Норма"}
_FILL = {"critical": "FEE2E2", "high": "FEF3C7", "watch": "E0F2FE", "weak": "FEF3C7"}
_thin = Side(style="thin", color="CBD5E1")
_BORDER = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
_HEAD_FILL = PatternFill("solid", fgColor="0F172A")


def _sheet(wb, title, headers, rows, widths, level_col=None, note=None):
    ws = wb.create_sheet(title[:31])
    r0 = 1
    if note:
        ws.cell(row=1, column=1, value=note).font = Font(italic=True, color="64748B")
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
        r0 = 3
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=r0, column=c, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _HEAD_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _BORDER
    for i, row in enumerate(rows, r0 + 1):
        for c, v in enumerate(row, 1):
            cell = ws.cell(row=i, column=c, value=v)
            cell.border = _BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        if level_col is not None:
            raw = row[level_col]
            key = next((k for k, v in LEVEL_RU.items() if v == raw), None)
            if key in _FILL:
                ws.cell(row=i, column=level_col + 1).fill = PatternFill("solid", fgColor=_FILL[key])
    for c, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = ws.cell(row=r0 + 1, column=1)
    if rows:
        ws.auto_filter.ref = f"A{r0}:{get_column_letter(len(headers))}{r0 + len(rows)}"
    return ws


def build_xlsx(data: dict, kind: str = "all", generated: str = "") -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    s, prm = data["summary"], data["params"]

    if kind == "all":
        ws = wb.create_sheet("Сводка")
        ws.column_dimensions["A"].width = 52
        ws.column_dimensions["B"].width = 18
        ws["A1"] = "Группа риска: слабые группы, слабые студенты, «мёртвые души»"
        ws["A1"].font = Font(bold=True, size=14)
        ws["A2"] = f"Сформировано: {generated}"
        ws["A2"].font = Font(italic=True, color="64748B")
        lines = [
            ("Студентов в базе", s["students_total"]),
            ("Проанализировано (группы с оценками)", s["students_analyzed"]),
            ("Групп с данными / всего", f'{s["groups_analyzed"]} / {s["groups_total"]}'),
            ("Групп без данных (нет нагрузки или оценок)", s["groups_no_data"]),
            ("Студентов в группах без данных", s["students_no_data"]),
            ("Средний балл по колледжу (по группам)", s["college_avg"]),
            (None, None),
            ("Слабых групп", s["weak_groups"]),
            ("  из них критических (2+ признака)", s["critical_groups"]),
            ("Слабых студентов", s["weak_students"]),
            ("  из них критических", s["critical_students"]),
            ("Неаттестованных студентов (средний < 50)", s["unattested_students"]),
            ("«Мёртвых душ» (явные)", s["dead_souls_high"]),
            ("«Мёртвых душ» (подозрение)", s["dead_souls_watch"]),
            (None, None),
            ("ПОРОГИ РАСЧЁТА", None),
            ("Слабый студент: средний балл ниже", prm["weak_avg"]),
            ("Слабый студент: не аттестован по предметам, от", prm["fail_subjects"]),
            ("Слабый студент: доля «Н» от", f'{_pct(prm["abs_warn"])}%'),
            ("Мёртвая душа: доля «Н» от", f'{_pct(prm["dead_abs"])}%'),
            ("Мёртвая душа: заполнено ячеек журнала меньше", f'{_pct(prm["dead_cov"])}%'),
            ("Слабая группа: средний балл ниже", prm["group_avg"]),
            ("Слабая группа: неаттестованных от", f'{_pct(prm["group_unatt"])}%'),
            ("Слабая группа: слабых студентов от", f'{_pct(prm["group_weak"])}%'),
        ]
        for i, (a, b) in enumerate(lines, 4):
            if a is None:
                continue
            ws.cell(row=i, column=1, value=a)
            ws.cell(row=i, column=2, value=b)
            if b is None:
                ws.cell(row=i, column=1).font = Font(bold=True)

    if kind in ("all", "groups"):
        rows = [[g["name"], g["department"], g["specialty"], g["students"], g["avg"], g["attested"],
                 g["unattested"], g["weak_students"], g["dead_souls"], g["abs_share"],
                 LEVEL_RU[g["level"]], "; ".join(g["flags"])] for g in data["weak_groups"]]
        _sheet(wb, "Слабые группы",
               ["Группа", "Отделение", "Специальность", "Студентов", "Средний балл", "Аттестовано студентов, %",
                "Неаттест.", "Слабых студ.", "Мёртвых душ", "Пропуски «Н», %", "Уровень", "Причины"],
               rows, [16, 30, 12, 11, 12, 14, 11, 12, 12, 14, 14, 70], level_col=10)

    if kind in ("all", "students"):
        rows = [[i, x["name"], x["group"], x["department"], x["grades"], x["avg"], x["abs_share"],
                 x["failed"], x["subjects"], "; ".join(x["worst"]), LEVEL_RU[x["level"]],
                 "; ".join(x["flags"])] for i, x in enumerate(data["weak_students"], 1)]
        _sheet(wb, "Слабые студенты",
               ["№", "ФИО", "Группа", "Отделение", "Оценок", "Средний балл", "Пропуски «Н», %",
                "Не аттест. предметов", "Предметов", "Самые слабые предметы", "Уровень", "Причины"],
               rows, [5, 36, 16, 30, 9, 12, 13, 13, 11, 55, 14, 55], level_col=10)

    if kind in ("all", "dead"):
        rows = [[i, x["name"], x["group"], x["department"], x["grades"], x["cells"], x["coverage"],
                 x["abs_share"], x["last_grade"], "Явная" if x["level"] == "high" else "Подозрение", "; ".join(x["reasons"])]
                for i, x in enumerate(data["dead_souls"], 1)]
        _sheet(wb, "Мёртвые души",
               ["№", "ФИО", "Группа", "Отделение", "Оценок", "Ячеек в журнале группы", "Заполнено, %",
                "Пропуски «Н», %", "Последняя оценка (не «Н»)", "Тип", "Причины"],
               rows, [5, 36, 16, 30, 9, 14, 12, 13, 16, 14, 70],
               note="Студенты, числящиеся в группе, но не имеющие реальной учебной активности в журналах. "
                    "Требуют проверки по приказам и посещаемости.")

    if kind == "all" and data["no_data_groups"]:
        rows = [[g["name"], g["department"], g["specialty"], g["students"], g["reason"]] for g in data["no_data_groups"]]
        _sheet(wb, "Группы без данных", ["Группа", "Отделение", "Специальность", "Студентов", "Причина"],
               rows, [16, 34, 14, 12, 50],
               note="В этих группах нет ни одной оценки — анализ невозможен.")

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
