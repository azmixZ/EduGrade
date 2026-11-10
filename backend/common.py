# backend/common.py
"""Общие помощники для main.py и модулей-роутеров (журнал действий, IP клиента, ответ с Excel)."""
import os
from datetime import datetime
from io import BytesIO
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import models
import security as sec

TRUST_PROXY = os.getenv("KKCI_TRUST_PROXY", "0") == "1"      # 1 = только если сервер стоит за вашим nginx
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def now_str() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M")


def client_ip(request: Request) -> str:
    return sec.client_ip(request, TRUST_PROXY)


def add_log(db: Session, actor: str, action: str, group: str = None, student: str = None,
            subject: str = None, month: str = None, ip: str = None):
    db.add(models.AuditLog(
        time=now_str(), teacher=actor, action=action,
        group_name=group, student_name=student, subject_name=subject, month=month, ip=ip,
    ))


def xlsx_response(data: bytes, filename: str) -> Response:
    return Response(content=data, media_type=XLSX_MIME, headers={
        "Content-Disposition": f"attachment; filename=\"report.xlsx\"; filename*=UTF-8''{quote(filename)}"})


def simple_xlsx(title: str, headers: list, rows: list, widths: list, notes: list = None) -> bytes:
    """Одна таблица: заголовок, (необязательные) пояснения, шапка, строки. Печать — альбомная, шапка закреплена."""
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    thin = Side(style="thin", color="CBD5E1")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws.cell(row=1, column=1, value=title).font = Font(bold=True, size=14, color="0F172A")
    r = 2
    for n in (notes or []):
        ws.cell(row=r, column=1, value=n).font = Font(italic=True, color="64748B")
        r += 1
    hdr = r + 1
    for i, h in enumerate(headers, 1):
        cell = ws.cell(row=hdr, column=i, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0F172A")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border
    for ri, row in enumerate(rows, hdr + 1):
        for ci, v in enumerate(row, 1):
            cell = ws.cell(row=ri, column=ci, value=v)
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(row=hdr + 1, column=1)
    if rows:
        ws.auto_filter.ref = f"A{hdr}:{get_column_letter(len(headers))}{hdr + len(rows)}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
