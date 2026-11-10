# backend/users_api.py
"""Выгрузки по пользователям: список учётных записей и выдача новых временных паролей (Excel).

Пароли в базе хранятся только в виде bcrypt-хешей, поэтому «посмотреть» существующий пароль нельзя.
Вместо этого администратор выдаёт НОВЫЕ временные пароли: они попадают в файл один раз, а пользователь
при первом входе обязан сменить пароль на свой (флаг must_change_password).
"""
from collections import defaultdict
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

import auth
import models
import security as sec
from common import add_log, client_ip, simple_xlsx, xlsx_response
from database import get_db

router = APIRouter()
MAX_ISSUE = 200                       # bcrypt медленный (по умолчанию ~0,2 с на пароль) — ограничиваем размер пачки


def _workload_text(db: Session) -> dict:
    """teacher_id -> «ПР-1-25: Математика, Физика; ЗУ-1-24: …» (для печатного списка)."""
    by = defaultdict(lambda: defaultdict(list))
    rows = (db.query(models.Workload.teacher_id, models.Group.name, models.Subject.name)
            .join(models.Group, models.Workload.group_id == models.Group.id)
            .join(models.Subject, models.Workload.subject_id == models.Subject.id)
            .order_by(models.Group.name, models.Subject.name).all())
    for tid, g, s in rows:
        by[tid][g].append(s)
    return {tid: "; ".join(f"{g}: {', '.join(subs)}" for g, subs in gs.items()) for tid, gs in by.items()}


def _workload_counts(db: Session) -> dict:
    cnt = defaultdict(int)
    for (tid,) in db.query(models.Workload.teacher_id).all():
        cnt[tid] += 1
    return cnt


@router.get("/api/export/users")
def export_users(request: Request, db: Session = Depends(get_db), admin: models.User = Depends(auth.check_admin)):
    """Список учётных записей (без паролей): логин, ФИО, роль, статус, последний вход, нагрузка."""
    users = db.query(models.User).order_by(models.User.role, models.User.full_name).all()
    wl = _workload_text(db)
    cnt = _workload_counts(db)
    rows = [[i, u.username, u.full_name, "Администратор" if u.role == "admin" else "Преподаватель",
             "Активна" if u.is_active else "Отключена", u.last_login or "не входил(а)",
             "да" if u.must_change_password else "нет", cnt.get(u.id, 0), wl.get(u.id, "—")]
            for i, u in enumerate(users, 1)]
    data = simple_xlsx("Пользователи",
                       ["№", "Логин", "ФИО", "Роль", "Статус", "Последний вход", "Обязан сменить пароль",
                        "Ведомостей", "Нагрузка (группа: предметы)"],
                       rows, [5, 20, 36, 16, 12, 18, 14, 12, 90],
                       notes=[f"Сформировано: {datetime.now():%d.%m.%Y %H:%M}. Пароли в системе хранятся в зашифрованном виде и "
                              "здесь не показываются; новые пароли выдаются отдельной выгрузкой."])
    add_log(db, admin.full_name, "Выгрузил список пользователей (Excel)", ip=client_ip(request))
    db.commit()
    return xlsx_response(data, f"Пользователи_{datetime.now():%Y-%m-%d}.xlsx")


class IssueBatch(BaseModel):
    ids: List[int]


@router.post("/api/admin/users/issue-passwords")
def issue_passwords(data: IssueBatch, request: Request, db: Session = Depends(get_db),
                    admin: models.User = Depends(auth.check_admin)):
    """Выдаёт новые временные пароли выбранным пользователям и возвращает Excel с логинами и паролями.
    Старые пароли и открытые сессии этих пользователей перестают действовать."""
    ids = list(dict.fromkeys(data.ids))
    if not ids:
        raise HTTPException(status_code=400, detail="Не выбрано ни одного пользователя")
    if len(ids) > MAX_ISSUE:
        raise HTTPException(status_code=400, detail=f"За один раз можно выдать не более {MAX_ISSUE} паролей")

    users = db.query(models.User).filter(models.User.id.in_(ids), models.User.id != admin.id) \
        .order_by(models.User.role, models.User.full_name).all()
    if not users:
        raise HTTPException(status_code=400, detail="Нет подходящих пользователей (свой пароль меняется через «Сменить пароль»)")

    wl = _workload_text(db)
    rows = []
    for i, u in enumerate(users, 1):
        pw = sec.generate_temp_password()
        u.password_hash = auth.get_password_hash(pw)
        u.must_change_password = True
        u.token_version = (u.token_version or 0) + 1
        rows.append([i, u.full_name, u.username, pw, "Администратор" if u.role == "admin" else "Преподаватель",
                     wl.get(u.id, "—")])
    add_log(db, admin.full_name, f"Выдал новые временные пароли: {len(users)} польз.", ip=client_ip(request))
    db.commit()

    xlsx = simple_xlsx("Новые пароли", ["№", "ФИО", "Логин", "Временный пароль", "Роль", "Нагрузка"], rows,
                       [5, 36, 20, 20, 16, 80],
                       notes=[f"Выдано: {datetime.now():%d.%m.%Y %H:%M} · администратор: {admin.full_name}.",
                              "Пароль показан один раз. Передайте его лично; при первом входе система потребует задать свой пароль.",
                              "После раздачи удалите файл."])
    return xlsx_response(xlsx, f"Новые_пароли_{datetime.now():%Y-%m-%d_%H%M}.xlsx")
