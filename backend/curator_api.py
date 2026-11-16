# backend/curator_api.py
"""API куратора, контроля заполнения и сверки контингента. Подключается в main.py: app.include_router(curator_api.router)."""
from datetime import datetime
from urllib.parse import quote

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.orm import Session

import auth
import curator
import models
import periods
from common import add_log, client_ip, xlsx_response
from database import get_db

router = APIRouter()


def _q(db: Session):
    return lambda sql, params=None: db.execute(text(sql), params or {}).fetchall()


def _check_group(db: Session, user: models.User, gid: int):
    """Куратор работает только со своей группой, администратор — с любой (состав группы правят только они)."""
    if user.role == "admin":
        return
    ok = db.execute(text("SELECT 1 FROM groups WHERE id = :g AND curator_id = :u"), {"g": gid, "u": user.id}).first()
    if not ok:
        raise HTTPException(status_code=403, detail="Вы не являетесь куратором этой группы")


def _check_view(db: Session, user: models.User, gid: int):
    """Смотреть сводную и выгрузки: куратор — свою группу, заведующий — группы своего отделения, админ — любую."""
    if user.head_department_id and user.role != "admin" and gid in auth.dept_group_ids(db, user.head_department_id):
        return
    _check_group(db, user, gid)


def _completeness(db: Session, gid: int, period: Optional[str]):
    """Контроль заполнения группы за период аттестации (пусто — текущий по календарю)."""
    per = periods.resolve(period)
    d = curator.completeness(periods.runner(db, per), gid)
    if d is not None:
        g = db.get(models.Group, gid)
        d["period"] = per
        d["period_title"] = periods.title(per, bool(g.graduating))
        d["group"]["graduating"] = bool(g.graduating)
    return d


class GraduatingIn(BaseModel):
    graduating: bool


@router.post("/api/admin/groups/{gid}/graduating")
def set_graduating(gid: int, data: GraduatingIn, request: Request, db: Session = Depends(get_db),
                   admin: models.User = Depends(auth.check_admin)):
    """Отметить группу выпускной: её итоги семестра называются «итоговая аттестация»."""
    g = db.get(models.Group, gid)
    if not g:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    g.graduating = bool(data.graduating)
    add_log(db, admin.full_name, ("Отметил группу выпускной" if data.graduating else "Снял отметку «выпускная»"),
            group=g.name, ip=client_ip(request))
    db.commit()
    return {"status": "ok", "graduating": g.graduating}


@router.get("/api/curator/groups")
def curator_groups(db: Session = Depends(get_db), user: models.User = Depends(auth.get_current_user)):
    """Группы куратора (у преподавателя — те, где он куратор; у заведующего — ещё и все группы отделения;
    у администратора — все). own = можно править состав группы."""
    items = curator.groups_list(_q(db), user.id, user.role == "admin")
    dept = auth.dept_scope(user)
    if dept is not None:
        mine = {g["id"] for g in items}
        items = sorted(items + [g for g in curator.groups_list(periods.runner(db, None, dept), user.id, True)
                                if g["id"] not in mine], key=lambda g: g["name"])
        for g in items:
            g["own"] = g["id"] in mine
    else:
        for g in items:
            g["own"] = True
    return items


@router.get("/api/curator/group/{gid}")
def curator_group(gid: int, period: Optional[str] = None, db: Session = Depends(get_db),
                  user: models.User = Depends(auth.get_current_user)):
    _check_view(db, user, gid)
    d = _completeness(db, gid, period)
    if d is None:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    for s in d["students"]:
        s.pop("results", None)                       # подробные итоги студентов нужны только для файлов родителям
    return d


@router.get("/api/curator/group/{gid}/summary")
def curator_summary(gid: int, period: Optional[str] = None, db: Session = Depends(get_db),
                    user: models.User = Depends(auth.get_current_user)):
    """Сводная таблица группы: студенты × предметы с итоговыми баллами и средними."""
    _check_view(db, user, gid)
    d = _completeness(db, gid, period)
    if d is None:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    return curator.summary(d)


@router.get("/api/export/curator/{gid}")
def export_curator(gid: int, request: Request, kind: str = "completeness", period: Optional[str] = None,
                   db: Session = Depends(get_db),
                   user: models.User = Depends(auth.get_current_user)):
    """kind = completeness (контроль заполнения, Excel) | summary (сводная ведомость, Excel)
    | parents (ZIP: по файлу на студента для родителей)."""
    if kind not in ("completeness", "parents", "summary"):
        raise HTTPException(status_code=400, detail="Неизвестный тип выгрузки")
    _check_view(db, user, gid)
    d = _completeness(db, gid, period)
    if d is None:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    gen = datetime.now().strftime("%d.%m.%Y %H:%M") + " · " + d["period_title"]
    name = d["group"]["name"].replace(" ", "_")
    if kind == "summary":
        add_log(db, user.full_name, "Выгрузил сводную ведомость группы (Excel)", group=d["group"]["name"], ip=client_ip(request))
        db.commit()
        return xlsx_response(curator.summary_xlsx(curator.summary(d), gen), f"Сводная_{name}_{datetime.now():%Y-%m-%d}.xlsx")
    if kind == "completeness":
        add_log(db, user.full_name, "Выгрузил контроль заполнения итоговых (Excel)", group=d["group"]["name"], ip=client_ip(request))
        db.commit()
        return xlsx_response(curator.completeness_xlsx(d, gen), f"Контроль_заполнения_{name}_{datetime.now():%Y-%m-%d}.xlsx")
    add_log(db, user.full_name, "Выгрузил итоги для родителей (ZIP, файл на каждого студента)", group=d["group"]["name"], ip=client_ip(request))
    db.commit()
    fname = quote(f"Итоги_для_родителей_{name}_{datetime.now():%Y-%m-%d}.zip")
    return Response(content=curator.parents_zip(d, gen), media_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename=\"parents.zip\"; filename*=UTF-8''{fname}"})


@router.get("/api/admin/reconcile")
def admin_reconcile(db: Session = Depends(get_db), _: models.User = Depends(auth.check_admin)):
    return curator.reconcile(_q(db))


@router.get("/api/export/reconcile")
def export_reconcile(request: Request, db: Session = Depends(get_db), admin: models.User = Depends(auth.check_admin)):
    d = curator.reconcile(_q(db))
    add_log(db, admin.full_name, "Выгрузил сверку контингента (Excel)", ip=client_ip(request))
    db.commit()
    return xlsx_response(curator.reconcile_xlsx(d, datetime.now().strftime("%d.%m.%Y %H:%M")), f"Сверка_контингента_{datetime.now():%Y-%m-%d}.xlsx")


@router.get("/api/export/completeness")
def export_completeness_all(request: Request, period: Optional[str] = None, db: Session = Depends(get_db),
                            admin: models.User = Depends(auth.check_analyst)):
    """По всему колледжу (у заведующего — по отделению): у каких преподавателей не заполнены итоговые."""
    per = periods.resolve(period)
    dept = auth.dept_scope(admin)
    items = curator.completeness_all(periods.runner(db, per, dept))
    add_log(db, admin.full_name, "Выгрузил контроль заполнения по " + ("колледжу" if dept is None else "отделению") + " (Excel)",
            ip=client_ip(request))
    db.commit()
    return xlsx_response(curator.all_completeness_xlsx(items, datetime.now().strftime("%d.%m.%Y %H:%M") + " · " + periods.title(per)),
                         f"Контроль_заполнения_колледж_{datetime.now():%Y-%m-%d}.xlsx")
