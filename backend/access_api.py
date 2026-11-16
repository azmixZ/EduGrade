# backend/access_api.py
"""Управление доступом (только администратор): какие группы и дисциплины/практики закреплены за преподавателем,
кто куратор группы. Нагрузка = строка workloads (преподаватель + предмет + группа) — она и открывает ведомость.
Подключается в main.py: app.include_router(access_api.router)."""
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

import auth
import models
from common import add_log, client_ip
from database import get_db

router = APIRouter()

_PRACTICE_RE = re.compile(r"практик|\(\s*П[ПО]\s*\)|\bП[ПО]\s*$|кәсіптік практика", re.IGNORECASE)


def is_practice(subject: models.Subject) -> bool:
    """Практика — если так помечено при создании или если это видно из названия (ПП, ПО, «практика»)."""
    return (subject.kind or "") == "practice" or bool(_PRACTICE_RE.search(subject.name or ""))


def _clean_name(raw: str) -> str:
    name = re.sub(r"\s+", " ", raw or "").strip()
    if not 2 <= len(name) <= 300 or re.search(r"[<>\x00-\x1f]", name):
        raise HTTPException(status_code=400, detail="Название: от 2 до 300 символов, без < и >")
    return name


def _teacher(db: Session, uid: int) -> models.User:
    u = db.get(models.User, uid)
    if not u:
        raise HTTPException(status_code=404, detail="Преподаватель не найден")
    return u


def _group(db: Session, gid: int) -> models.Group:
    g = db.get(models.Group, gid)
    if not g:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    return g


def _grade_counts(db: Session, workload_ids) -> dict:
    if not workload_ids:
        return {}
    rows = (db.query(models.Lesson.workload_id, func.count(models.Grade.id))
            .join(models.Grade, models.Grade.lesson_id == models.Lesson.id)
            .filter(models.Lesson.workload_id.in_(list(workload_ids)))
            .group_by(models.Lesson.workload_id).all())
    return dict(rows)


def _workload_rows(db: Session, query) -> list:
    rows = query.all()
    counts = _grade_counts(db, [w.id for w, *_ in rows])
    out = []
    for w, g, s, u in rows:
        out.append({
            "workload_id": w.id, "group_id": g.id, "group_name": g.name,
            "subject_id": s.id, "subject_name": s.name, "practice": is_practice(s),
            "teacher_id": u.id if u else None, "teacher": u.full_name if u else "—",
            "grades": counts.get(w.id, 0),
        })
    return out


def _base_query(db: Session):
    return (db.query(models.Workload, models.Group, models.Subject, models.User)
            .join(models.Group, models.Workload.group_id == models.Group.id)
            .join(models.Subject, models.Workload.subject_id == models.Subject.id)
            .outerjoin(models.User, models.Workload.teacher_id == models.User.id))


# ------------------------------------------------------------------ чтение
@router.get("/api/admin/access/overview")
def overview(db: Session = Depends(get_db), _: models.User = Depends(auth.check_admin)):
    """Справочники для вкладки: преподаватели (с числом ведомостей и кураторством), группы, предметы."""
    wl_by_teacher = dict(db.query(models.Workload.teacher_id, func.count(models.Workload.id))
                         .group_by(models.Workload.teacher_id).all())
    wl_by_group = dict(db.query(models.Workload.group_id, func.count(models.Workload.id))
                       .group_by(models.Workload.group_id).all())
    st_by_group = dict(db.query(models.Student.group_id, func.count(models.Student.id))
                       .group_by(models.Student.group_id).all())
    users = db.query(models.User).order_by(models.User.full_name).all()
    names = {u.id: u.full_name for u in users}
    groups = db.query(models.Group).order_by(models.Group.name).all()
    curated = {}
    for g in groups:
        if g.curator_id:
            curated.setdefault(g.curator_id, []).append(g.name)
    teachers = [{
        "id": u.id, "full_name": u.full_name, "username": u.username, "role": u.role,
        "is_active": bool(u.is_active), "workloads": wl_by_teacher.get(u.id, 0), "curated": curated.get(u.id, []),
    } for u in users if u.role == "teacher"]
    return {
        "teachers": teachers,
        "groups": [{"id": g.id, "name": g.name, "curator_id": g.curator_id, "curator": names.get(g.curator_id, ""),
                    "workloads": wl_by_group.get(g.id, 0), "students": st_by_group.get(g.id, 0)} for g in groups],
        "subjects": [{"id": s.id, "name": s.name, "practice": is_practice(s)}
                     for s in db.query(models.Subject).order_by(models.Subject.name).all()],
    }


@router.get("/api/admin/access/teacher/{uid}")
def teacher_access(uid: int, db: Session = Depends(get_db), _: models.User = Depends(auth.check_admin)):
    u = _teacher(db, uid)
    q = _base_query(db).filter(models.Workload.teacher_id == uid).order_by(models.Group.name, models.Subject.name)
    curated = db.query(models.Group).filter(models.Group.curator_id == uid).order_by(models.Group.name).all()
    return {
        "teacher": {"id": u.id, "full_name": u.full_name, "username": u.username, "role": u.role,
                    "is_active": bool(u.is_active), "last_login": u.last_login or ""},
        "workloads": _workload_rows(db, q),
        "curated": [{"id": g.id, "name": g.name} for g in curated],
    }


@router.get("/api/admin/access/group/{gid}")
def group_access(gid: int, db: Session = Depends(get_db), _: models.User = Depends(auth.check_admin)):
    g = _group(db, gid)
    q = _base_query(db).filter(models.Workload.group_id == gid).order_by(models.Subject.name)
    cur = db.get(models.User, g.curator_id) if g.curator_id else None
    students = db.query(func.count(models.Student.id)).filter(models.Student.group_id == gid).scalar()
    return {
        "group": {"id": g.id, "name": g.name, "curator_id": g.curator_id, "curator": cur.full_name if cur else "",
                  "students": students, "contingent": g.contingent},
        "workloads": _workload_rows(db, q),
    }


# ------------------------------------------------------------------ изменения
class WorkloadIn(BaseModel):
    teacher_id: int
    group_id: int
    subject_id: Optional[int] = None      # выбранный из справочника предмет
    subject_name: str = ""                # или новое название (создаётся, если такого ещё нет)
    practice: bool = False                # пометить как практику


class TeacherIn(BaseModel):
    teacher_id: int


class CuratorIn(BaseModel):
    group_id: int
    teacher_id: Optional[int] = None      # None = снять куратора


def _find_or_create_subject(db: Session, data: WorkloadIn) -> models.Subject:
    if data.subject_id:
        s = db.get(models.Subject, data.subject_id)
        if not s:
            raise HTTPException(status_code=404, detail="Предмет не найден")
    else:
        name = _clean_name(data.subject_name)
        key = name.casefold()
        s = next((x for x in db.query(models.Subject).all() if re.sub(r"\s+", " ", x.name).strip().casefold() == key), None)
        if s is None:
            s = models.Subject(name=name, kind="practice" if data.practice else "subject")
            db.add(s)
            db.flush()
    if data.practice and s.kind != "practice":
        s.kind = "practice"
    return s


@router.post("/api/admin/access/workloads")
def add_workload(data: WorkloadIn, request: Request, db: Session = Depends(get_db),
                 admin: models.User = Depends(auth.check_admin)):
    """Закрепить за преподавателем дисциплину или практику в группе (открывает ему ведомость)."""
    t = _teacher(db, data.teacher_id)
    g = _group(db, data.group_id)
    s = _find_or_create_subject(db, data)
    same = (db.query(models.Workload)
            .filter(models.Workload.group_id == g.id, models.Workload.subject_id == s.id).all())
    if any(w.teacher_id == t.id for w in same):
        db.rollback()
        raise HTTPException(status_code=400, detail=f"«{s.name}» в группе {g.name} уже закреплена за этим преподавателем")
    others = [w.teacher.full_name for w in same if w.teacher]
    w = models.Workload(teacher_id=t.id, subject_id=s.id, group_id=g.id)
    db.add(w)
    kind = "практику" if is_practice(s) else "дисциплину"
    add_log(db, admin.full_name, f"Закрепил {kind} за преподавателем {t.full_name}", group=g.name, subject=s.name,
            ip=client_ip(request))
    db.commit()
    warn = f"Этот предмет в группе уже ведёт: {', '.join(others)}. Если нужно передать ведомость — используйте «Передать»." if others else ""
    return {"status": "ok", "workload_id": w.id, "warning": warn}


@router.post("/api/admin/access/workloads/{wid}/teacher")
def move_workload(wid: int, data: TeacherIn, request: Request, db: Session = Depends(get_db),
                  admin: models.User = Depends(auth.check_admin)):
    """Передать ведомость другому преподавателю (оценки сохраняются)."""
    w = db.get(models.Workload, wid)
    if not w:
        raise HTTPException(status_code=404, detail="Ведомость не найдена")
    t = _teacher(db, data.teacher_id)
    if w.teacher_id == t.id:
        return {"status": "ok"}
    dup = (db.query(models.Workload).filter(models.Workload.group_id == w.group_id, models.Workload.subject_id == w.subject_id,
                                            models.Workload.teacher_id == t.id).first())
    if dup:
        raise HTTPException(status_code=400, detail="У этого преподавателя уже есть такая ведомость в этой группе")
    old = w.teacher.full_name if w.teacher else "—"
    w.teacher_id = t.id
    add_log(db, admin.full_name, f"Передал ведомость: {old} → {t.full_name}", group=w.group.name,
            subject=w.subject.name if w.subject else None, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


@router.delete("/api/admin/access/workloads/{wid}")
def delete_workload(wid: int, request: Request, force: bool = False, db: Session = Depends(get_db),
                    admin: models.User = Depends(auth.check_admin)):
    """Открепить. Если в ведомости уже есть оценки, нужен force=true (оценки удаляются вместе с ней)."""
    w = db.get(models.Workload, wid)
    if not w:
        raise HTTPException(status_code=404, detail="Ведомость не найдена")
    grades = _grade_counts(db, [w.id]).get(w.id, 0)
    if grades and not force:
        raise HTTPException(status_code=409, detail=f"В ведомости уже {grades} оценок")
    info = (w.group.name, w.subject.name if w.subject else None, w.teacher.full_name if w.teacher else "—")
    db.delete(w)                                   # колонки и оценки удаляются каскадом
    add_log(db, admin.full_name, f"Открепил ведомость у преподавателя {info[2]}" + (f" (удалено оценок: {grades})" if grades else ""),
            group=info[0], subject=info[1], ip=client_ip(request))
    db.commit()
    return {"status": "ok", "deleted_grades": grades}


@router.post("/api/admin/access/curator")
def set_curator(data: CuratorIn, request: Request, db: Session = Depends(get_db),
                admin: models.User = Depends(auth.check_admin)):
    g = _group(db, data.group_id)
    old = db.get(models.User, g.curator_id).full_name if g.curator_id and db.get(models.User, g.curator_id) else ""
    if data.teacher_id is None:
        g.curator_id = None
        action = f"Снял кураторство ({old or '—'})"
    else:
        t = _teacher(db, data.teacher_id)
        g.curator_id = t.id
        action = f"Назначил куратором: {t.full_name}" + (f" (был: {old})" if old and old != t.full_name else "")
    add_log(db, admin.full_name, action, group=g.name, ip=client_ip(request))
    db.commit()
    return {"status": "ok", "previous": old}
