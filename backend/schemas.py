# backend/schemas.py
from pydantic import BaseModel
from typing import List

class GradeBase(BaseModel):
    student_id: int
    lesson_id: int
    value: str

class LessonBase(BaseModel):
    id: int
    date_str: str

class StudentBase(BaseModel):
    id: int
    full_name: str

# Это пакет данных для отрисовки всей таблицы
class JournalData(BaseModel):
    group_id: int
    group_name: str
    students: List[StudentBase]
    lessons: List[LessonBase]
    grades: List[GradeBase]