# backend/notify.py
"""Уведомления «у студента единственная тройка» и окно «Что нового» после обновления.

Единственная тройка: у студента выставлено не меньше MIN_SUBJECTS итоговых, нет неаттестации,
ровно один предмет на 50–69 баллов, остальные 70 и выше. Подтянуть этот предмет — и студент хорошист.
  • преподаватель видит таких студентов, если тройка по ЕГО предмету;
  • куратор — всех таких студентов своей группы;
  • администратор — всех по колледжу.
Считается по периоду аттестации (преподавателю — текущий по календарю).
"""
from collections import defaultdict
from statistics import mean

from grading import round_half_up
from risk import PTS

MIN_SUBJECTS = 3          # меньше выставленных итоговых — рано делать выводы
FOUR = 70                 # с этого балла — «4»

# ---- окно «Что нового»: поменяйте NEWS_VERSION, и каждый пользователь увидит окно один раз при следующем входе.
# Пункты по ролям: «teacher» — всем преподавателям, «curator» — кураторам, «head» — заведующим отделением,
# «admin» — администратору. Если для роли пунктов нет, окно ей не показывается.
NEWS_VERSION = "2026-11-11"
NEWS_TITLE = "Обновление системы"
NEWS = {
    "teacher": [],
    "curator": [],
    "head": [
        "Вы назначены заведующим отделением. В меню появился раздел «Аналитика и контроль» — всё только по вашему отделению.",
        "«Главная панель», «Успеваемость» и «Аналитика»: студенты, оценки и успеваемость групп отделения, сравнение групп.",
        "«Группа риска» и «Можно подтянуть»: слабые студенты и те, кому до хорошиста не хватает одной-двух троек — "
        "с предметом и преподавателем, у которого тройка.",
        "«Группы отделения»: сводная по любой группе — у какого преподавателя студент отстаёт, кто не выставил итоговые, "
        "кому осталась одна тройка до хорошиста. Все отчёты скачиваются в Excel.",
        "Своя ведомость — как раньше, в «Итоговой ведомости».",
    ],
    "admin": [
        "Роль «заведующий отделением»: в «Управлении правами» — колонка «Зав. отделением». Выберите отделение, и преподаватель "
        "увидит аналитику, группу риска, «Можно подтянуть» и сводные всех групп этого отделения.",
        "Заведующий не может управлять учётными записями и паролями, видеть журнал действий и чужие отделения.",
    ],
}


def news_for(role: str, is_curator: bool, is_head: bool = False) -> list:
    if role == "admin":
        return NEWS["admin"]
    return NEWS["teacher"] + (NEWS["curator"] if is_curator else []) + (NEWS["head"] if is_head else [])


def single_threes(q, teacher_id=None, curator_id=None, group_id=None):
    """q — runner за один период (periods.runner). teacher_id и curator_id вместе — «или»: тройка по моему
    предмету ИЛИ студент моей группы. Без фильтров — весь колледж."""
    students = {r[0]: (r[1], r[2], r[3], r[4]) for r in q("""
        SELECT s.id, s.full_name, g.id, g.name, g.curator_id FROM students s JOIN groups g ON g.id = s.group_id""")}
    cell = defaultdict(list)
    meta = {}
    for sid, wid, subj, tid, teacher, pts in q(f"""
        SELECT g.student_id, w.id, sb.name, w.teacher_id, COALESCE(u.full_name, '—'), {PTS}
        FROM grades g JOIN lessons l ON l.id = g.lesson_id JOIN workloads w ON w.id = l.workload_id
        JOIN subjects sb ON sb.id = w.subject_id LEFT JOIN users u ON u.id = w.teacher_id
        WHERE ({PTS}) IS NOT NULL"""):
        cell[(sid, wid)].append(pts)
        meta[wid] = (subj, tid, teacher)

    per_student = defaultdict(list)
    for (sid, wid), lst in cell.items():
        per_student[sid].append((wid, round_half_up(mean(lst))))

    items = []
    for sid, res in per_student.items():
        st = students.get(sid)
        if not st or len(res) < MIN_SUBJECTS:
            continue
        pts = [p for _, p in res]
        if min(pts) < 50:
            continue
        threes = [(wid, p) for wid, p in res if p < FOUR]
        if len(threes) != 1:
            continue
        wid, p = threes[0]
        subj, tid, teacher = meta[wid]
        name, gid, gname, cur_id = st
        if group_id is not None and gid != group_id:
            continue
        if teacher_id is not None and curator_id is not None:
            if tid != teacher_id and cur_id != curator_id:
                continue
        elif teacher_id is not None and tid != teacher_id:
            continue
        elif curator_id is not None and cur_id != curator_id:
            continue
        items.append({
            "key": f"{sid}:{wid}:{p}", "student_id": sid, "workload_id": wid, "group_id": gid,                     # меняется, если поменялся балл — уведомление снова «новое»
            "student": name, "group": gname, "subject": subj, "teacher": teacher, "points": p, "gap": FOUR - p,
            "avg": round_half_up(mean(pts)), "subjects": len(res),
            "mine": tid == teacher_id, "my_group": cur_id == curator_id,
        })
    items.sort(key=lambda x: (x["gap"], x["group"], x["student"]))
    return items
