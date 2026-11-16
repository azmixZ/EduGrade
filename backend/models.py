# backend/models.py
from sqlalchemy import Column, Integer, String, Boolean, Date, ForeignKey, Index
from sqlalchemy.orm import relationship
from database import Base


class Department(Base):
    __tablename__ = "departments"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)

    specialties = relationship("Specialty", back_populates="department", cascade="all, delete-orphan")


class Specialty(Base):
    __tablename__ = "specialties"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, nullable=False)  # ПР, ИБ, ЗУ, ФК и т.д.
    name = Column(String, nullable=False)  # Полное название
    department_id = Column(Integer, ForeignKey("departments.id"))

    department = relationship("Department", back_populates="specialties")
    groups = relationship("Group", back_populates="specialty", cascade="all, delete-orphan")


class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    specialty_id = Column(Integer, ForeignKey("specialties.id"))
    curator_id = Column(Integer, ForeignKey("users.id"), nullable=True)   # куратор группы (аккаунт преподавателя)
    contingent = Column(Integer, nullable=True)                           # число студентов по официальному списку колледжа
    graduating = Column(Boolean, nullable=False, default=False, server_default="0")  # выпускная: итоги семестра = итоговая аттестация

    specialty = relationship("Specialty", back_populates="groups")
    students = relationship("Student", back_populates="group", cascade="all, delete-orphan")
    workloads = relationship("Workload", back_populates="group")


class Student(Base):
    __tablename__ = "students"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    group_id = Column(Integer, ForeignKey("groups.id"), index=True)

    group = relationship("Group", back_populates="students")
    grades = relationship("Grade", back_populates="student", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    full_name = Column(String)
    role = Column(String(7))  # 'admin' или 'teacher'
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")             # False = вход запрещён
    token_version = Column(Integer, nullable=False, default=0, server_default="0")            # +1 = все старые токены недействительны
    last_login = Column(String)
    must_change_password = Column(Boolean, nullable=False, default=False, server_default="0")
    head_department_id = Column(Integer, ForeignKey("departments.id"), nullable=True)   # заведующий этим отделением (видит его аналитику)
    seen_news = Column(String)        # версия окна «Что нового», которую пользователь уже видел   # True = сменить пароль при входе

    workloads = relationship("Workload", back_populates="teacher")


class Subject(Base):
    __tablename__ = "subjects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    kind = Column(String, default="subject", server_default="subject")   # 'subject' = дисциплина, 'practice' = практика

    workloads = relationship("Workload", back_populates="subject")


# === СВЯЗУЮЩАЯ ТАБЛИЦА (УЧЕБНАЯ НАГРУЗКА): преподаватель + предмет + группа ===
class Workload(Base):
    __tablename__ = "workloads"
    id = Column(Integer, primary_key=True, index=True)
    teacher_id = Column(Integer, ForeignKey("users.id"), index=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"))
    group_id = Column(Integer, ForeignKey("groups.id"), index=True)

    teacher = relationship("User", back_populates="workloads")
    subject = relationship("Subject", back_populates="workloads")
    group = relationship("Group", back_populates="workloads")
    lessons = relationship("Lesson", back_populates="workload", cascade="all, delete-orphan")


class Lesson(Base):
    __tablename__ = "lessons"
    id = Column(Integer, primary_key=True, index=True)
    workload_id = Column(Integer, ForeignKey("workloads.id"), index=True)
    date_str = Column(String)
    date = Column(Date)                           # настоящая дата пары (для динамики и сортировки); у экзамена может быть пустой
    period = Column(String, default="oct", index=True)   # период аттестации: oct / s1 / mar / s2 (см. periods.py)
    lesson_type = Column(String, default="final")  # 'final' = итоговая колонка (в системе только она); 'pair'/'exam' — старые данные до convert_to_finals.py

    workload = relationship("Workload", back_populates="lessons")
    grades = relationship("Grade", back_populates="lesson", cascade="all, delete-orphan")


class Grade(Base):
    __tablename__ = "grades"
    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    lesson_id = Column(Integer, ForeignKey("lessons.id"), index=True)
    value = Column(String)

    student = relationship("Student", back_populates="grades")
    lesson = relationship("Lesson", back_populates="grades")

    # одна оценка на пару (студент, урок)
    __table_args__ = (
        Index("ux_grade_student_lesson", "student_id", "lesson_id", unique=True),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    time = Column(String)
    teacher = Column(String)
    action = Column(String)
    group_name = Column(String)
    student_name = Column(String)
    subject_name = Column(String)
    month = Column(String)
    ip = Column(String)
