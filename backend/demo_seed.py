# backend/demo_seed.py
"""
Демо-колледж с вымышленными людьми, чтобы попробовать систему без настоящих списков.

    python demo_seed.py            # создать базу с демо-данными (если базы ещё нет)
    python demo_seed.py --fresh    # удалить kkci.db и собрать заново
    python demo_fill.py --yes      # потом проставить демо-оценки

Создаёт отделения, специальности, 12 групп по 18–25 студентов, 8 преподавателей
(логины teacher1…teacher8, пароль demo) и их нагрузку. ФИО генерируются случайно.
"""
import argparse
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

DEPARTMENTS = {
    "Информационные технологии": [("ПР", "Программное обеспечение"), ("ИБ", "Информационная безопасность")],
    "Экономика и право": [("УЧ", "Учёт и аудит"), ("ПВ", "Правоведение")],
    "Сервис": [("ТР", "Туризм"), ("ДЗ", "Дизайн")],
}
SUBJECTS = {
    "ПР": ["Основы программирования", "Базы данных", "Веб-разработка", "Математика", "Английский язык"],
    "ИБ": ["Сетевые технологии", "Криптография", "Операционные системы", "Математика", "Английский язык"],
    "УЧ": ["Бухгалтерский учёт", "Экономика организации", "Статистика", "Казахский язык", "Английский язык"],
    "ПВ": ["Теория государства и права", "Гражданское право", "Трудовое право", "Казахский язык", "История Казахстана"],
    "ТР": ["Основы туризма", "География туризма", "Английский язык", "Маркетинг", "История Казахстана"],
    "ДЗ": ["Композиция", "Цветоведение", "Компьютерная графика", "История искусств", "Казахский язык"],
}
SURNAMES = ["Абенов", "Ахметов", "Байжанов", "Габдуллин", "Даулетов", "Ерланов", "Жумабаев", "Иванов", "Кайратов",
            "Ким", "Ли", "Мусин", "Нурланов", "Омаров", "Петров", "Рахимов", "Сагинтаев", "Смирнов", "Тлеуов",
            "Уалиев", "Федоров", "Хамитов", "Шайкенов", "Ыбыраев", "Яковлев", "Серикбаев", "Касымов", "Попов"]
MALE = ["Алихан", "Арман", "Батыр", "Данияр", "Ерасыл", "Нурсултан", "Тимур", "Максим", "Артём", "Санжар", "Дамир"]
FEMALE = ["Айгерим", "Аружан", "Дана", "Жанель", "Камила", "Мадина", "Анна", "Алина", "Томирис", "Сабина", "Асель"]


def fake_name(rng):
    surname = rng.choice(SURNAMES)
    if rng.random() < 0.5:
        return f"{surname} {rng.choice(MALE)}"
    if surname.endswith(("ов", "ев", "ин")):
        surname += "а"
    return f"{surname} {rng.choice(FEMALE)}"


def main():
    ap = argparse.ArgumentParser(description="Демо-данные для EduGrade")
    ap.add_argument("--fresh", action="store_true", help="удалить kkci.db перед заполнением")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    if args.fresh:
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                os.remove("kkci.db" + suffix)
            except FileNotFoundError:
                pass

    from migrate import init_db
    from database import SessionLocal
    from auth import get_password_hash
    import models

    init_db()
    db = SessionLocal()
    try:
        if db.query(models.Group).count():
            print("В базе уже есть группы. Для чистой демо-базы: python demo_seed.py --fresh")
            return 1
        rng = random.Random(args.seed)

        if not db.query(models.User).filter_by(username="admin").first():
            db.add(models.User(username="admin", password_hash=get_password_hash("admin"),
                               full_name="Администратор", role="admin", must_change_password=True))

        demo_hash = get_password_hash("demo")
        teachers = []
        for i in range(1, 9):
            t = models.User(username=f"teacher{i}", password_hash=demo_hash,
                            full_name=fake_name(rng), role="teacher")
            db.add(t)
            teachers.append(t)

        subjects = {}
        for names in SUBJECTS.values():
            for n in names:
                if n not in subjects:
                    subjects[n] = models.Subject(name=n)
                    db.add(subjects[n])
        db.flush()

        groups = 0
        students = 0
        for dep_name, specs in DEPARTMENTS.items():
            dep = models.Department(name=dep_name)
            db.add(dep)
            for code, spec_name in specs:
                spec = models.Specialty(code=code, name=spec_name, department=dep)
                db.add(spec)
                for course, year in ((1, 25), (2, 24)):
                    g = models.Group(name=f"{code}-{course}-{year}", specialty=spec)
                    db.add(g)
                    groups += 1
                    for _ in range(rng.randint(18, 25)):
                        db.add(models.Student(full_name=fake_name(rng), group=g))
                        students += 1
                    db.flush()
                    for subj in SUBJECTS[code]:
                        db.add(models.Workload(teacher=rng.choice(teachers), subject=subjects[subj], group=g))
        db.commit()
        print(f"Готово: {groups} групп, {students} студентов, {len(teachers)} преподавателей, {len(subjects)} предметов.")
        print("Вход: admin / admin (попросит сменить пароль) или teacher1…teacher8 / demo.")
        print("Оценки: python demo_fill.py --yes --dead 3")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
