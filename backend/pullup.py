# backend/pullup.py
"""
Модуль «Можно подтянуть»: студенты, у которых 1–2 итоговые тройки, а по остальным предметам 4 и 5.
Таким студентам достаточно подтянуть один-два предмета, чтобы стать хорошистом.

Работает по схеме risk.py: на вход функция q(sql, params) -> строки. Шкала 100-балльная:
5 = 90–100, 4 = 70–89, 3 = 50–69, ниже 50 или «Н» = неаттестация (такие студенты в список не попадают — это «слабые»).
"""
from collections import defaultdict
from dataclasses import dataclass, asdict
from statistics import mean

from grading import round_half_up
from risk import PTS, _pct, _sheet            # общие: SQL баллов, процент, оформление Excel-листа

from openpyxl import Workbook
from openpyxl.styles import Font
from io import BytesIO


@dataclass
class Params:
    max_threes: int = 2            # допустимо троек (итог 50–69) у студента
    min_subjects: int = 3          # студент учитывается, если итоговые выставлены минимум по стольким предметам
    easy_gap: int = 5              # «лёгкий случай»: до четвёрки (70) не хватает не более стольких баллов по каждой тройке

    @classmethod
    def from_query(cls, **kw) -> "Params":
        d = cls()
        rng = {"max_threes": (1, 6), "min_subjects": (1, 30), "easy_gap": (1, 19)}
        for k, (lo, hi) in rng.items():
            v = kw.get(k)
            if v is None:
                continue
            try:
                setattr(d, k, max(lo, min(hi, int(float(v)))))
            except (TypeError, ValueError):
                pass
        return d


def compute(q, p: Params) -> dict:
    groups = {r[0]: {"name": r[1], "department": r[2]} for r in q("""
        SELECT g.id, g.name, COALESCE(d.name, 'Без отделения') FROM groups g
        LEFT JOIN specialties sp ON sp.id = g.specialty_id LEFT JOIN departments d ON d.id = sp.department_id""")}
    students = {r[0]: {"name": r[1], "gid": r[2]} for r in q("SELECT id, full_name, group_id FROM students")}

    # итог студента по предмету (если в ведомости по какой-то причине несколько колонок — берём среднее, как в отчётах)
    cell = defaultdict(list)
    meta = {}
    for sid, wid, subj, teacher, pts in q(f"""
        SELECT g.student_id, w.id, sb.name, COALESCE(u.full_name, '—'), {PTS}
        FROM grades g JOIN lessons l ON l.id = g.lesson_id JOIN workloads w ON w.id = l.workload_id
        JOIN subjects sb ON sb.id = w.subject_id LEFT JOIN users u ON u.id = w.teacher_id
        WHERE ({PTS}) IS NOT NULL"""):
        cell[(sid, wid)].append(pts)
        meta[wid] = (subj, teacher)

    per_student = defaultdict(list)                  # sid -> [(предмет, преподаватель, итоговый балл)]
    for (sid, wid), lst in cell.items():
        per_student[sid].append((meta[wid][0], meta[wid][1], round_half_up(mean(lst))))

    cands = []
    stat = defaultdict(lambda: {"students": 0, "good": 0, "cands": 0})     # по группам (только студенты с итоговыми)
    for sid, items in per_student.items():
        stu = students.get(sid)
        if not stu or stu["gid"] not in groups or len(items) < p.min_subjects:
            continue
        gst = stat[stu["gid"]]
        gst["students"] += 1
        pts_all = [x[2] for x in items]
        if min(pts_all) < 50:
            continue                                                       # есть неаттестация — это «слабые», не «подтянуть»
        threes = sorted((x for x in items if x[2] < 70), key=lambda x: -x[2])
        if not threes:
            gst["good"] += 1                                               # уже без троек
            continue
        if len(threes) > p.max_threes:
            continue
        gst["cands"] += 1
        gaps = [70 - t[2] for t in threes]
        grp = groups[stu["gid"]]
        cands.append({
            "id": sid, "name": stu["name"], "group": grp["name"], "department": grp["department"],
            "subjects": len(items), "threes": len(threes), "avg": round_half_up(mean(pts_all)),
            "easy": max(gaps) <= p.easy_gap, "total_gap": sum(gaps),
            "targets": [{"subject": t[0], "teacher": t[1], "points": t[2], "gap": 70 - t[2]} for t in threes],
        })
    # самые «лёгкие» сверху: сначала лёгкие случаи, потом меньше баллов не хватает
    cands.sort(key=lambda c: (not c["easy"], c["total_gap"], c["name"]))

    rows = []
    for gid, st in stat.items():
        n = st["students"]
        rows.append({
            "group": groups[gid]["name"], "department": groups[gid]["department"], "students": n,
            "good": st["good"], "candidates": st["cands"],
            "quality_now": _pct(st["good"] / n) if n else 0.0,                         # студенты без троек, %
            "quality_potential": _pct((st["good"] + st["cands"]) / n) if n else 0.0,   # если подтянуть всех кандидатов
        })
    rows.sort(key=lambda r: (-r["candidates"], r["group"]))

    analyzed = sum(r["students"] for r in rows)
    good = sum(r["good"] for r in rows)
    summary = {
        "students_analyzed": analyzed, "candidates": len(cands), "easy": sum(1 for c in cands if c["easy"]),
        "one_three": sum(1 for c in cands if c["threes"] == 1), "two_threes": sum(1 for c in cands if c["threes"] == 2),
        "good_now": good,
        "quality_now": _pct(good / analyzed) if analyzed else 0.0,
        "quality_potential": _pct((good + len(cands)) / analyzed) if analyzed else 0.0,
    }
    return {"params": asdict(p), "summary": summary, "candidates": cands, "groups": rows}


def build_xlsx(data: dict, generated: str = "") -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    s, prm = data["summary"], data["params"]

    ws = wb.create_sheet("Сводка")
    ws.column_dimensions["A"].width = 62
    ws.column_dimensions["B"].width = 16
    ws["A1"] = "Можно подтянуть: студенты с 1–2 тройками, остальные итоговые — 4 и 5"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Сформировано: {generated}"
    lines = [
        ("Студентов проанализировано (итоговые не менее чем по %d предметам)" % prm["min_subjects"], s["students_analyzed"]),
        ("Уже без троек (хорошисты и отличники)", s["good_now"]),
        ("Кандидатов на подтяжку", s["candidates"]),
        ("  из них с одной тройкой", s["one_three"]),
        ("  из них с двумя тройками", s["two_threes"]),
        ("  лёгкие случаи (до «4» не хватает ≤ %d б. по каждому предмету)" % prm["easy_gap"], s["easy"]),
        ("Качество знаний сейчас, % студентов без троек", s["quality_now"]),
        ("Потенциал, если подтянуть всех кандидатов, %", s["quality_potential"]),
    ]
    for i, (a, b) in enumerate(lines, 4):
        ws.cell(row=i, column=1, value=a)
        ws.cell(row=i, column=2, value=b)

    rows = []
    for i, c in enumerate(data["candidates"], 1):
        rows.append([i, c["name"], c["group"], c["department"], c["subjects"], c["avg"], c["threes"],
                     "Лёгкий" if c["easy"] else "Средний",
                     "\n".join(f"{t['subject']} — {t['points']} б. (до «4» не хватает {t['gap']})" for t in c["targets"]),
                     "\n".join(t["teacher"] for t in c["targets"])])
    _sheet(wb, "Можно подтянуть",
           ["№", "ФИО", "Группа", "Отделение", "Предметов", "Средний итог", "Троек", "Сложность",
            "Что подтянуть (предмет — итог)", "Преподаватель(и)"],
           rows, [5, 36, 16, 30, 11, 11, 8, 12, 70, 36],
           note="Итоговая оценка 3 = 50–69 баллов; чтобы получить 4, нужно 70 и выше. Порядок: сначала самые лёгкие случаи.")

    rows = [[g["group"], g["department"], g["students"], g["good"], g["candidates"], g["quality_now"], g["quality_potential"]]
            for g in data["groups"]]
    _sheet(wb, "По группам", ["Группа", "Отделение", "Студентов", "Без троек", "Кандидатов", "Качество знаний, %",
                              "Потенциал, %"], rows, [16, 30, 11, 11, 12, 16, 13],
           note="Качество знаний = доля студентов без итоговых троек. Потенциал — если подтянуть всех кандидатов.")
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
