# backend/seed_workload.py
"""
Загрузка учебной нагрузки из рабочих учебных планов (Excel) в таблицу workloads.

Положите файлы планов (.xlsx / .xls) в папку  plans/  рядом с этим скриптом
(или укажите другую через --dir), затем:

    python seed_workload.py --dry-run      # только показать, что будет сделано
    python seed_workload.py --reset        # удалить старую (пустую) нагрузку и загрузить новую
    python seed_workload.py                # добавить нагрузку, ничего не удаляя

Скрипт НЕ создаёт группы и студентов (это делает seed.py). Он создаёт:
  - преподавателей (users), которых ещё нет; пароли пишутся в teachers_new.csv
  - предметы (subjects)
  - нагрузку (workloads): преподаватель + предмет + группа
Строки с «вакансия» пропускаются.
"""
import argparse
import csv
import glob
import os
import re
import secrets
import string
import sys
from difflib import SequenceMatcher

HERE = os.path.dirname(os.path.abspath(__file__))

# Если название группы в плане не совпало с названием в базе, добавьте соответствие сюда:
#   ключ  = norm_group(название в плане),  значение = norm_group(название в базе)
ALIASES = {
    "тпппо126": "тпппо26",   # план «ТПП-ПО-1-26»  ->  в базе «ТПП-ПО-26»
    "мт26": "мт126",         # план «МТ-26 б.9»    ->  в базе «МТ-1-26»
}

# ----------------------------------------------------------------------
# Нормализация названий
# ----------------------------------------------------------------------
LATIN = str.maketrans("ABCEHKMOPTXYabcehkmoptxy", "АВСЕНКМОРТХУавсенкмортху")


def norm_group(name):
    """Ключ для сопоставления: 'ПР-1- 23 б.9' и 'ПР-1-23' -> 'пр123'; 'гос' сохраняется."""
    s = str(name or "").translate(LATIN).lower()
    gos = "гос" in s
    s = re.sub(r"\(?\s*(база\s*)?\d+\s*кл\.?\s*\)?", " ", s)   # (11 кл), (база 9 кл)
    s = re.sub(r"б\s*\.\s*\d+", " ", s)                        # б.9, б.11
    s = s.replace("гос", " ")
    s = re.sub(r"[^0-9a-zа-яё]", "", s)
    return s + ("гос" if gos else "")


def norm_teacher(name):
    """'Арбагулов Е. А.' и 'Байсакалова А.В' -> 'Арбагулов Е.А.' / 'Байсакалова А.В.'"""
    s = re.sub(r"\s+", " ", str(name or "").strip())
    parts = s.replace(".", " ").split()
    rest = []
    for p in parts[1:]:
        rest.extend(list(p) if (p.isupper() and len(p) <= 3) else [p])   # 'РС' -> 'Р','С'
    if rest and all(len(p) == 1 for p in rest):
        return parts[0] + " " + "".join(p + "." for p in rest)
    return re.sub(r"\.\s+", ".", s)


def is_vacancy(name):
    s = str(name or "").strip().lower()
    return (not s) or ("вакан" in s)


def norm_subject(name):
    return re.sub(r"\s+", " ", str(name or "").strip())


TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ә": "a", "ғ": "g", "қ": "k", "ң": "n", "ө": "o", "ұ": "u", "ү": "u", "һ": "h", "і": "i",
}


def translit(s):
    out = "".join(TRANSLIT.get(ch, ch) for ch in str(s).lower())
    return re.sub(r"[^a-z]", "", out)


def make_username(full_name, taken):
    parts = norm_teacher(full_name).replace(".", " ").split()
    base = translit(parts[0]) if parts else "teacher"
    base = base or "teacher"
    candidates = [base]
    if len(parts) > 1:
        candidates.append(base + translit(parts[1])[:1])
    if len(parts) > 2:
        candidates.append(base + translit(parts[1])[:1] + translit(parts[2])[:1])
    for c in candidates:
        if c not in taken:
            return c
    n = 2
    while f"{base}{n}" in taken:
        n += 1
    return f"{base}{n}"


def gen_password(n=8):
    alphabet = string.ascii_lowercase.replace("l", "").replace("o", "") + string.digits.replace("0", "").replace("1", "")
    return "".join(secrets.choice(alphabet) for _ in range(n))


# ----------------------------------------------------------------------
# Чтение Excel
# ----------------------------------------------------------------------
class Sheet:
    def __init__(self, title, getter, max_row):
        self.title, self._get, self.max_row = title, getter, max_row

    def cell(self, r, c):
        return self._get(r, c)


def load_sheets(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        for ws in wb.worksheets:
            yield Sheet(ws.title, lambda r, c, ws=ws: ws.cell(row=r, column=c).value, ws.max_row)
    elif ext == ".xls":
        try:
            import xlrd
        except ImportError:
            raise RuntimeError(
                f"{os.path.basename(path)}: для .xls нужен пакет xlrd (pip install xlrd) "
                f"или пересохраните файл как .xlsx")
        wb = xlrd.open_workbook(path)
        for ws in wb.sheets():
            def get(r, c, ws=ws):
                if r - 1 < ws.nrows and c - 1 < ws.ncols:
                    v = ws.cell_value(r - 1, c - 1)
                    return v if v != "" else None
                return None
            yield Sheet(ws.name, get, ws.nrows)


def parse_sheet(sh):
    """Возвращает (название_группы, [(предмет, преподаватель), ...])."""
    hdr_row = subj_col = teach_col = None
    for r in range(1, 16):
        for c in range(1, 21):
            v = str(sh.cell(r, c) or "").strip().lower()
            if v.startswith("наименование"):
                hdr_row, subj_col = r, c
            if v.startswith("фио преподавател") or v == "преподаватель":
                teach_col = c
    if not hdr_row:
        return None, []
    teach_col = teach_col or 13  # колонка M

    group = None
    for r in range(hdr_row + 1, hdr_row + 8):
        v = sh.cell(r, 2)
        if v not in (None, ""):
            group = str(v).strip()
            break

    items, subject = [], None
    for r in range(hdr_row + 1, sh.max_row + 1):
        d = sh.cell(r, subj_col)
        t = sh.cell(r, teach_col)
        if d and str(d).strip().lower().startswith("итого"):
            break
        if d and str(d).strip():
            subject = norm_subject(d)       # пустая ячейка = тот же предмет (вторая подгруппа)
        if subject and t and str(t).strip():
            items.append((subject, str(t).strip()))
    return group, items


def load_plans(folder):
    files = sorted(glob.glob(os.path.join(folder, "*.xlsx")) + glob.glob(os.path.join(folder, "*.xls")))
    files = [f for f in files if not os.path.basename(f).startswith("~$")]
    plans = []
    for f in files:
        for sh in load_sheets(f):
            group, items = parse_sheet(sh)
            plans.append({"file": os.path.basename(f), "sheet": sh.title, "group": group, "items": items})
    return plans


# ----------------------------------------------------------------------
# План загрузки (чистая функция, без БД)
# ----------------------------------------------------------------------
def build_plan(plans, db_groups):
    """db_groups: {norm_key: имя группы в БД}."""
    assignments, missing, vacancies, skipped_sheets = set(), [], 0, []
    for p in plans:
        if not p["group"]:
            skipped_sheets.append(f'{p["file"]} / {p["sheet"]}')
            continue
        key = norm_group(p["group"])
        key = ALIASES.get(key, key)
        gname = db_groups.get(key)
        if not gname:
            missing.append(p["group"])
            continue
        for subject, teacher in p["items"]:
            if is_vacancy(teacher):
                vacancies += 1
                continue
            assignments.add((gname, subject, norm_teacher(teacher)))
    return {"assignments": sorted(assignments), "missing_groups": missing,
            "vacancies": vacancies, "skipped_sheets": skipped_sheets}


def similar_teachers(names):
    """Возможные опечатки: одинаковые инициалы, очень похожие фамилии."""
    def split(n):
        parts = n.replace(".", " ").split()
        return parts[0].lower(), "".join(x[:1] for x in parts[1:]).lower()
    names = sorted(set(names))
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            sa, ia = split(a)
            sb, ib = split(b)
            if ia == ib and sa != sb and SequenceMatcher(None, sa, sb).ratio() >= 0.75:
                out.append((a, b))
    return out


# ----------------------------------------------------------------------
# Запись в БД
# ----------------------------------------------------------------------
def run(args):
    sys.path.insert(0, HERE)
    import models
    from auth import get_password_hash
    from database import SessionLocal, engine

    from migrate import init_db
    init_db()

    folder = args.dir if os.path.isabs(args.dir) else os.path.join(HERE, args.dir)
    if not os.path.isdir(folder):
        print(f"❌ Папка с планами не найдена: {folder}")
        return 1
    try:
        plans = load_plans(folder)
    except RuntimeError as e:
        print("❌", e)
        return 1
    if not plans:
        print(f"❌ В папке {folder} нет файлов .xlsx/.xls")
        return 1

    db = SessionLocal()
    try:
        groups = {}
        for g in db.query(models.Group).all():
            k = norm_group(g.name)
            if k in groups:
                print(f"⚠️ Две группы в базе дают один ключ «{k}»: {groups[k]} и {g.name}")
            groups[k] = g.name
        group_by_name = {g.name: g for g in db.query(models.Group).all()}

        plan = build_plan(plans, groups)
        assignments = plan["assignments"]
        teacher_names = sorted({t for _, _, t in assignments})

        existing_users = db.query(models.User).all()
        by_name = {norm_teacher(u.full_name).lower(): u for u in existing_users if u.role == "teacher"}
        new_teachers = [t for t in teacher_names if t.lower() not in by_name]

        print(f"📚 Листов в планах: {len(plans)}; групп сопоставлено с базой: "
              f"{len(plans) - len(plan['missing_groups']) - len(plan['skipped_sheets'])}")
        print(f"📝 Назначений (преподаватель + предмет + группа): {len(assignments)}")
        print(f"👨‍🏫 Преподавателей: {len(teacher_names)} (новых: {len(new_teachers)})")
        print(f"🕳  Строк «вакансия» (пропущено): {plan['vacancies']}")
        if plan["missing_groups"]:
            print("\n⚠️ Группы из планов, которых НЕТ в базе (нагрузка по ним не загружена):")
            for g in plan["missing_groups"]:
                print("   -", g)
            print("   Добавьте этих студентов в seed.py или поправьте ALIASES вверху этого файла.")
        if plan["skipped_sheets"]:
            print("\n⚠️ Листы без таблицы предметов (пропущены):", *plan["skipped_sheets"], sep="\n   - ")
        sim = similar_teachers(teacher_names)
        if sim:
            print("\n⚠️ Возможные опечатки в ФИО (будут созданы как разные люди):")
            for a, b in sim:
                print(f"   - {a}  /  {b}")

        if args.dry_run:
            print("\nℹ️ Режим --dry-run: в базу ничего не записано.")
            return 0

        # --- сброс старой нагрузки (только пустой, без уроков) ---
        if args.reset:
            empty = db.query(models.Workload).filter(~models.Workload.lessons.any()).all()
            for w in empty:
                db.delete(w)
            db.commit()
            kept = db.query(models.Workload).count()
            print(f"\n🧹 Удалено пустых записей нагрузки: {len(empty)}; оставлено (есть уроки): {kept}")

        # --- преподаватели ---
        taken = {u.username for u in existing_users}
        creds = []
        for name in new_teachers:
            username = make_username(name, taken)
            taken.add(username)
            password = gen_password()
            u = models.User(username=username, password_hash=get_password_hash(password),
                            full_name=name, role="teacher", must_change_password=True)   # временный пароль из CSV: смена при первом входе
            db.add(u)
            by_name[name.lower()] = u
            creds.append((username, password, name))
        db.commit()
        for u in by_name.values():
            db.refresh(u)

        # --- предметы ---
        subjects = {s.name: s for s in db.query(models.Subject).all()}
        for _, subj, _ in assignments:
            if subj not in subjects:
                s = models.Subject(name=subj)
                db.add(s)
                subjects[subj] = s
        db.commit()
        for s in subjects.values():
            db.refresh(s)

        # --- нагрузка ---
        existing = {(w.teacher_id, w.subject_id, w.group_id) for w in db.query(models.Workload).all()}
        created = 0
        for gname, subj, tname in assignments:
            key = (by_name[tname.lower()].id, subjects[subj].id, group_by_name[gname].id)
            if key in existing:
                continue
            existing.add(key)
            db.add(models.Workload(teacher_id=key[0], subject_id=key[1], group_id=key[2]))
            created += 1
        db.commit()
        print(f"\n✅ Создано записей нагрузки: {created}")

        if creds:
            path = os.path.join(HERE, "teachers_new.csv")
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["Логин", "Пароль", "ФИО"])
                w.writerows(creds)
            print(f"🔑 Новых преподавателей: {len(creds)}. Логины и пароли: {path}")
            print("   Раздайте пароли и удалите этот файл.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Загрузка нагрузки из учебных планов")
    ap.add_argument("--dir", default="plans", help="папка с Excel-планами (по умолчанию ./plans)")
    ap.add_argument("--reset", action="store_true", help="удалить прежнюю нагрузку без уроков перед загрузкой")
    ap.add_argument("--dry-run", action="store_true", help="ничего не записывать, только показать отчёт")
    sys.exit(run(ap.parse_args()))
