# backend/risk_api.py
"""API модуля «Группа риска». Подключается в main.py одной строкой: app.include_router(risk_api.router)."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

import auth
import models
import pullup
import risk
from common import add_log, client_ip, xlsx_response
from database import get_db

router = APIRouter()

KINDS = {"all": "полный отчёт", "groups": "слабые группы", "students": "слабые студенты", "dead": "мёртвые души"}


def _runner(db: Session):
    return lambda sql, params=None: db.execute(text(sql), params or {}).fetchall()


@router.get("/api/admin/risk")
def get_risk(
    weak_avg: Optional[float] = None, fail_subjects: Optional[int] = None, abs_warn: Optional[float] = None,
    dead_abs: Optional[float] = None, dead_cov: Optional[float] = None,
    group_avg: Optional[float] = None, group_unatt: Optional[float] = None, group_weak: Optional[float] = None,
    db: Session = Depends(get_db), _: models.User = Depends(auth.check_admin),
):
    """Слабые группы, слабые студенты, «мёртвые души». Пороги необязательны (см. risk.Params)."""
    params = risk.Params.from_query(
        weak_avg=weak_avg, fail_subjects=fail_subjects, abs_warn=abs_warn, dead_abs=dead_abs,
        dead_cov=dead_cov, group_avg=group_avg, group_unatt=group_unatt, group_weak=group_weak)
    return risk.compute(_runner(db), params)


@router.get("/api/export/risk")
def export_risk(
    request: Request, kind: str = "all",
    weak_avg: Optional[float] = None, fail_subjects: Optional[int] = None, abs_warn: Optional[float] = None,
    dead_abs: Optional[float] = None, dead_cov: Optional[float] = None,
    group_avg: Optional[float] = None, group_unatt: Optional[float] = None, group_weak: Optional[float] = None,
    db: Session = Depends(get_db), admin: models.User = Depends(auth.check_admin),
):
    """Excel: kind = all | groups | students | dead. Пороги те же, что на экране."""
    if kind not in KINDS:
        raise HTTPException(status_code=400, detail="Неизвестный тип выгрузки")
    params = risk.Params.from_query(
        weak_avg=weak_avg, fail_subjects=fail_subjects, abs_warn=abs_warn, dead_abs=dead_abs,
        dead_cov=dead_cov, group_avg=group_avg, group_unatt=group_unatt, group_weak=group_weak)
    data = risk.compute(_runner(db), params)
    xlsx = risk.build_xlsx(data, kind, datetime.now().strftime("%d.%m.%Y %H:%M"))

    add_log(db, admin.full_name, f"Выгрузил отчёт «Группа риска» ({KINDS[kind]}, Excel)", ip=client_ip(request))
    db.commit()

    names = {"all": "Группа_риска", "groups": "Слабые_группы", "students": "Слабые_студенты", "dead": "Мертвые_души"}
    return xlsx_response(xlsx, f"{names[kind]}_{datetime.now():%Y-%m-%d}.xlsx")


# ==========================================================
# «Можно подтянуть»: 1–2 итоговые тройки, остальные 4–5
# ==========================================================
@router.get("/api/admin/pullup")
def get_pullup(max_threes: Optional[int] = None, min_subjects: Optional[int] = None, easy_gap: Optional[int] = None,
               db: Session = Depends(get_db), _: models.User = Depends(auth.check_admin)):
    return pullup.compute(_runner(db), pullup.Params.from_query(
        max_threes=max_threes, min_subjects=min_subjects, easy_gap=easy_gap))


@router.get("/api/export/pullup")
def export_pullup(request: Request, max_threes: Optional[int] = None, min_subjects: Optional[int] = None,
                  easy_gap: Optional[int] = None, db: Session = Depends(get_db),
                  admin: models.User = Depends(auth.check_admin)):
    data = pullup.compute(_runner(db), pullup.Params.from_query(
        max_threes=max_threes, min_subjects=min_subjects, easy_gap=easy_gap))
    xlsx = pullup.build_xlsx(data, datetime.now().strftime("%d.%m.%Y %H:%M"))
    add_log(db, admin.full_name, "Выгрузил отчёт «Можно подтянуть» (Excel)", ip=client_ip(request))
    db.commit()
    return xlsx_response(xlsx, f"Можно_подтянуть_{datetime.now():%Y-%m-%d}.xlsx")
