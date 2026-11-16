# backend/curator.py
"""
Куратор и контроль заполнения: кто из преподавателей не поставил итоговые, выгрузки для куратора и родителей,
сверка контингента со списком колледжа. Работает по схеме risk.py: на вход функция q(sql, params) -> строки.
"""
import re
import zipfile
from collections import defaultdict
from datetime import datetime
from io import BytesIO
from statistics import mean

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from grading import final_grade, points, round_half_up

_thin = Side(style="thin", color="CBD5E1")
_B = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
_HEAD = PatternFill("solid", fgColor="0F172A")
_FILL = {"none": "FEE2E2", "partial": "FEF3C7", "all": "D1FAE5"}
STATUS_RU = {"all": "Заполнено", "partial": "Частично", "none": "Не заполнено"}


def _valid(v):
    return points(v) is not None


def groups_list(q, user_id, is_admin):
    where = "" if is_admin else "WHERE g.curator_id = :u"
    rows = q(f"""
        SELECT g.id, g.name, COALESCE(d.name, ''), COALESCE(cu.full_name, ''), g.contingent,
               (SELECT COUNT(*) FROM students s WHERE s.group_id = g.id), COALESCE(g.graduating, 0)
        FROM groups g LEFT JOIN specialties sp ON sp.id = g.specialty_id LEFT JOIN departments d ON d.id = sp.department_id
        LEFT JOIN users cu ON cu.id = g.curator_id {where} ORDER BY g.name""", {"u": user_id})
    return [{"id": r[0], "name": r[1], "department": r[2], "curator": r[3], "contingent": r[4], "students": r[5],
             "graduating": bool(r[6])} for r in rows]


def completeness(q, gid):
    g = q("""SELECT g.id, g.name, COALESCE(cu.full_name, ''), g.contingent FROM groups g
             LEFT JOIN users cu ON cu.id = g.curator_id WHERE g.id = :g""", {"g": gid})
    if not g:
        return None
    students = q("SELECT id, full_name FROM students WHERE group_id = :g ORDER BY full_name", {"g": gid})
    works = q("""SELECT w.id, sb.name, COALESCE(u.full_name, '—') FROM workloads w JOIN subjects sb ON sb.id = w.subject_id
                 LEFT JOIN users u ON u.id = w.teacher_id WHERE w.group_id = :g ORDER BY sb.name""", {"g": gid})
    vals = defaultdict(list)
    for sid, wid, v in q("""SELECT gr.student_id, w.id, gr.value FROM grades gr JOIN lessons l ON l.id = gr.lesson_id
                            JOIN workloads w ON w.id = l.workload_id WHERE w.group_id = :g""", {"g": gid}):
        if _valid(v):
            vals[(sid, wid)].append(points(v))
    n = len(students)
    subjects, per_student = [], {s[0]: {"id": s[0], "name": s[1], "entered": 0, "missing": [], "results": []} for s in students}
    for wid, subj, teacher in works:
        entered = 0
        for sid, _ in students:
            lst = vals.get((sid, wid))
            if lst:
                entered += 1
                pts = round_half_up(mean(lst))
                per_student[sid]["entered"] += 1
                per_student[sid]["results"].append({"subject": subj, "teacher": teacher, "points": pts,
                                                    "mark": "Н" if all(x == 0 for x in lst) and pts == 0 else final_grade(pts)})
            else:
                per_student[sid]["missing"].append(subj)
                per_student[sid]["results"].append({"subject": subj, "teacher": teacher, "points": None, "mark": ""})
        status = "none" if entered == 0 else ("all" if entered == n and n else "partial")
        subjects.append({"workload_id": wid, "subject": subj, "teacher": teacher, "entered": entered, "total": n, "status": status})
    cells = n * len(works)
    done = sum(s["entered"] for s in subjects)
    bad_teachers = sorted({s["teacher"] for s in subjects if s["status"] != "all"})
    summary = {"students": n, "subjects": len(works), "subjects_complete": sum(1 for s in subjects if s["status"] == "all"),
               "subjects_none": sum(1 for s in subjects if s["status"] == "none"), "teachers_incomplete": len(bad_teachers),
               "cells": cells, "cells_done": done, "percent": round(done * 100 / cells, 1) if cells else 0.0}
    return {"group": {"id": g[0][0], "name": g[0][1], "curator": g[0][2], "contingent": g[0][3]},
            "summary": summary, "subjects": subjects, "students": list(per_student.values()), "teachers_incomplete": bad_teachers}


def completeness_all(q):
    rows = q("""
        SELECT w.id, g.name, sb.name, COALESCE(u.full_name, '—'), COALESCE(cu.full_name, ''),
          (SELECT COUNT(*) FROM students s WHERE s.group_id = w.group_id),
          (SELECT COUNT(DISTINCT gr.student_id) FROM grades gr JOIN lessons l ON l.id = gr.lesson_id
            WHERE l.workload_id = w.id AND gr.value IS NOT NULL AND TRIM(gr.value) <> '')
        FROM workloads w JOIN groups g ON g.id = w.group_id JOIN subjects sb ON sb.id = w.subject_id
        LEFT JOIN users u ON u.id = w.teacher_id LEFT JOIN users cu ON cu.id = g.curator_id ORDER BY u.full_name, g.name, sb.name""")
    items = []
    for wid, grp, subj, teacher, curator, total, entered in rows:
        status = "none" if entered == 0 else ("all" if entered >= total and total else "partial")
        items.append({"group": grp, "subject": subj, "teacher": teacher, "curator": curator, "entered": entered, "total": total, "status": status})
    return items


def reconcile(q):
    rows = q("""
        SELECT g.id, g.name, COALESCE(d.name, ''), COALESCE(cu.full_name, ''), g.contingent,
               (SELECT COUNT(*) FROM students s WHERE s.group_id = g.id)
        FROM groups g LEFT JOIN specialties sp ON sp.id = g.specialty_id LEFT JOIN departments d ON d.id = sp.department_id
        LEFT JOIN users cu ON cu.id = g.curator_id ORDER BY g.name""")
    out = []
    for gid, name, dept, cur, cont, sys_n in rows:
        diff = None if cont is None else sys_n - cont
        if cont is None:
            st = "нет в списке колледжа"
        elif sys_n == 0:
            st = "нет студентов в системе — нужен список"
        elif diff == 0:
            st = "совпадает"
        else:
            st = "в системе " + ("меньше" if diff < 0 else "больше")
        out.append({"id": gid, "name": name, "department": dept, "curator": cur, "contingent": cont, "system": sys_n, "diff": diff, "status": st})
    tc = sum(r["contingent"] or 0 for r in out)
    ts = sum(r["system"] for r in out)
    return {"groups": out, "summary": {"contingent": tc, "system": ts, "diff": ts - tc,
                                       "groups_ok": sum(1 for r in out if r["status"] == "совпадает"),
                                       "groups_bad": sum(1 for r in out if r["status"] not in ("совпадает",))}}


# ------------------------------------------------------------------ Excel
def _sheet(wb, title, headers, rows, widths, note=None, status_col=None, first=False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title[:31]
    r0 = 1
    if note:
        ws.cell(row=1, column=1, value=note).font = Font(italic=True, color="64748B")
        r0 = 3
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=r0, column=c, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _HEAD
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _B
    for i, row in enumerate(rows, r0 + 1):
        for c, v in enumerate(row, 1):
            cell = ws.cell(row=i, column=c, value=v)
            cell.border = _B
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        if status_col is not None:
            key = next((k for k, v in STATUS_RU.items() if v == row[status_col]), None)
            if key:
                ws.cell(row=i, column=status_col + 1).fill = PatternFill("solid", fgColor=_FILL[key])
    for c, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = ws.cell(row=r0 + 1, column=1)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    return ws


def _bytes(wb):
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def completeness_xlsx(d, generated=""):
    g, s = d["group"], d["summary"]
    wb = Workbook()
    rows = [[i, x["subject"], x["teacher"], f'{x["entered"]} из {x["total"]}', STATUS_RU[x["status"]]] for i, x in enumerate(d["subjects"], 1)]
    _sheet(wb, "Заполнение по предметам", ["№", "Предмет", "Преподаватель", "Итоговые выставлены", "Статус"], rows, [5, 70, 30, 20, 16],
           note=f'Группа {g["name"]} · куратор: {g["curator"] or "—"} · заполнено {s["percent"]}% ({s["cells_done"]} из {s["cells"]}) · {generated}',
           status_col=4, first=True)
    bad = [x for x in d["subjects"] if x["status"] != "all"]
    rows = [[x["teacher"], x["subject"], f'{x["entered"]} из {x["total"]}', STATUS_RU[x["status"]]] for x in sorted(bad, key=lambda y: (y["teacher"], y["subject"]))]
    _sheet(wb, "Кто не заполнил", ["Преподаватель", "Предмет", "Итоговые выставлены", "Статус"], rows, [30, 70, 20, 16],
           note="Только предметы, где итоговые выставлены не всем студентам.", status_col=3)
    rows = [[i, x["name"], f'{x["entered"]} из {s["subjects"]}', "; ".join(x["missing"][:8]) + (" …" if len(x["missing"]) > 8 else "")]
            for i, x in enumerate(d["students"], 1) if x["missing"]]
    _sheet(wb, "Студенты без итоговых", ["№", "ФИО", "Итоговых", "Нет итоговых по предметам"], rows, [5, 40, 14, 90])
    return _bytes(wb)


def _safe(name):
    return re.sub(r'[\\/:*?"<>|]+', "", name).strip() or "student"


def parents_zip(d, generated=""):
    """ZIP: по одному Excel-файлу на студента (итоги по предметам, незаполненное отмечено) — куратор отправляет каждому родителю его файл."""
    g = d["group"]
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for st in d["students"]:
            wb = Workbook()
            res = st["results"]
            rows = [[i, r["subject"], r["teacher"], r["points"] if r["points"] is not None else "не выставлено",
                     r["mark"] or "—"] for i, r in enumerate(res, 1)]
            have = [r["points"] for r in res if r["points"] is not None]
            ws = _sheet(wb, "Итоги", ["№", "Предмет", "Преподаватель", "Итоговый балл", "Оценка"], rows, [5, 70, 30, 16, 12],
                        note=f'Итоги: {st["name"]} · группа {g["name"]} · куратор: {g["curator"] or "—"} · {generated}', first=True)
            foot = len(rows) + 5
            ws.cell(row=foot, column=2, value=f'Средний итоговый балл: {round(mean(have), 1) if have else "—"}').font = Font(bold=True)
            ws.cell(row=foot + 1, column=2, value=f'Итоговые выставлены по {len(have)} из {len(res)} предметов').font = Font(italic=True, color="64748B")
            for r in range(5, 5 + len(rows)):
                if ws.cell(row=r, column=4).value == "не выставлено":
                    for c in range(1, 6):
                        ws.cell(row=r, column=c).fill = PatternFill("solid", fgColor="FEF3C7")
            z.writestr(f"{_safe(st['name'])}.xlsx", _bytes(wb))
        z.writestr("ПРОЧТИТЕ.txt", ("Один файл = один студент. Отправляйте каждому родителю только его файл (персональные данные).\n"
                                    "Жёлтым отмечены предметы, по которым преподаватель ещё не выставил итоговую оценку.\n").encode("utf-8"))
    return buf.getvalue()


def all_completeness_xlsx(items, generated=""):
    wb = Workbook()
    by_t = defaultdict(lambda: {"w": 0, "bad": 0})
    for x in items:
        by_t[x["teacher"]]["w"] += 1
        by_t[x["teacher"]]["bad"] += 0 if x["status"] == "all" else 1
    rows = [[t, v["w"], v["w"] - v["bad"], v["bad"]] for t, v in sorted(by_t.items(), key=lambda kv: (-kv[1]["bad"], kv[0]))]
    _sheet(wb, "По преподавателям", ["Преподаватель", "Ведомостей", "Заполнено полностью", "Не заполнено / частично"], rows, [34, 14, 20, 24],
           note=f"Сформировано: {generated}. Сверху — у кого больше всего незаполненных ведомостей.", first=True)
    rows = [[x["teacher"], x["group"], x["curator"], x["subject"], f'{x["entered"]} из {x["total"]}', STATUS_RU[x["status"]]]
            for x in items if x["status"] != "all"]
    _sheet(wb, "Что не заполнено", ["Преподаватель", "Группа", "Куратор группы", "Предмет", "Итоговые выставлены", "Статус"], rows,
           [30, 16, 30, 70, 20, 16], status_col=5)
    return _bytes(wb)


def reconcile_xlsx(d, generated=""):
    wb = Workbook()
    s = d["summary"]
    rows = [[r["name"], r["department"], r["curator"], r["contingent"] if r["contingent"] is not None else "—", r["system"],
             r["diff"] if r["diff"] is not None else "—", r["status"]] for r in d["groups"]]
    _sheet(wb, "Сверка контингента", ["Группа", "Отделение", "Куратор", "По списку колледжа", "В системе", "Разница", "Статус"], rows,
           [18, 32, 32, 16, 12, 10, 40],
           note=f'Контингент по списку: {s["contingent"]} · в системе: {s["system"]} · разница {s["diff"]:+d} · {generated}', first=True)
    return _bytes(wb)


# ------------------------------------------------------------------ сводная таблица куратора
def _short(name, limit=48):
    name = re.sub(r"\s+", " ", name or "").strip()
    return name if len(name) <= limit else name[:limit - 1].rstrip() + "…"


def summary(d):
    """Сводная по группе: студенты × предметы (итоговый балл), средний балл и число 5/4/3/неатт у каждого,
    средний балл и успеваемость по каждому предмету. На вход — результат completeness() (с results)."""
    cols = [{"workload_id": s["workload_id"], "subject": s["subject"], "short": _short(s["subject"]), "teacher": s["teacher"],
             "status": s["status"], "pts": []} for s in d["subjects"]]
    students = []
    for st in d["students"]:
        cells, have = [], []
        cnt = {"5": 0, "4": 0, "3": 0, "fail": 0}
        for i, r in enumerate(st["results"]):
            p, m = r["points"], r["mark"]
            cells.append({"p": p, "m": m})
            if p is None:
                continue
            have.append(p)
            cols[i]["pts"].append(p)
            cnt["fail" if m in ("Н", "неатт.") else m] += 1
        avg = round(mean(have), 1) if have else None
        students.append({"id": st["id"], "name": st["name"], "cells": cells, "avg": avg,
                         "mark": final_grade(avg) if avg is not None else "", "c5": cnt["5"], "c4": cnt["4"],
                         "c3": cnt["3"], "fail": cnt["fail"], "missing": len(cells) - len(have)})
    for c in cols:
        pts = c.pop("pts")
        c["avg"] = round(mean(pts), 1) if pts else None
        c["filled"] = len(pts)
        c["uspev"] = round(sum(1 for p in pts if p >= 50) * 100 / len(pts), 1) if pts else None
    graded = [s for s in students if s["avg"] is not None]
    totals = {
        "students": len(students), "subjects": len(cols),
        "avg": round(mean(s["avg"] for s in graded), 1) if graded else None,
        "excellent": sum(1 for s in graded if s["c5"] and not (s["c4"] or s["c3"] or s["fail"])),
        "good": sum(1 for s in graded if s["c4"] and not (s["c3"] or s["fail"])),
        "with3": sum(1 for s in graded if s["c3"] and not s["fail"]),
        "failing": sum(1 for s in graded if s["fail"]),
        "no_grades": len(students) - len(graded),
        "percent": d["summary"]["percent"],
    }
    return {"group": d["group"], "subjects": cols, "students": students, "totals": totals}


_LVL = {"5": "D1FAE5", "4": "DBEAFE", "3": "FEF3C7", "неатт.": "FEE2E2", "Н": "E2E8F0"}


def summary_xlsx(sm, generated=""):
    g, t, cols = sm["group"], sm["totals"], sm["subjects"]
    n = len(cols)
    wb = Workbook()
    ws = wb.active
    ws.title = "Сводная"
    ws.cell(row=1, column=1, value=f'Сводная ведомость группы {g["name"]}').font = Font(bold=True, size=14)
    avg = t["avg"] if t["avg"] is not None else "—"
    ws.cell(row=2, column=1, value=(f'Куратор: {g["curator"] or "—"} · студентов: {t["students"]} · предметов: {t["subjects"]} · '
                                    f'заполнено {t["percent"]}% · средний балл группы: {avg} · {generated}')
            ).font = Font(italic=True, color="64748B")
    hdr = 4
    tail = ["Средний балл", "Оценка", "«5»", "«4»", "«3»", "Неатт.", "Не выставлено"]
    heads = ["№", "ФИО студента"] + [f'{i}. {c["short"]}' for i, c in enumerate(cols, 1)] + tail
    for ci, h in enumerate(heads, 1):
        vertical = 2 < ci <= 2 + n
        cell = ws.cell(row=hdr, column=ci, value=h)
        cell.font = Font(bold=True, color="FFFFFF", size=9 if vertical else 11)
        cell.fill = _HEAD
        cell.border = _B
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=not vertical,
                                   text_rotation=90 if vertical else 0)
    ws.row_dimensions[hdr].height = 190
    for ri, st in enumerate(sm["students"], 1):
        r = hdr + ri
        marks = [("Н" if c["m"] == "Н" else c["p"]) if c["p"] is not None else "" for c in st["cells"]]
        vals = [ri, st["name"]] + marks + [st["avg"] if st["avg"] is not None else "", st["mark"],
                                           st["c5"], st["c4"], st["c3"], st["fail"], st["missing"]]
        for ci, v in enumerate(vals, 1):
            cell = ws.cell(row=r, column=ci, value=v)
            cell.border = _B
            cell.alignment = Alignment(horizontal="left" if ci == 2 else "center", vertical="center")
        for i, c in enumerate(st["cells"]):
            if c["m"] in _LVL:
                ws.cell(row=r, column=3 + i).fill = PatternFill("solid", fgColor=_LVL[c["m"]])
        if st["mark"] in _LVL:
            ws.cell(row=r, column=3 + n + 1).fill = PatternFill("solid", fgColor=_LVL[st["mark"]])
    foot = hdr + len(sm["students"]) + 1
    for label, key in (("Средний балл по предмету", "avg"), ("Успеваемость, %", "uspev"), ("Выставлено итоговых", "filled")):
        ws.cell(row=foot, column=2, value=label).font = Font(bold=True)
        for i, c in enumerate(cols):
            cell = ws.cell(row=foot, column=3 + i, value=c[key] if c[key] is not None else "—")
            cell.alignment = Alignment(horizontal="center")
            cell.font = Font(bold=True)
            cell.border = _B
        foot += 1
    ws.column_dimensions["A"].width = 5
    ws.column_dimensions["B"].width = 36
    for i in range(n):
        ws.column_dimensions[get_column_letter(3 + i)].width = 6
    for i, w in enumerate([10, 9, 6, 6, 6, 8, 11]):
        ws.column_dimensions[get_column_letter(3 + n + i)].width = w
    ws.freeze_panes = ws.cell(row=hdr + 1, column=3)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    rows = [[i, c["subject"], c["teacher"], c["avg"] if c["avg"] is not None else "—",
             c["uspev"] if c["uspev"] is not None else "—", f'{c["filled"]} из {t["students"]}'] for i, c in enumerate(cols, 1)]
    _sheet(wb, "Предметы", ["№", "Предмет (полное название)", "Преподаватель", "Средний балл", "Успеваемость, %", "Выставлено"],
           rows, [5, 80, 30, 14, 16, 14], note="Номера совпадают с номерами столбцов на листе «Сводная».")
    return _bytes(wb)
