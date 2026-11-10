# backend/main.py
import logging
import math
import os
import re
from collections import defaultdict
from datetime import datetime
from urllib.parse import quote
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

import auth
import models
import reports
import risk_api
import users_api
from common import TRUST_PROXY, XLSX_MIME, add_log, client_ip, now_str, xlsx_response
import security as sec
from grading import GRADE_ORDER, bucket, calc_metrics, normalize_grade, points
from database import get_db
from migrate import init_db

logger = logging.getLogger("kkci")

# ==========================================
# НАСТРОЙКИ БЕЗОПАСНОСТИ (через переменные окружения)
# ==========================================
DEBUG = os.getenv("KKCI_DEBUG", "0") == "1"                  # 1 = открыть /docs (только для разработки!)
RATE_LIMIT = int(os.getenv("KKCI_RATE_LIMIT", "600"))        # запросов в минуту с одного IP
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("KKCI_ALLOWED_ORIGINS", "*").split(",") if o.strip()]
MAX_BODY = 1_000_000                                         # байт на один запрос
MAX_GRADES_BATCH = 3000
FINAL_LABEL = "ИТОГ"                  # единственная колонка ведомости: итоговая оценка за предмет

api_limiter = sec.SlidingWindowLimiter(RATE_LIMIT, 60)
login_guard = sec.LoginGuard()
pw_limiter = sec.SlidingWindowLimiter(5, 900)                # попыток ввода «старого пароля» за 15 минут

if ALLOWED_ORIGINS == ["*"] and not DEBUG:
    logger.warning("KKCI_ALLOWED_ORIGINS не задан: CORS открыт для всех сайтов. Для рабочего сервера укажите свой домен.")

init_db()   # таблицы + новые колонки (см. migrate.py)

app = FastAPI(
    title="EduGrade API",
    docs_url="/docs" if DEBUG else None,        # в рабочем режиме схема API скрыта
    redoc_url=None,
    openapi_url="/openapi.json" if DEBUG else None,
)


# ==========================================
# ЗАЩИТНЫЙ СЛОЙ: лимиты, размер запроса, заголовки
# (добавляется ДО CORS, чтобы CORS оборачивал и ответы об ошибках)
# ==========================================
@app.middleware("http")
async def protect(request: Request, call_next):
    if request.method != "OPTIONS":
        ip = sec.client_ip(request, TRUST_PROXY)
        if not api_limiter.allow(ip):
            return JSONResponse({"detail": "Слишком много запросов. Подождите минуту."},
                                status_code=429, headers={"Retry-After": "60"})
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > MAX_BODY:
            return JSONResponse({"detail": "Слишком большой запрос"}, status_code=413)
        if request.method in ("POST", "PUT", "PATCH") and not cl and "chunked" in request.headers.get("transfer-encoding", "").lower():
            return JSONResponse({"detail": "Требуется Content-Length"}, status_code=411)

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    if not DEBUG:
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response


# Авторизация идёт через заголовок Authorization (не cookies), поэтому CSRF неприменим, а credentials не нужны.
# Для продакшена задайте KKCI_ALLOWED_ORIGINS="https://ваш-домен".
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["Content-Disposition"],      # чтобы браузер видел имя скачиваемого файла
)


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    logger.exception("Необработанная ошибка: %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Внутренняя ошибка сервера"}, status_code=500)


@app.exception_handler(RequestValidationError)
async def validation_exception(request: Request, exc: RequestValidationError):
    return JSONResponse({"detail": "Некорректные данные запроса"}, status_code=422)


# ==========================================
# ВСПОМОГАТЕЛЬНОЕ
# ==========================================
def final_lesson(db: Session, workload: models.Workload) -> models.Lesson:
    """Итоговая колонка ведомости. Система хранит только итоговые оценки: на одну нагрузку — ровно одна колонка.
    Если её ещё нет (новая ведомость), она создаётся автоматически."""
    lesson = (db.query(models.Lesson).filter(models.Lesson.workload_id == workload.id,
                                            models.Lesson.lesson_type == "final").order_by(models.Lesson.id).first())
    if lesson is None:
        lesson = models.Lesson(workload_id=workload.id, date_str=FINAL_LABEL, lesson_type="final")
        db.add(lesson)
        db.commit()
        db.refresh(lesson)
    return lesson


def find_workload(db: Session, user: models.User, group_id: int,
                  subject_id: Optional[int] = None) -> Optional[models.Workload]:
    q = db.query(models.Workload).filter(models.Workload.group_id == group_id)
    if user.role != "admin":
        q = q.filter(models.Workload.teacher_id == user.id)
    if subject_id:
        q = q.filter(models.Workload.subject_id == subject_id)
    return q.order_by(models.Workload.id).first()


def check_workload_access(user: models.User, workload: models.Workload):
    if user.role != "admin" and workload.teacher_id != user.id:
        raise HTTPException(status_code=403, detail="Эта ведомость вам не назначена")


def user_out(u: models.User) -> dict:
    return {
        "id": u.id, "username": u.username, "full_name": u.full_name, "role": u.role,
        "is_active": bool(u.is_active), "last_login": u.last_login or "",
        "must_change_password": bool(u.must_change_password),
    }


# ==========================================
# СХЕМЫ (Pydantic)
# ==========================================
class StudentOut(BaseModel):
    id: int
    full_name: str


class LessonOut(BaseModel):
    id: int
    date_str: str
    lesson_type: str = "final"


class GradeOut(BaseModel):
    student_id: int
    lesson_id: int
    value: str


class JournalData(BaseModel):
    group_id: int
    group_name: str
    workload_id: Optional[int] = None
    subject_id: Optional[int] = None
    subject_name: Optional[str] = None
    students: List[StudentOut]
    lessons: List[LessonOut]
    grades: List[GradeOut]


class GradeItem(BaseModel):
    student_id: int
    lesson_id: int
    value: str


class GradesBatch(BaseModel):
    grades: List[GradeItem]


class NewUser(BaseModel):
    username: str
    password: str = ""        # пусто = сгенерировать временный пароль
    full_name: str
    role: str


class PasswordChange(BaseModel):
    old_password: str
    new_password: str


class ActiveToggle(BaseModel):
    active: bool


class PasswordSet(BaseModel):
    new_password: str = ""    # пусто = сгенерировать


# ==========================================
# 1. АВТОРИЗАЦИЯ
# ==========================================
@app.post("/login")
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    ip = client_ip(request)
    username = (form_data.username or "").strip()[:64]

    wait = login_guard.locked_for(username, ip)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Слишком много неудачных попыток входа. Повторите через {math.ceil(wait / 60)} мин.",
            headers={"Retry-After": str(wait)},
        )

    user = db.query(models.User).filter(models.User.username == username).first()
    # Хеш проверяется всегда (для несуществующего логина — заглушка), чтобы время ответа не выдавало логины
    password_ok = auth.verify_password(form_data.password or "", user.password_hash if user else auth.DUMMY_HASH)

    if not user or not password_ok or not user.is_active:
        if login_guard.fail(username, ip):
            safe_name = re.sub(r"[^\w.\-@]", "?", username)[:32]
            add_log(db, "Система", f"Блокировка входа после неудачных попыток: «{safe_name}»", ip=ip)
            db.commit()
        raise HTTPException(status_code=400, detail="Неверный логин или пароль")

    login_guard.success(username, ip)
    user.last_login = now_str()
    if user.role == "admin":
        add_log(db, user.full_name, "Вход администратора в систему", ip=ip)
    db.commit()

    return {
        "access_token": auth.create_access_token(user),
        "token_type": "bearer",
        "role": user.role,
        "full_name": user.full_name,
        "must_change_password": bool(user.must_change_password),
    }


@app.get("/api/me")
def me(user: models.User = Depends(auth.get_current_user)):
    return {"username": user.username, "full_name": user.full_name, "role": user.role,
            "must_change_password": bool(user.must_change_password)}


@app.post("/api/me/password")
def change_password(data: PasswordChange, request: Request, db: Session = Depends(get_db),
                    user: models.User = Depends(auth.get_current_user)):
    if not pw_limiter.allow(user.username):
        raise HTTPException(status_code=429, detail="Слишком много попыток. Повторите позже.")
    if not auth.verify_password(data.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Текущий пароль указан неверно")
    if data.new_password == data.old_password:
        raise HTTPException(status_code=400, detail="Новый пароль должен отличаться от текущего")
    err = sec.validate_new_password(data.new_password, user.username)
    if err:
        raise HTTPException(status_code=400, detail=err)

    user.password_hash = auth.get_password_hash(data.new_password)
    user.must_change_password = False
    user.token_version = (user.token_version or 0) + 1          # прежние токены становятся недействительными
    add_log(db, user.full_name, "Сменил(а) пароль", ip=client_ip(request))
    db.commit()
    return {"status": "ok", "access_token": auth.create_access_token(user)}


# ==========================================
# 2. ИТОГОВАЯ ВЕДОМОСТЬ
# ==========================================
@app.get("/api/workloads")
def my_workloads(db: Session = Depends(get_db), user: models.User = Depends(auth.get_current_user)):
    """Нагрузка текущего преподавателя (для админа — вся нагрузка)."""
    q = (db.query(models.Workload, models.Group, models.Subject)
         .join(models.Group, models.Workload.group_id == models.Group.id)
         .join(models.Subject, models.Workload.subject_id == models.Subject.id))
    if user.role != "admin":
        q = q.filter(models.Workload.teacher_id == user.id)
    rows = q.order_by(models.Group.name, models.Subject.name).all()
    return [{
        "workload_id": w.id,
        "group_id": g.id, "group_name": g.name,
        "subject_id": s.id, "subject_name": s.name,
    } for w, g, s in rows]


@app.get("/api/groups")
def get_groups(db: Session = Depends(get_db), user: models.User = Depends(auth.get_current_user)):
    q = db.query(models.Group)
    if user.role != "admin":
        q = (q.join(models.Workload, models.Workload.group_id == models.Group.id)
             .filter(models.Workload.teacher_id == user.id).distinct())
    return [{"id": g.id, "name": g.name} for g in q.order_by(models.Group.name).all()]


@app.get("/api/journal/{group_id}", response_model=JournalData)
def get_journal(group_id: int, subject_id: Optional[int] = None,
                db: Session = Depends(get_db), user: models.User = Depends(auth.get_current_user)):
    group = db.get(models.Group, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    workload = find_workload(db, user, group_id, subject_id)
    if not workload:
        raise HTTPException(status_code=403, detail="У вас нет нагрузки в этой группе")

    students = (db.query(models.Student)
                .filter(models.Student.group_id == group_id)
                .order_by(models.Student.full_name).all())
    lessons = [final_lesson(db, workload)]
    lesson_ids = [l.id for l in lessons]
    grades = (db.query(models.Grade).filter(models.Grade.lesson_id.in_(lesson_ids)).all()
              if lesson_ids else [])

    return {
        "group_id": group.id,
        "group_name": group.name,
        "workload_id": workload.id,
        "subject_id": workload.subject_id,
        "subject_name": workload.subject.name if workload.subject else None,
        "students": [{"id": s.id, "full_name": s.full_name} for s in students],
        "lessons": [{"id": l.id, "date_str": l.date_str or "", "lesson_type": l.lesson_type or "final"}
                    for l in lessons],
        "grades": [{"student_id": g.student_id, "lesson_id": g.lesson_id, "value": g.value or ""}
                   for g in grades],
    }


@app.post("/api/journal/grades")
def save_grades(batch: GradesBatch, request: Request, db: Session = Depends(get_db),
                user: models.User = Depends(auth.get_current_user)):
    """Пакетное сохранение оценок одним запросом и одним коммитом."""
    if not batch.grades:
        return {"saved": 0}
    if len(batch.grades) > MAX_GRADES_BATCH:
        raise HTTPException(status_code=413, detail="Слишком много оценок в одном запросе")
    ip = client_ip(request)

    items = []
    for it in batch.grades:
        v = normalize_grade(it.value)
        if v is None:
            raise HTTPException(status_code=400, detail="Недопустимая оценка. Допустимо: число от 0 до 100 или Н")
        items.append((it.student_id, it.lesson_id, v))

    lesson_ids = {i[1] for i in items}
    student_ids = {i[0] for i in items}
    lessons = {l.id: l for l in db.query(models.Lesson).filter(models.Lesson.id.in_(lesson_ids)).all()}
    students = {s.id: s for s in db.query(models.Student).filter(models.Student.id.in_(student_ids)).all()}
    existing = {(g.student_id, g.lesson_id): g for g in
                db.query(models.Grade).filter(models.Grade.lesson_id.in_(lesson_ids),
                                              models.Grade.student_id.in_(student_ids)).all()}

    checked_workloads = {}
    saved = 0
    for sid, lid, val in items:
        lesson = lessons.get(lid)
        student = students.get(sid)
        if not lesson or not student:
            raise HTTPException(status_code=404, detail="Студент или колонка не найдены")

        if lesson.lesson_type != "final":
            raise HTTPException(status_code=400, detail="Система принимает только итоговые оценки")

        wl = checked_workloads.get(lesson.workload_id)
        if wl is None:
            wl = lesson.workload
            check_workload_access(user, wl)
            checked_workloads[lesson.workload_id] = wl
        if student.group_id != wl.group_id:
            raise HTTPException(status_code=400, detail="Студент не из группы этой ведомости")

        grade = existing.get((sid, lid))
        old = (grade.value if grade else "") or ""
        if old == val:
            continue

        if val == "":
            db.delete(grade)
            existing.pop((sid, lid), None)
            action = f"Удалил итоговую оценку (было {old})"
        elif grade:
            grade.value = val
            action = f"Изменил итоговую оценку {old} → {val}"
        else:
            new_grade = models.Grade(student_id=sid, lesson_id=lid, value=val)
            db.add(new_grade)
            existing[(sid, lid)] = new_grade
            action = f"Выставил итоговую оценку {val}"

        add_log(db, user.full_name, action,
                group=wl.group.name, student=student.full_name,
                subject=wl.subject.name if wl.subject else None,
                ip=ip)
        saved += 1

    db.commit()
    return {"saved": saved}


# ==========================================
# 3. АДМИНКА: СТАТИСТИКА, АНАЛИТИКА, ПОЛЬЗОВАТЕЛИ
# ==========================================
@app.get("/api/admin/stats")
def get_admin_stats(db: Session = Depends(get_db), _: models.User = Depends(auth.check_admin)):
    students_count = db.query(func.count(models.Student.id)).scalar()
    grades_count = db.query(func.count(models.Grade.id)).scalar()
    groups_count = db.query(func.count(models.Group.id)).scalar()

    chart = {k: 0 for k in GRADE_ORDER}
    for value, cnt in db.query(models.Grade.value, func.count()).group_by(models.Grade.value).all():
        b = bucket(value)
        if b:
            chart[b] += cnt

    logs_db = db.query(models.AuditLog).order_by(models.AuditLog.id.desc()).limit(100).all()
    logs = [{
        "time": l.time, "teacher": l.teacher, "action": l.action,
        "group": l.group_name or "—", "student": l.student_name or "—",
        "subject": l.subject_name or "—", "month": l.month or "—", "ip": l.ip or "—",
    } for l in logs_db]

    return {
        "counts": {"students": students_count, "grades": grades_count, "groups": groups_count},
        "chart": chart,
        "logs": logs,
    }


@app.get("/api/admin/analytics")
def get_analytics(db: Session = Depends(get_db), _: models.User = Depends(auth.check_admin)):
    """Реальные данные для графиков: по отделениям и по группам."""
    groups = (db.query(models.Group.id, models.Group.name, models.Department.name)
              .outerjoin(models.Specialty, models.Group.specialty_id == models.Specialty.id)
              .outerjoin(models.Department, models.Specialty.department_id == models.Department.id)
              .order_by(models.Group.name).all())

    students_per_group = dict(db.query(models.Student.group_id, func.count(models.Student.id))
                              .group_by(models.Student.group_id).all())

    rows = (db.query(models.Student.group_id, models.Grade.value, func.count(models.Grade.id))
            .join(models.Grade, models.Grade.student_id == models.Student.id)
            .group_by(models.Student.group_id, models.Grade.value).all())

    group_dist = defaultdict(lambda: {k: 0 for k in GRADE_ORDER})
    group_pts = defaultdict(int)
    for gid, value, cnt in rows:
        b = bucket(value)
        if b:
            group_dist[gid][b] += cnt
            group_pts[gid] += (points(value) or 0) * cnt

    dept_dist = defaultdict(lambda: {k: 0 for k in GRADE_ORDER})
    dept_pts = defaultdict(int)
    groups_out = []
    for gid, gname, dname in groups:
        dist = group_dist[gid]
        dname = dname or "Без отделения"
        for k in GRADE_ORDER:
            dept_dist[dname][k] += dist[k]
        dept_pts[dname] += group_pts[gid]
        m = calc_metrics(dist, group_pts[gid])
        groups_out.append({
            "id": gid, "name": gname, "department": dname,
            "students": students_per_group.get(gid, 0),
            "uspev": m["uspev"], "avg": m["avg"],
            "dist": [dist[k] for k in GRADE_ORDER],  # порядок: Н, <50, 3, 4, 5
        })

    departments = [{"name": n, **calc_metrics(d, dept_pts[n]), "dist": [d[k] for k in GRADE_ORDER]}
                   for n, d in dept_dist.items()]

    return {"labels": GRADE_ORDER, "departments": departments, "groups": groups_out}


@app.get("/api/admin/logs")
def admin_logs(q: str = "", page: int = 1, size: int = 50, db: Session = Depends(get_db),
               _: models.User = Depends(auth.check_admin)):
    """Журнал действий с поиском (ФИО, группа, предмет, преподаватель, действие) и страницами."""
    size = max(10, min(size, 200))
    page = max(1, page)
    query = db.query(models.AuditLog)
    q = (q or "").strip()[:64]
    if q:
        # SQLite сравнивает без учёта регистра только латиницу, поэтому пробуем несколько вариантов написания
        variants = {q, q.lower(), q.upper(), q.capitalize(), q.title()}
        cols = (models.AuditLog.teacher, models.AuditLog.action, models.AuditLog.group_name,
                models.AuditLog.student_name, models.AuditLog.subject_name, models.AuditLog.month,
                models.AuditLog.time)
        query = query.filter(or_(*[c.like(f"%{v}%") for c in cols for v in variants]))
    total = query.count()
    items = query.order_by(models.AuditLog.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"total": total, "page": page, "size": size, "items": [{
        "time": l.time, "teacher": l.teacher, "action": l.action,
        "group": l.group_name or "—", "student": l.student_name or "—",
        "subject": l.subject_name or "—", "month": l.month or "—", "ip": l.ip or "—",
    } for l in items]}


@app.get("/api/admin/users")
def get_users(db: Session = Depends(get_db), _: models.User = Depends(auth.check_admin)):
    users = db.query(models.User).order_by(models.User.id).all()
    return [user_out(u) for u in users]


@app.post("/api/admin/users")
def create_user(user_data: NewUser, request: Request, db: Session = Depends(get_db),
                admin: models.User = Depends(auth.check_admin)):
    username = (user_data.username or "").strip()
    full_name = (user_data.full_name or "").strip()
    if not sec.valid_username(username):
        raise HTTPException(status_code=400, detail="Логин: 3–32 символа, только латиница, цифры и . _ -")
    if not sec.valid_full_name(full_name):
        raise HTTPException(status_code=400, detail="ФИО: 3–100 символов, без < > и кавычек")
    if user_data.role not in ("admin", "teacher"):
        raise HTTPException(status_code=400, detail="Роль должна быть 'admin' или 'teacher'")
    if db.query(models.User).filter(models.User.username == username).first():
        raise HTTPException(status_code=400, detail="Пользователь с таким логином уже существует")

    generated = not user_data.password
    password = sec.generate_temp_password() if generated else user_data.password
    if not generated:
        err = sec.validate_password(password, username, full_name)
        if err:
            raise HTTPException(status_code=400, detail=err)

    db.add(models.User(
        username=username,
        password_hash=auth.get_password_hash(password),
        full_name=full_name,
        role=user_data.role,
        must_change_password=True,          # пароль знает администратор — пользователь обязан сменить его при входе
    ))
    add_log(db, admin.full_name, f"Создал учётную запись «{username}» ({user_data.role})", ip=client_ip(request))
    db.commit()
    return {"status": "ok", "temp_password": password if generated else None}


@app.post("/api/admin/users/{user_id}/reset-password")
def reset_password(user_id: int, request: Request, data: Optional[PasswordSet] = None,
                   db: Session = Depends(get_db), admin: models.User = Depends(auth.check_admin)):
    """Админ задаёт пользователю новый пароль вручную (new_password) или получает сгенерированный."""
    target = db.get(models.User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="Свой пароль меняется через «Сменить пароль»")

    new_password = (data.new_password if data else "") or ""
    if new_password:
        err = sec.validate_password(new_password, target.username, target.full_name)
        if err:
            raise HTTPException(status_code=400, detail=err)
    else:
        new_password = sec.generate_temp_password()

    target.password_hash = auth.get_password_hash(new_password)
    target.must_change_password = True
    target.token_version = (target.token_version or 0) + 1      # старые сессии пользователя завершаются
    add_log(db, admin.full_name, f"Изменил пароль пользователю «{target.username}»", ip=client_ip(request))
    db.commit()
    return {"status": "ok", "password": new_password}


@app.post("/api/admin/users/{user_id}/active")
def set_user_active(user_id: int, data: ActiveToggle, request: Request, db: Session = Depends(get_db),
                    admin: models.User = Depends(auth.check_admin)):
    target = db.get(models.User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="Нельзя отключить собственную учётную запись")

    target.is_active = bool(data.active)
    if not data.active:
        target.token_version = (target.token_version or 0) + 1  # мгновенно завершает открытые сессии
    add_log(db, admin.full_name,
            f"{'Включил' if data.active else 'Отключил'} учётную запись «{target.username}»", ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


# ==========================================
# 4. ВЫГРУЗКИ В EXCEL
# ==========================================
def sql_runner(db: Session):
    """Обёртка для reports.py: q(sql, params) -> список строк."""
    return lambda sql, params=None: db.execute(text(sql), params or {}).fetchall()


def file_part(s: str, limit: int = 40) -> str:
    return re.sub(r"[\\/:*?\"<>|\s]+", "_", s or "").strip("_")[:limit] or "x"


@app.get("/api/export/journal/{workload_id}")
def export_journal(workload_id: int, db: Session = Depends(get_db),
                   user: models.User = Depends(auth.get_current_user)):
    """Ведомость одного предмета (преподаватель — только свою, админ — любую)."""
    wl = db.get(models.Workload, workload_id)
    if not wl:
        raise HTTPException(status_code=404, detail="Ведомость не найдена")
    check_workload_access(user, wl)
    try:
        data, gname, subject = reports.journal_report(sql_runner(db), workload_id)
    except reports.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return xlsx_response(data, f"Ведомость_{file_part(gname)}_{file_part(subject)}_{datetime.now():%Y-%m-%d}.xlsx")


@app.get("/api/export/group/{group_id}")
def export_group(group_id: int, request: Request, db: Session = Depends(get_db),
                 admin: models.User = Depends(auth.check_admin)):
    """Полный отчёт по группе: сводка, предметы, ведомости по каждому предмету."""
    try:
        data, gname = reports.group_report(sql_runner(db), group_id)
    except reports.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    add_log(db, admin.full_name, "Выгрузил отчёт по группе (Excel)", group=gname, ip=client_ip(request))
    db.commit()
    return xlsx_response(data, f"Отчет_{file_part(gname)}_{datetime.now():%Y-%m-%d}.xlsx")


@app.get("/api/export/compare")
def export_compare(request: Request, groups: str = "", db: Session = Depends(get_db),
                   admin: models.User = Depends(auth.check_admin)):
    """Сравнение групп. ?groups=1,2,3 — выбранные группы; без параметра — все."""
    ids = []
    if groups.strip():
        ids = [int(x) for x in groups.split(",") if re.fullmatch(r"[0-9]{1,9}", x.strip())][:100]
        if not ids:
            raise HTTPException(status_code=400, detail="Не указаны группы для сравнения")
    try:
        data, n = reports.compare_report(sql_runner(db), ids or None)
    except reports.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    add_log(db, admin.full_name, f"Выгрузил сравнение групп (Excel): {n} гр.", ip=client_ip(request))
    db.commit()
    return xlsx_response(data, f"Сравнение_групп_{datetime.now():%Y-%m-%d}.xlsx")


# ==========================================
# 5. МОДУЛЬ «ГРУППА РИСКА» (слабые группы, слабые студенты, мёртвые души) — см. risk.py / risk_api.py
# ==========================================
app.include_router(risk_api.router)
app.include_router(users_api.router)
