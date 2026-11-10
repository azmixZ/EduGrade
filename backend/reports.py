# backend/reports.py
"""
Выгрузки в Excel: отчёт по группе, ведомость преподавателя, сравнение групп.

Функции принимают `q(sql, params) -> список строк` (в main.py это обёртка над сессией SQLAlchemy,
в тестах — обычный sqlite3), поэтому модуль не зависит от ORM и легко проверяется отдельно.
"""
import io
import re
from collections import defaultdict
from datetime import datetime

from openpyxl import Workbook
from openpyxl.chart import BarChart, DoughnutChart, RadarChart, Reference, Series
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.series import DataPoint
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from grading import GRADE_ORDER, bucket, calc_metrics, final_grade, points, round_half_up


class NotFound(Exception):
    pass


# ----------------------------------------------------------------------
# Оформление
# ----------------------------------------------------------------------
NAVY = "0F172A"
HEAD_FILL = PatternFill("solid", fgColor=NAVY)
EXAM_FILL = PatternFill("solid", fgColor="1D4ED8")
TOTAL_FILL = PatternFill("solid", fgColor="E2E8F0")
SOFT_FILL = PatternFill("solid", fgColor="F1F5F9")
LEVEL_FILL = {
    "5": PatternFill("solid", fgColor="D1FAE5"),
    "4": PatternFill("solid", fgColor="DBEAFE"),
    "3": PatternFill("solid", fgColor="FEF3C7"),
    "<50": PatternFill("solid", fgColor="FEE2E2"),
    "Н": PatternFill("solid", fgColor="E2E8F0"),
}
THIN = Side(style="thin", color="CBD5E1")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)

SCALE_NOTE = "Шкала: 90–100 = «5», 70–89 = «4», 50–69 = «3», меньше 50 = неаттестация, «Н» = 0 баллов."


def now_str():
    return datetime.now().strftime("%d.%m.%Y %H:%M")


def safe_title(name, used):
    t = re.sub(r"[\[\]:*?/\\]", " ", str(name)).strip()[:31] or "Лист"
    base, n = t, 2
    while t.lower() in used:
        suffix = f" ({n})"
        t = base[:31 - len(suffix)] + suffix
        n += 1
    used.add(t.lower())
    return t


def title_block(ws, lines):
    for i, text in enumerate(lines, 1):
        c = ws.cell(row=i, column=1, value=text)
        c.font = Font(bold=True, size=14, color=NAVY) if i == 1 else Font(size=10, color="475569")
        c.alignment = Alignment(horizontal="left", vertical="center")


def header_row(ws, row, headers, exam_cols=()):
    for col, text in enumerate(headers, 1):
        c = ws.cell(row=row, column=col, value=text)
        c.font = Font(bold=True, color="FFFFFF", size=10)
        c.fill = EXAM_FILL if col in exam_cols else HEAD_FILL
        c.alignment = CENTER
        c.border = BORDER
    ws.row_dimensions[row].height = 44


def set_widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def put(ws, row, col, value, fmt=None, align=CENTER, fill=None, bold=False):
    c = ws.cell(row=row, column=col, value=value)
    c.border = BORDER
    c.alignment = align
    if fmt:
        c.number_format = fmt
    if fill:
        c.fill = fill
    if bold:
        c.font = Font(bold=True)
    return c


def level_of(p):
    return "<50" if p < 50 else "3" if p < 70 else "4" if p < 90 else "5"


def pct(part, total):
    return round(part * 100 / total, 1) if total else 0


def to_bytes(wb):
    for ws in wb.worksheets:                 # удобная печать: альбомная ориентация, все столбцы на одной странице
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_options.horizontalCentered = True
    for i, ws in enumerate(wb.worksheets):
        ws.sheet_view.tabSelected = (i == 0)
    wb.active = 0
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def explanations_sheet(wb, used):
    ws = wb.create_sheet(safe_title("Пояснения", used))
    lines = [
        ("Как читать отчёт", True),
        (SCALE_NOTE, False),
        ("«Н» (пропуск) считается как 0 баллов и входит в средний балл и в успеваемость.", False),
        ("Успеваемость группы / отделения = доля выставленных оценок от 50 баллов (все оценки, включая «Н»).", False),
        ("Успеваемость по предмету в отчёте по группе = доля студентов, чей средний балл по предмету не ниже 50.", False),
        ("Итог студента по предмету = среднее баллов по колонкам ведомости, округлённое до целого (0,5 — вверх).", False),
        ("Пустые ячейки (оценка ещё не выставлена) в расчётах не участвуют.", False),
        (f"Источник: EduGrade. Сформировано {now_str()}.", False),
    ]
    for i, (text, bold) in enumerate(lines, 1):
        c = ws.cell(row=i, column=1, value=text)
        c.font = Font(bold=bold, size=13 if bold else 11)
        c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 110


# ----------------------------------------------------------------------
# Ведомость (один предмет одной группы) — используется и в отчёте по группе, и в выгрузке преподавателя
# ----------------------------------------------------------------------
def write_journal_sheet(ws, title_lines, students, lessons, grades, layout=None):
    """
    students: [(id, ФИО)], lessons: [(id, подпись, тип)], grades: {(student_id, lesson_id): значение}.
    Возвращает {student_id: (баллы|None, оценка|'—', число Н)}.
    """
    title_block(ws, title_lines)
    hdr = len(title_lines) + 2          # одна пустая строка между шапкой и таблицей
    exam_cols = [3 + i for i, l in enumerate(lessons) if l[2] == "exam"]
    headers = ["№", "ФИО студента"] + [l[1] for l in lessons] + ["Итог, баллы", "Оценка", "Кол-во «Н»"]
    header_row(ws, hdr, headers, exam_cols)

    results = {}
    col_pts = defaultdict(list)
    for i, (sid, name) in enumerate(students, 1):
        r = hdr + i
        put(ws, r, 1, i, align=CENTER)
        put(ws, r, 2, name, align=LEFT)
        pts, n_abs = [], 0
        for j, (lid, _, _) in enumerate(lessons):
            raw = grades.get((sid, lid))
            c = put(ws, r, 3 + j, None)
            p = points(raw) if raw not in (None, "") else None
            if p is None:
                continue
            if (raw or "").strip().upper() == "Н":
                c.value = "Н"
                n_abs += 1
                c.fill = LEVEL_FILL["Н"]
            else:
                c.value = p
                c.fill = LEVEL_FILL[level_of(p)]
            pts.append(p)
            col_pts[j].append(p)
        base = 3 + len(lessons)
        if pts:
            avg = round_half_up(sum(pts) / len(pts))
            g = final_grade(avg)
            put(ws, r, base, avg, bold=True, fill=LEVEL_FILL[level_of(avg)])
            put(ws, r, base + 1, g, bold=True, fill=LEVEL_FILL[level_of(avg)])
            results[sid] = (avg, g, n_abs)
        else:
            put(ws, r, base, "—")
            put(ws, r, base + 1, "—")
            results[sid] = (None, "—", n_abs)
        put(ws, r, base + 2, n_abs)

    foot = hdr + len(students) + 1
    put(ws, foot, 2, "Средний балл по колонке", align=LEFT, fill=TOTAL_FILL, bold=True)
    put(ws, foot, 1, None, fill=TOTAL_FILL)
    for j in range(len(lessons)):
        vals = col_pts.get(j, [])
        put(ws, foot, 3 + j, round(sum(vals) / len(vals), 1) if vals else "—", fmt="0.0", fill=TOTAL_FILL, bold=True)
    for k in range(3):
        put(ws, foot, 3 + len(lessons) + k, None, fill=TOTAL_FILL)

    set_widths(ws, [5, 38] + [11] * len(lessons) + [13, 10, 12])
    ws.freeze_panes = ws.cell(row=hdr + 1, column=3)
    if layout is not None:
        layout.update(hdr=hdr, foot=foot, base=3 + len(lessons), n_lessons=len(lessons), n_students=len(students))
    return results


# ----------------------------------------------------------------------
# Загрузка данных
# ----------------------------------------------------------------------
def group_info(q, gid):
    rows = q("SELECT g.id, g.name, COALESCE(d.name, ''), COALESCE(sp.name, '') FROM groups g "
             "LEFT JOIN specialties sp ON sp.id = g.specialty_id "
             "LEFT JOIN departments d ON d.id = sp.department_id WHERE g.id = :gid", {"gid": gid})
    if not rows:
        raise NotFound("Группа не найдена")
    return rows[0]


def load_students(q, gid):
    return [(r[0], r[1]) for r in q("SELECT id, full_name FROM students WHERE group_id = :gid ORDER BY full_name",
                                    {"gid": gid})]


def lesson_tuple(row):
    return (row[0], row[2] or "", row[3] or "pair")


# ----------------------------------------------------------------------
# 1. ВЕДОМОСТЬ ПРЕПОДАВАТЕЛЯ (один предмет)
# ----------------------------------------------------------------------
def journal_report(q, workload_id):
    rows = q("SELECT w.id, w.group_id, s.name, COALESCE(u.full_name, '') FROM workloads w "
             "JOIN subjects s ON s.id = w.subject_id LEFT JOIN users u ON u.id = w.teacher_id WHERE w.id = :wid",
             {"wid": workload_id})
    if not rows:
        raise NotFound("Ведомость не найдена")
    _, gid, subject, teacher = rows[0]
    _, gname, dept, _spec = group_info(q, gid)
    students = load_students(q, gid)
    lessons = [lesson_tuple(r) for r in q(
        "SELECT id, workload_id, date_str, COALESCE(lesson_type, 'pair') FROM lessons "
        "WHERE workload_id = :wid ORDER BY id", {"wid": workload_id})]
    grades = {(r[0], r[1]): r[2] for r in q(
        "SELECT gr.student_id, gr.lesson_id, gr.value FROM grades gr JOIN lessons l ON l.id = gr.lesson_id "
        "WHERE l.workload_id = :wid", {"wid": workload_id})}

    wb = Workbook()
    used = set()
    ws = wb.active
    ws.title = safe_title("Ведомость", used)
    layout = {}
    write_journal_sheet(ws, [f"Ведомость: {subject}",
                             f"Группа: {gname} · Преподаватель: {teacher or '—'}",
                             f"Сформирована: {now_str()}",
                             SCALE_NOTE], students, lessons, grades, layout)
    dist = {k: 0 for k in GRADE_ORDER}
    for val in grades.values():
        b = bucket(val)
        if b:
            dist[b] += 1
    build_journal_charts(wb, ws, layout, dist, subject, gname)
    return to_bytes(wb), gname, subject


# ----------------------------------------------------------------------
# 2. ОТЧЁТ ПО ГРУППЕ
# ----------------------------------------------------------------------
def group_report(q, gid):
    _, gname, dept, spec = group_info(q, gid)
    students = load_students(q, gid)
    workloads = [(r[0], r[1], r[2]) for r in q(
        "SELECT w.id, s.name, COALESCE(u.full_name, '') FROM workloads w "
        "JOIN subjects s ON s.id = w.subject_id LEFT JOIN users u ON u.id = w.teacher_id "
        "WHERE w.group_id = :gid ORDER BY s.name, w.id", {"gid": gid})]
    lessons_by_wl = defaultdict(list)
    for r in q("SELECT l.id, l.workload_id, l.date_str, COALESCE(l.lesson_type, 'pair') FROM lessons l "
               "JOIN workloads w ON w.id = l.workload_id WHERE w.group_id = :gid ORDER BY l.id", {"gid": gid}):
        lessons_by_wl[r[1]].append(lesson_tuple(r))
    grades = {(r[0], r[1]): r[2] for r in q(
        "SELECT gr.student_id, gr.lesson_id, gr.value FROM grades gr JOIN lessons l ON l.id = gr.lesson_id "
        "JOIN workloads w ON w.id = l.workload_id WHERE w.group_id = :gid", {"gid": gid})}

    wb = Workbook()
    used = set()
    ws_sum = wb.active
    ws_sum.title = safe_title("Сводка", used)
    ws_sub = wb.create_sheet(safe_title("Предметы", used))

    # --- ведомости по предметам (и накопление статистики) ---
    active = [w for w in workloads if lessons_by_wl.get(w[0])]
    per_wl_results = {}
    wl_dist = {}
    for idx, (wid, subject, teacher) in enumerate(active, 1):
        ws = wb.create_sheet(safe_title(f"{idx}. {subject}", used))
        per_wl_results[wid] = write_journal_sheet(
            ws, [f"Ведомость: {subject}", f"Группа: {gname} · Преподаватель: {teacher or '—'}",
                 f"Сформирована: {now_str()}", SCALE_NOTE],
            students, lessons_by_wl[wid], grades)

    # статистика по предметам (по всем оценкам)
    lesson_wl = {l[0]: wid for wid, ls in lessons_by_wl.items() for l in ls}
    for wid, _, _ in workloads:
        wl_dist[wid] = ({k: 0 for k in GRADE_ORDER}, 0)
    for (sid, lid), val in grades.items():
        wid = lesson_wl.get(lid)
        b = bucket(val)
        if wid is None or not b:
            continue
        dist, psum = wl_dist[wid]
        dist[b] += 1
        wl_dist[wid] = (dist, psum + (points(val) or 0))

    total_dist = {k: 0 for k in GRADE_ORDER}
    total_pts = 0
    for dist, psum in wl_dist.values():
        for k in GRADE_ORDER:
            total_dist[k] += dist[k]
        total_pts += psum
    gm = calc_metrics(total_dist, total_pts)

    # --- Сводка ---
    n_sub = len(active)
    title_block(ws_sum, [
        f"Отчёт по группе {gname}",
        f"Отделение: {dept or '—'} · Специальность: {spec or '—'}",
        f"Сформирован: {now_str()} · Студентов: {len(students)} · Предметов с оценками: {n_sub}",
        f"Средний балл группы: {gm['avg']} · Успеваемость: {gm['uspev']}% (доля оценок от 50 баллов) · "
        f"Выставлено оценок: {sum(total_dist.values())}",
    ])
    hdr = 6
    headers = ["№", "ФИО студента"] + [f"{s}\n{t}" if t else s for _, s, t in active] + \
              ["Средний балл", "Итог", "Кол-во «Н»", "Предметов с неатт."]
    header_row(ws_sum, hdr, headers)
    ws_sum.row_dimensions[hdr].height = 125

    subj_avgs = defaultdict(list)
    overalls = []                        # (ФИО, итог, число «Н», предметов с неатт.)
    for i, (sid, name) in enumerate(students, 1):
        r = hdr + i
        put(ws_sum, r, 1, i)
        put(ws_sum, r, 2, name, align=LEFT)
        finals, total_abs, fails = [], 0, 0
        for j, (wid, _, _) in enumerate(active):
            avg, g, n_abs = per_wl_results[wid].get(sid, (None, "—", 0))
            total_abs += n_abs
            if avg is None:
                put(ws_sum, r, 3 + j, "—")
                continue
            put(ws_sum, r, 3 + j, avg, fill=LEVEL_FILL[level_of(avg)])
            finals.append(avg)
            subj_avgs[j].append(avg)
            fails += avg < 50
        base = 3 + len(active)
        if finals:
            overall = round_half_up(sum(finals) / len(finals))
            put(ws_sum, r, base, overall, bold=True, fill=LEVEL_FILL[level_of(overall)])
            put(ws_sum, r, base + 1, final_grade(overall), bold=True, fill=LEVEL_FILL[level_of(overall)])
            overalls.append((name, overall, total_abs, fails))
        else:
            put(ws_sum, r, base, "—")
            put(ws_sum, r, base + 1, "—")
        put(ws_sum, r, base + 2, total_abs)
        put(ws_sum, r, base + 3, fails, fill=LEVEL_FILL["<50"] if fails else None)

    r1 = hdr + len(students) + 1
    r2 = r1 + 1
    put(ws_sum, r1, 2, "Средний балл по предмету", align=LEFT, fill=TOTAL_FILL, bold=True)
    put(ws_sum, r2, 2, "Успеваемость по предмету, %", align=LEFT, fill=TOTAL_FILL, bold=True)
    for rr in (r1, r2):
        put(ws_sum, rr, 1, None, fill=TOTAL_FILL)
    for j in range(len(active)):
        vals = subj_avgs.get(j, [])
        put(ws_sum, r1, 3 + j, round(sum(vals) / len(vals), 1) if vals else "—", fmt="0.0", fill=TOTAL_FILL, bold=True)
        put(ws_sum, r2, 3 + j, pct(sum(v >= 50 for v in vals), len(vals)) if vals else "—", fmt="0.0",
            fill=TOTAL_FILL, bold=True)
    for k in range(4):
        for rr in (r1, r2):
            put(ws_sum, rr, 3 + len(active) + k, None, fill=TOTAL_FILL)
    set_widths(ws_sum, [5, 38] + [19] * len(active) + [13, 9, 11, 14])
    ws_sum.freeze_panes = ws_sum.cell(row=hdr + 1, column=3)

    # --- Предметы ---
    title_block(ws_sub, [f"Предметы группы {gname}", f"Сформирован: {now_str()}", SCALE_NOTE])
    headers = ["Предмет", "Преподаватель", "Колонок", "Оценок", "Средний балл", "Успеваемость, %",
               "«5»", "«4»", "«3»", "Неатт. (<50)", "«Н»"]
    header_row(ws_sub, 5, headers)
    sub_rows = sorted(workloads, key=lambda w: (sum(wl_dist[w[0]][0].values()) == 0, w[1], w[0]))   # предметы с оценками — первыми
    n_graded = sum(1 for w in workloads if sum(wl_dist[w[0]][0].values()) > 0)
    for i, (wid, subject, teacher) in enumerate(sub_rows, 1):
        dist, psum = wl_dist[wid]
        n = sum(dist.values())
        m = calc_metrics(dist, psum)
        r = 5 + i
        put(ws_sub, r, 1, subject, align=LEFT)
        put(ws_sub, r, 2, teacher or "—", align=LEFT)
        put(ws_sub, r, 3, len(lessons_by_wl.get(wid, [])))
        put(ws_sub, r, 4, n)
        put(ws_sub, r, 5, m["avg"] if n else "—", fmt="0.0")
        put(ws_sub, r, 6, m["uspev"] if n else "—", fmt="0.0")
        for k, key in enumerate(["5", "4", "3", "<50", "Н"]):
            put(ws_sub, r, 7 + k, dist[key])
    set_widths(ws_sub, [46, 26, 9, 9, 13, 15, 8, 8, 8, 13, 8])
    ws_sub.freeze_panes = "A6"
    if workloads:
        ws_sub.auto_filter.ref = f"A5:K{5 + len(workloads)}"

    build_group_dashboard(wb, gname, dept, spec, len(students), total_dist, gm, ws_sub, n_graded, ws_sum, hdr,
                          3 + len(active), overalls)
    explanations_sheet(wb, used)
    return to_bytes(wb), gname


# ----------------------------------------------------------------------
# 3. СРАВНЕНИЕ ГРУПП
# ----------------------------------------------------------------------
def compare_report(q, group_ids=None):
    groups = [(r[0], r[1], r[2]) for r in q(
        "SELECT g.id, g.name, COALESCE(d.name, 'Без отделения') FROM groups g "
        "LEFT JOIN specialties sp ON sp.id = g.specialty_id "
        "LEFT JOIN departments d ON d.id = sp.department_id ORDER BY g.name")]
    if group_ids:
        wanted = set(group_ids)
        groups = [g for g in groups if g[0] in wanted]
    if not groups:
        raise NotFound("Группы не найдены")
    gids = {g[0] for g in groups}

    n_students = dict(q("SELECT group_id, COUNT(*) FROM students GROUP BY group_id"))
    wl_meta = {r[0]: (r[1], r[2], r[3]) for r in q(
        "SELECT w.id, w.group_id, s.name, COALESCE(u.full_name, '') FROM workloads w "
        "JOIN subjects s ON s.id = w.subject_id LEFT JOIN users u ON u.id = w.teacher_id")}
    g_dist = defaultdict(lambda: {k: 0 for k in GRADE_ORDER})
    g_pts = defaultdict(int)
    w_dist = defaultdict(lambda: {k: 0 for k in GRADE_ORDER})
    w_pts = defaultdict(int)
    for gid, wid, value, cnt in q(
            "SELECT w.group_id, w.id, gr.value, COUNT(*) FROM grades gr JOIN lessons l ON l.id = gr.lesson_id "
            "JOIN workloads w ON w.id = l.workload_id GROUP BY w.group_id, w.id, gr.value"):
        b = bucket(value)
        if gid not in gids or not b:
            continue
        g_dist[gid][b] += cnt
        g_pts[gid] += (points(value) or 0) * cnt
        w_dist[wid][b] += cnt
        w_pts[wid] += (points(value) or 0) * cnt

    all_dist = {k: sum(g_dist[g][k] for g in gids) for k in GRADE_ORDER}
    all_pts = sum(g_pts[g] for g in gids)
    overall = calc_metrics(all_dist, all_pts)

    rows = []
    for gid, name, dept in groups:
        d = g_dist[gid]
        n = sum(d.values())
        m = calc_metrics(d, g_pts[gid])
        rows.append({"gid": gid, "name": name, "dept": dept, "students": n_students.get(gid, 0), "n": n,
                     "avg": m["avg"], "uspev": m["uspev"], "d": d})
    rows.sort(key=lambda r: (r["n"] == 0, -r["avg"], r["name"]))

    wb = Workbook()
    used = set()
    ws = wb.active
    ws.title = safe_title("Сравнение групп", used)
    title_block(ws, [
        "Сравнение групп по успеваемости",
        f"Групп в отчёте: {len(groups)} · Сформирован: {now_str()}",
        f"В среднем по выбранным группам: средний балл {overall['avg']}, успеваемость {overall['uspev']}%, "
        f"оценок выставлено {sum(all_dist.values())}",
        SCALE_NOTE,
    ])
    hdr = 6
    header_row(ws, hdr, ["Место", "Группа", "Отделение", "Студентов", "Оценок", "Средний балл", "Успеваемость, %",
                         "«5», %", "«4», %", "«3», %", "Неатт., %", "«Н», %", "Откл. от среднего, баллы"])
    for i, r in enumerate(rows, 1):
        rr = hdr + i
        n, d = r["n"], r["d"]
        put(ws, rr, 1, i if n else "—")
        put(ws, rr, 2, r["name"], align=LEFT, bold=True)
        put(ws, rr, 3, r["dept"], align=LEFT)
        put(ws, rr, 4, r["students"])
        put(ws, rr, 5, n)
        put(ws, rr, 6, r["avg"] if n else "—", fmt="0.0", bold=True)
        put(ws, rr, 7, r["uspev"] if n else "—", fmt="0.0", bold=True)
        for k, key in enumerate(["5", "4", "3", "<50", "Н"]):
            put(ws, rr, 8 + k, pct(d[key], n) if n else "—", fmt="0.0")
        put(ws, rr, 13, round(r["avg"] - overall["avg"], 1) if n else "—", fmt="+0.0;-0.0;0.0")
        ws.row_dimensions[rr].height = 30
    last = hdr + len(rows)
    tot = last + 1
    put(ws, tot, 1, None, fill=TOTAL_FILL)
    put(ws, tot, 2, "Всего / в среднем", align=LEFT, fill=TOTAL_FILL, bold=True)
    put(ws, tot, 3, None, fill=TOTAL_FILL)
    put(ws, tot, 4, sum(r["students"] for r in rows), fill=TOTAL_FILL, bold=True)
    tn = sum(all_dist.values())
    put(ws, tot, 5, tn, fill=TOTAL_FILL, bold=True)
    put(ws, tot, 6, overall["avg"], fmt="0.0", fill=TOTAL_FILL, bold=True)
    put(ws, tot, 7, overall["uspev"], fmt="0.0", fill=TOTAL_FILL, bold=True)
    for k, key in enumerate(["5", "4", "3", "<50", "Н"]):
        put(ws, tot, 8 + k, pct(all_dist[key], tn), fmt="0.0", fill=TOTAL_FILL, bold=True)
    put(ws, tot, 13, None, fill=TOTAL_FILL)
    set_widths(ws, [8, 18, 30, 12, 10, 13, 15, 9, 9, 9, 11, 9, 16])
    ws.freeze_panes = ws.cell(row=hdr + 1, column=3)
    ws.auto_filter.ref = f"A{hdr}:M{last}"
    if len(rows) > 1:
        for col in ("F", "G"):
            ws.conditional_formatting.add(
                f"{col}{hdr + 1}:{col}{last}",
                ColorScaleRule(start_type="min", start_color="FCA5A5", mid_type="percentile", mid_value=50,
                               mid_color="FDE68A", end_type="max", end_color="86EFAC"))

    # --- Отделения ---
    ws_d = wb.create_sheet(safe_title("Отделения", used))
    title_block(ws_d, ["Сравнение отделений", f"Сформирован: {now_str()}", SCALE_NOTE])
    header_row(ws_d, 5, ["Отделение", "Групп", "Студентов", "Оценок", "Средний балл", "Успеваемость, %",
                         "«5», %", "«4», %", "«3», %", "Неатт., %", "«Н», %"])
    depts = defaultdict(lambda: {"groups": 0, "students": 0, "d": {k: 0 for k in GRADE_ORDER}, "pts": 0})
    for r in rows:
        x = depts[r["dept"]]
        x["groups"] += 1
        x["students"] += r["students"]
        x["pts"] += g_pts[r["gid"]]
        for k in GRADE_ORDER:
            x["d"][k] += r["d"][k]
    dept_rows = []
    for name, x in depts.items():
        n = sum(x["d"].values())
        m = calc_metrics(x["d"], x["pts"])
        dept_rows.append((name, x, n, m))
    dept_rows.sort(key=lambda t: (t[2] == 0, -t[3]["avg"], t[0]))
    for i, (name, x, n, m) in enumerate(dept_rows, 1):
        r = 5 + i
        put(ws_d, r, 1, name, align=LEFT, bold=True)
        put(ws_d, r, 2, x["groups"])
        put(ws_d, r, 3, x["students"])
        put(ws_d, r, 4, n)
        put(ws_d, r, 5, m["avg"] if n else "—", fmt="0.0", bold=True)
        put(ws_d, r, 6, m["uspev"] if n else "—", fmt="0.0", bold=True)
        for k, key in enumerate(["5", "4", "3", "<50", "Н"]):
            put(ws_d, r, 7 + k, pct(x["d"][key], n) if n else "—", fmt="0.0")
    set_widths(ws_d, [38, 9, 11, 10, 13, 15, 9, 9, 9, 11, 9])
    nd = len(dept_rows)
    if nd:
        ws_d.add_chart(make_bar("Средний балл и успеваемость", ws_d, 1, [5, 6], 5, 6, 5 + nd, False,
                                [C_BLUE, C_GREEN], 16, 9, ymax=100, labels=True), f"A{nd + 8}")
        ws_d.add_chart(make_bar("Структура оценок по отделениям", ws_d, 1, [7, 8, 9, 10, 11], 5, 6, 5 + nd, False,
                                [DIST_COLORS[k] for k in DIST_KEYS], 16, 9, stacked=True), f"G{nd + 8}")

    # --- Предметы по группам ---
    ws_s = wb.create_sheet(safe_title("Предметы по группам", used))
    title_block(ws_s, ["Успеваемость по предметам в выбранных группах", f"Сформирован: {now_str()}", SCALE_NOTE])
    header_row(ws_s, 5, ["Группа", "Предмет", "Преподаватель", "Оценок", "Средний балл", "Успеваемость, %"])
    gname_by_id = {g[0]: g[1] for g in groups}
    items = []
    for wid, (gid, subject, teacher) in wl_meta.items():
        if gid not in gids:
            continue
        n = sum(w_dist[wid].values()) if wid in w_dist else 0
        if n == 0:
            continue
        m = calc_metrics(w_dist[wid], w_pts[wid])
        items.append((gname_by_id[gid], subject, teacher, n, m["avg"], m["uspev"]))
    items.sort(key=lambda t: (t[0], t[1], t[2]))
    for i, it in enumerate(items, 1):
        r = 5 + i
        put(ws_s, r, 1, it[0], align=LEFT, bold=True)
        put(ws_s, r, 2, it[1], align=LEFT)
        put(ws_s, r, 3, it[2] or "—", align=LEFT)
        put(ws_s, r, 4, it[3])
        put(ws_s, r, 5, it[4], fmt="0.0")
        put(ws_s, r, 6, it[5], fmt="0.0")
    set_widths(ws_s, [16, 52, 26, 9, 13, 15])
    ws_s.freeze_panes = "A6"
    if items:
        ws_s.auto_filter.ref = f"A5:F{5 + len(items)}"
        ws_s.conditional_formatting.add(
            f"E6:E{5 + len(items)}",
            ColorScaleRule(start_type="min", start_color="FCA5A5", mid_type="percentile", mid_value=50,
                           mid_color="FDE68A", end_type="max", end_color="86EFAC"))

    build_compare_dashboard(wb, ws, hdr, rows, overall, all_dist, ws_d, nd, len(groups))
    explanations_sheet(wb, used)
    return to_bytes(wb), len(groups)


# ----------------------------------------------------------------------
# Диаграммы и дашборды
# ----------------------------------------------------------------------
DIST_KEYS = ["5", "4", "3", "<50", "Н"]
DIST_COLORS = {"5": "10B981", "4": "3B82F6", "3": "F59E0B", "<50": "EF4444", "Н": "94A3B8"}
DIST_NAMES = {"5": "«5» (90–100)", "4": "«4» (70–89)", "3": "«3» (50–69)", "<50": "Неаттестация (<50)", "Н": "«Н» (0 баллов)"}
C_BLUE, C_GREEN, C_RED, C_GREY, C_AMBER = "3B82F6", "10B981", "EF4444", "94A3B8", "F59E0B"
ROW_CM = 0.705            # высота строки дашборда (20 pt) в сантиметрах


def short_name(full):
    parts = str(full).split()
    if len(parts) >= 3:
        return f"{parts[0]} {parts[1][0]}.{parts[2][0]}."
    if len(parts) == 2:
        return f"{parts[0]} {parts[1][0]}."
    return str(full)


def rows_for(h_cm):
    return int(h_cm / ROW_CM) + 2


def _fill(series, color):
    series.graphicalProperties.solidFill = color
    series.graphicalProperties.line.solidFill = color


def _labels(show_percent=False):
    dl = DataLabelList()
    dl.showVal = not show_percent
    dl.showPercent = show_percent
    dl.showSerName = dl.showCatName = dl.showLegendKey = dl.showLeaderLines = False
    return dl


def make_bar(title, ws, cats_col, val_cols, hdr, first, last, horizontal, colors, w, h,
             stacked=False, legend=True, labels=False, ymax=None):
    """Столбчатая диаграмма. Заголовки рядов берутся из строки hdr (она должна идти прямо над данными)."""
    ch = BarChart()
    ch.type = "bar" if horizontal else "col"
    ch.grouping = "percentStacked" if stacked else "clustered"
    if stacked:
        ch.overlap = 100
    ch.gapWidth = 40 if stacked else 55
    ch.title = title
    ch.width, ch.height = w, h
    for col, color in zip(val_cols, colors):
        s = Series(Reference(ws, min_col=col, min_row=hdr, max_row=last), title_from_data=True)
        _fill(s, color)
        ch.series.append(s)
    ch.set_categories(Reference(ws, min_col=cats_col, min_row=first, max_row=last))
    ch.x_axis.delete = False
    ch.y_axis.delete = False
    if horizontal:
        ch.x_axis.scaling.orientation = "maxMin"      # первая строка таблицы — сверху
        ch.y_axis.crosses = "max"
    if stacked:
        ch.y_axis.number_format = "0%"
    elif ymax:
        ch.y_axis.scaling.min = 0
        ch.y_axis.scaling.max = ymax
    if legend:
        ch.legend.position = "b"
    else:
        ch.legend = None
    if labels:
        ch.dataLabels = _labels()
    return ch


def make_doughnut(title, ws, label_col, value_col, first, last, keys, w=16.5, h=8.5):
    ch = DoughnutChart()
    ch.holeSize = 55
    ch.title = title
    ch.width, ch.height = w, h
    ch.add_data(Reference(ws, min_col=value_col, min_row=first, max_row=last), titles_from_data=False)
    ch.set_categories(Reference(ws, min_col=label_col, min_row=first, max_row=last))
    s = ch.series[0]
    for i, k in enumerate(keys):
        pt = DataPoint(idx=i)
        pt.graphicalProperties.solidFill = DIST_COLORS[k]
        pt.graphicalProperties.line.solidFill = "FFFFFF"
        s.dPt.append(pt)
    ch.dataLabels = _labels(show_percent=True)
    ch.legend.position = "r"
    return ch


def prepare_dashboard(ws, title_lines, widths, tab_color):
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = tab_color
    set_widths(ws, widths)
    title_block(ws, title_lines)
    for r in range(1, 200):
        ws.row_dimensions[r].height = 20
    ws.row_dimensions[1].height = 26


def section_title(ws, row, text, col=1):
    c = ws.cell(row=row, column=col, value=text)
    c.font = Font(bold=True, size=12, color=NAVY)
    c.alignment = Alignment(horizontal="left", vertical="center")


def side_panel(ws, row, kpis):
    """Колонка ключевых показателей: подпись | значение, с цветной полоской слева."""
    section_title(ws, row, "Ключевые показатели")
    for i, (label, value, fmt, color) in enumerate(kpis):
        r = row + 1 + i
        c1 = put(ws, r, 1, label, align=LEFT)
        c1.border = Border(left=Side(style="thick", color=color), right=THIN, top=THIN, bottom=THIN)
        c2 = put(ws, r, 2, value, fmt=fmt)
        c2.font = Font(bold=True, size=12, color=color if color != C_GREY else "475569")
        put(ws, r, 3, None)
    return row + 1 + len(kpis)


def dist_table(ws, row, dist):
    """Таблица «категория | оценок | доля». Возвращает (первая, последняя) строки данных."""
    section_title(ws, row, "Распределение оценок")
    header_row(ws, row + 1, ["Категория", "Оценок", "Доля, %"])
    ws.row_dimensions[row + 1].height = 22
    total = sum(dist.values())
    for i, k in enumerate(DIST_KEYS):
        r = row + 2 + i
        put(ws, r, 1, DIST_NAMES[k], align=LEFT, fill=LEVEL_FILL[k])
        put(ws, r, 2, dist[k], fmt="#,##0")
        put(ws, r, 3, pct(dist[k], total), fmt="0.0")
    return row + 2, row + 1 + len(DIST_KEYS)


def mini_table(ws, row, title, headers, data, fmts=None):
    section_title(ws, row, title)
    header_row(ws, row + 1, headers)
    ws.row_dimensions[row + 1].height = 30
    for i, vals in enumerate(data):
        for j, v in enumerate(vals):
            put(ws, row + 2 + i, 1 + j, v, fmt=(fmts[j] if fmts else None), align=LEFT if j == 0 else CENTER)
    return row + 2 + len(data)


def build_compare_dashboard(wb, ws_tab, hdr, rows, overall, all_dist, ws_dept, dept_n, n_groups):
    ws = wb.create_sheet("Дашборд", 0)
    prepare_dashboard(ws, ["Дашборд: сравнение групп", f"Групп в отчёте: {n_groups} · Сформирован: {now_str()}", SCALE_NOTE],
                      [36, 13, 12, 2] + [11] * 24, C_BLUE)
    total = sum(all_dist.values())
    kpis = [("Групп в отчёте", n_groups, "0", C_BLUE),
            ("Студентов", sum(r["students"] for r in rows), "#,##0", C_BLUE),
            ("Оценок выставлено", total, "#,##0", C_BLUE),
            ("Средний балл (из 100)", overall["avg"], "0.0", C_GREEN),
            ("Успеваемость, %", overall["uspev"], "0.0", C_GREEN),
            ("Неаттестация, %", pct(all_dist["<50"], total), "0.0", C_RED),
            ("Пропуски «Н», %", pct(all_dist["Н"], total), "0.0", C_GREY)]
    r = side_panel(ws, 5, kpis) + 1
    dfirst, dlast = dist_table(ws, r, all_dist)
    r = dlast + 2

    active = [x for x in rows if x["n"] > 0]
    if len(active) >= 6:
        r = mini_table(ws, r, "Лидеры по среднему баллу", ["Группа", "Ср. балл", "Успев., %"],
                       [(x["name"], x["avg"], x["uspev"]) for x in active[:5]], [None, "0.0", "0.0"]) + 1
        r = mini_table(ws, r, "Требуют внимания", ["Группа", "Ср. балл", "Успев., %"],
                       [(x["name"], x["avg"], x["uspev"]) for x in reversed(active[-5:])], [None, "0.0", "0.0"]) + 1

    n = len(active)
    if n == 0:
        return ws
    last = hdr + n
    horizontal = n > 12
    h_rank = min(26, max(9, 0.55 * n + 3.5)) if horizontal else 9.5
    cw = 16.5

    cur = 5
    ws.add_chart(make_doughnut("Распределение оценок", ws, 1, 2, dfirst, dlast, DIST_KEYS, cw, 8.5), f"E{cur}")
    if dept_n:
        ch = make_bar("Отделения: средний балл и успеваемость", ws_dept, 1, [5, 6], 5, 6, 5 + dept_n, False,
                      [C_BLUE, C_GREEN], cw, 8.5, ymax=100, labels=True)
        ws.add_chart(ch, f"M{cur}")
    cur += rows_for(8.5) + 1

    ws.add_chart(make_bar("Средний балл по группам (рейтинг)", ws_tab, 2, [6], hdr, hdr + 1, last, horizontal,
                          [C_BLUE], cw, h_rank, legend=False, labels=True, ymax=100), f"E{cur}")
    ws.add_chart(make_bar("Успеваемость по группам, %", ws_tab, 2, [7], hdr, hdr + 1, last, horizontal,
                          [C_GREEN], cw, h_rank, legend=False, labels=True, ymax=100), f"M{cur}")
    cur += rows_for(h_rank) + 1

    stacked = make_bar("Структура оценок по группам", ws_tab, 2, [8, 9, 10, 11, 12], hdr, hdr + 1, last, horizontal,
                       [DIST_COLORS[k] for k in DIST_KEYS], cw * 2 + 1 if horizontal else cw, h_rank if horizontal else 9.5,
                       stacked=True)
    ws.add_chart(stacked, f"E{cur}")

    if 2 <= n <= 6:                                       # профиль распределения — радар для малого числа групп
        radar = RadarChart()
        radar.type = "marker"
        radar.title = "Профиль распределения оценок, %"
        radar.width, radar.height = cw, 9.5
        palette = [C_BLUE, C_RED, C_GREEN, C_AMBER, "8B5CF6", "0EA5E9"]
        for i in range(n):
            rr = hdr + 1 + i
            s = Series(Reference(ws_tab, min_col=8, max_col=12, min_row=rr, max_row=rr), title=rows[i]["name"])
            s.graphicalProperties.line.solidFill = palette[i]
            s.graphicalProperties.line.width = 28000
            radar.series.append(s)
        radar.set_categories(Reference(ws_tab, min_col=8, max_col=12, min_row=hdr, max_row=hdr))
        radar.legend.position = "b"
        ws.add_chart(radar, f"M{cur}")
    return ws


def build_group_dashboard(wb, gname, dept, spec, n_students, total_dist, gm, ws_sub, n_sub_active, ws_sum, sum_hdr,
                          base_col, overalls):
    ws = wb.create_sheet("Дашборд", 0)
    prepare_dashboard(ws, [f"Дашборд группы {gname}", f"Отделение: {dept or '—'} · Специальность: {spec or '—'}",
                           f"Сформирован: {now_str()}", SCALE_NOTE], [36, 13, 12, 2] + [11] * 24, C_BLUE)
    total = sum(total_dist.values())
    at_risk = sum(1 for _, o, _, _ in overalls if o < 50)
    kpis = [("Студентов в группе", n_students, "0", C_BLUE),
            ("Предметов с оценками", n_sub_active, "0", C_BLUE),
            ("Оценок выставлено", total, "#,##0", C_BLUE),
            ("Средний балл (из 100)", gm["avg"], "0.0", C_GREEN),
            ("Успеваемость, %", gm["uspev"], "0.0", C_GREEN),
            ("Студентов с итогом < 50", at_risk, "0", C_RED),
            ("Пропуски «Н», %", pct(total_dist["Н"], total), "0.0", C_GREY)]
    r = side_panel(ws, 5, kpis) + 1
    dfirst, dlast = dist_table(ws, r, total_dist)
    r = dlast + 2
    if overalls:
        weakest = sorted(overalls, key=lambda x: (x[1], -x[2]))[:8]
        r = mini_table(ws, r, "Зона риска: самые низкие итоги", ["Студент", "Итог, баллы", "Кол-во «Н»"],
                       [(short_name(n), o, a) for n, o, a, _ in weakest], [None, "0", "0"]) + 1
        best = sorted(overalls, key=lambda x: (-x[1], x[2]))[:5]
        r = mini_table(ws, r, "Лучшие студенты", ["Студент", "Итог, баллы", "Кол-во «Н»"],
                       [(short_name(n), o, a) for n, o, a, _ in best], [None, "0", "0"]) + 1
    if total == 0:
        return ws

    cw = 16.5
    cur = 5
    ws.add_chart(make_doughnut("Распределение оценок группы", ws, 1, 2, dfirst, dlast, DIST_KEYS, cw, 8.5), f"E{cur}")
    h_sub = min(26, max(9, 0.95 * n_sub_active + 3.5))     # два ряда на предмет + длинные названия
    ws.add_chart(make_bar("Предметы: средний балл и успеваемость", ws_sub, 1, [5, 6], 5, 6, 5 + n_sub_active, True,
                          [C_BLUE, C_GREEN], cw, max(h_sub, 8.5), ymax=100, labels=True), f"M{cur}")
    cur += rows_for(max(8.5, h_sub)) + 1

    ns = n_students
    h_st = min(26, max(9, 0.55 * ns + 3.5))
    first, last = sum_hdr + 1, sum_hdr + ns
    ws.add_chart(make_bar("Средний балл студентов", ws_sum, 2, [base_col], sum_hdr, first, last, True,
                          [C_BLUE], cw, h_st, legend=False, labels=True, ymax=100), f"E{cur}")
    ws.add_chart(make_bar("Пропуски «Н» по студентам", ws_sum, 2, [base_col + 2], sum_hdr, first, last, True,
                          [C_GREY], cw, h_st, legend=False, labels=True), f"M{cur}")
    cur += rows_for(h_st) + 1

    ws.add_chart(make_bar("Структура оценок по предметам", ws_sub, 1, [7, 8, 9, 10, 11], 5, 6, 5 + n_sub_active, True,
                          [DIST_COLORS[k] for k in DIST_KEYS], cw * 2 + 1, max(9, h_sub), stacked=True), f"E{cur}")
    return ws


def build_journal_charts(wb, ws_j, layout, grades_dist, subject, gname):
    hdr, foot, base = layout["hdr"], layout["foot"], layout["base"]
    nl, ns = layout["n_lessons"], layout["n_students"]
    ws = wb.create_sheet("Диаграммы")
    prepare_dashboard(ws, [f"Диаграммы: {subject}", f"Группа: {gname} · Сформировано: {now_str()}", SCALE_NOTE],
                      [36, 13, 12, 2] + [11] * 24, C_BLUE)
    total = sum(grades_dist.values())
    if total == 0 or ns == 0:
        ws.cell(row=5, column=1, value="Оценок пока нет — диаграммы появятся после заполнения ведомости.")
        return
    dfirst, dlast = dist_table(ws, 5, grades_dist)
    cw = 16.5
    ws.add_chart(make_doughnut("Распределение оценок", ws, 1, 2, dfirst, dlast, DIST_KEYS, cw, 8.5), "E5")
    if nl:
        ch = BarChart()
        ch.type = "col"
        ch.title = "Средний балл по колонкам"
        ch.width, ch.height = cw, 8.5
        s = Series(Reference(ws_j, min_col=3, max_col=2 + nl, min_row=foot, max_row=foot), title="Средний балл")
        _fill(s, C_BLUE)
        ch.series.append(s)
        ch.set_categories(Reference(ws_j, min_col=3, max_col=2 + nl, min_row=hdr, max_row=hdr))
        ch.x_axis.delete = False
        ch.y_axis.delete = False
        ch.y_axis.scaling.min = 0
        ch.y_axis.scaling.max = 100
        ch.legend = None
        ch.dataLabels = _labels()
        ws.add_chart(ch, "M5")
    h = min(26, max(9, 0.55 * ns + 3.5))
    cur = 5 + rows_for(8.5) + 1
    ws.add_chart(make_bar("Итог по студентам, баллы", ws_j, 2, [base], hdr, hdr + 1, hdr + ns, True, [C_BLUE],
                          cw, h, legend=False, labels=True, ymax=100), f"E{cur}")
    ws.add_chart(make_bar("Пропуски «Н» по студентам", ws_j, 2, [base + 2], hdr, hdr + 1, hdr + ns, True, [C_GREY],
                          cw, h, legend=False, labels=True), f"M{cur}")
