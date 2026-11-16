# backend/students_api.py
"""Состав группы: куратор (своя группа) и администратор (любая) добавляют, переименовывают и удаляют студентов;
администратор ещё переводит студента в другую группу (оценки сохраняются).
Подключается в main.py: app.include_router(students_api.router)."""
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

import auth
import models
import security as sec
from common import add_log, client_ip
from curator_api import _check_group
from database import get_db

router = APIRouter()


class StudentIn(BaseModel):
    full_name: str


class StudentEdit(BaseModel):
    full_name: Optional[str] = None
    group_id: Optional[int] = None        # перевод в другую группу — только администратор


def _name(raw: str) -> str:
    name = re.sub(r"\s+", " ", raw or "").strip()
    if not sec.valid_full_name(name):
        raise HTTPException(status_code=400, detail="ФИО: от 3 до 100 символов, без < > и кавычек")
    return name


def _group(db: Session, gid: int) -> models.Group:
    g = db.get(models.Group, gid)
    if not g:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    return g


def _student(db: Session, user: models.User, sid: int) -> models.Student:
    s = db.get(models.Student, sid)
    if not s:
        raise HTTPException(status_code=404, detail="Студент не найден")
    _check_group(db, user, s.group_id)
    return s


def _duplicate(db: Session, gid: int, name: str, skip_id: int = 0) -> bool:
    key = name.casefold()
    return any(re.sub(r"\s+", " ", s.full_name).strip().casefold() == key
               for s in db.query(models.Student).filter(models.Student.group_id == gid, models.Student.id != skip_id))


def _grades(db: Session, sid: int) -> int:
    return db.query(func.count(models.Grade.id)).filter(models.Grade.student_id == sid).scalar() or 0


@router.get("/api/curator/group/{gid}/students")
def list_students(gid: int, db: Session = Depends(get_db), user: models.User = Depends(auth.get_current_user)):
    _check_group(db, user, gid)
    g = _group(db, gid)
    counts = dict(db.query(models.Grade.student_id, func.count(models.Grade.id))
                  .join(models.Student, models.Student.id == models.Grade.student_id)
                  .filter(models.Student.group_id == gid).group_by(models.Grade.student_id).all())
    students = db.query(models.Student).filter(models.Student.group_id == gid).order_by(models.Student.full_name).all()
    return {"group": {"id": g.id, "name": g.name, "contingent": g.contingent},
            "students": [{"id": s.id, "full_name": s.full_name, "grades": counts.get(s.id, 0)} for s in students]}


@router.post("/api/curator/group/{gid}/students")
def add_student(gid: int, data: StudentIn, request: Request, db: Session = Depends(get_db),
                user: models.User = Depends(auth.get_current_user)):
    _check_group(db, user, gid)
    g = _group(db, gid)
    name = _name(data.full_name)
    if _duplicate(db, gid, name):
        raise HTTPException(status_code=400, detail=f"«{name}» уже есть в группе {g.name}")
    s = models.Student(full_name=name, group_id=gid)
    db.add(s)
    add_log(db, user.full_name, "Добавил студента в группу", group=g.name, student=name, ip=client_ip(request))
    db.commit()
    return {"status": "ok", "id": s.id}


@router.post("/api/curator/students/{sid}")
def edit_student(sid: int, data: StudentEdit, request: Request, db: Session = Depends(get_db),
                 user: models.User = Depends(auth.get_current_user)):
    s = _student(db, user, sid)
    old_name, old_group = s.full_name, s.group.name if s.group else "—"
    ip = client_ip(request)
    if data.group_id is not None and data.group_id != s.group_id:
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="Переводить студентов в другую группу может только администратор")
        ng = _group(db, data.group_id)
        if _duplicate(db, ng.id, data.full_name and _name(data.full_name) or s.full_name):
            raise HTTPException(status_code=400, detail=f"В группе {ng.name} уже есть студент с таким ФИО")
        s.group_id = ng.id
        add_log(db, user.full_name, f"Перевёл студента: {old_group} → {ng.name}", group=ng.name, student=s.full_name, ip=ip)
    if data.full_name is not None:
        name = _name(data.full_name)
        if name != s.full_name:
            if _duplicate(db, s.group_id, name, skip_id=s.id):
                raise HTTPException(status_code=400, detail="В группе уже есть студент с таким ФИО")
            s.full_name = name
            add_log(db, user.full_name, f"Исправил ФИО студента: {old_name} → {name}",
                    group=db.get(models.Group, s.group_id).name, student=name, ip=ip)
    db.commit()
    return {"status": "ok"}


@router.delete("/api/curator/students/{sid}")
def delete_student(sid: int, request: Request, force: bool = False, db: Session = Depends(get_db),
                   user: models.User = Depends(auth.get_current_user)):
    """Удалить студента. Если у него уже есть оценки, нужен force=true (оценки удаляются вместе с ним)."""
    s = _student(db, user, sid)
    n = _grades(db, sid)
    if n and not force:
        raise HTTPException(status_code=409, detail=f"У студента уже {n} оценок")
    info = (s.full_name, s.group.name if s.group else "—")
    db.delete(s)                                   # оценки удаляются каскадом
    add_log(db, user.full_name, "Удалил студента из группы" + (f" (удалено оценок: {n})" if n else ""),
            group=info[1], student=info[0], ip=client_ip(request))
    db.commit()
    return {"status": "ok", "deleted_grades": n}
