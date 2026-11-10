# backend/security.py
"""
Модуль безопасности (только стандартная библиотека):
  - секретный ключ JWT (из переменной окружения или автоматически созданного файла)
  - политика паролей
  - ограничитель частоты запросов и защита входа от подбора
  - строгая проверка входных данных
"""
import os
import re
import secrets
import stat
import threading
import time
from collections import defaultdict, deque
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
KNOWN_BAD_SECRETS = {"kkci_super_secret_key_2026", "secret", "changeme", ""}


# ----------------------------------------------------------------------
# 1. СЕКРЕТНЫЙ КЛЮЧ
# ----------------------------------------------------------------------
def load_secret_key() -> str:
    """
    Порядок: переменная окружения KKCI_SECRET_KEY -> файл .secret_key рядом с кодом
    (создаётся автоматически, 64+ случайных символов, права только владельцу).
    Ключ, который был зашит в старом коде, отвергается.
    """
    env = os.getenv("KKCI_SECRET_KEY", "").strip()
    if env:
        if env in KNOWN_BAD_SECRETS or len(env) < 32:
            raise RuntimeError("KKCI_SECRET_KEY слишком короткий или известный (нужно ≥ 32 символов).")
        return env

    path = os.path.join(HERE, ".secret_key")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            key = f.read().strip()
        if len(key) >= 32 and key not in KNOWN_BAD_SECRETS:
            return key

    key = secrets.token_urlsafe(64)
    with open(path, "w", encoding="utf-8") as f:
        f.write(key)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)   # 600 (на Windows не действует — ограничьте доступ вручную)
    except OSError:
        pass
    print(f"🔐 Создан новый секретный ключ: {path} (не публикуйте и не коммитьте этот файл!)")
    return key


# ----------------------------------------------------------------------
# 2. ПАРОЛИ (требования минимальные)
# ----------------------------------------------------------------------
def validate_password(password: str, username: str = "", full_name: str = "") -> Optional[str]:
    """Минимальные требования (удобство важнее): 4–72 символа. Возвращает текст ошибки или None."""
    if not isinstance(password, str) or len(password) < 4:
        return "Пароль должен быть не короче 4 символов"
    if len(password.encode("utf-8")) > 72:
        return "Пароль слишком длинный (максимум 72 байта)"
    return None


WEAK_PASSWORDS = {"admin", "password", "12345678", "qwerty123", "11111111", "kkci", "kkci2026"}


def validate_new_password(password: str, username: str = "") -> Optional[str]:
    """Строже, чем validate_password: применяется, когда пользователь сам выбирает пароль (смена при входе)."""
    err = validate_password(password, username)
    if err:
        return err
    if len(password) < 8:
        return "Пароль должен быть не короче 8 символов"
    if password.lower() in WEAK_PASSWORDS or (username and password.lower() == username.lower()):
        return "Слишком простой пароль"
    if not (re.search(r"[A-Za-zА-Яа-яЁё]", password) and re.search(r"\d", password)):
        return "Пароль должен содержать буквы и цифры"
    return None


def generate_temp_password(n: int = 10) -> str:
    letters = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"   # без l, o, I, O
    digits = "23456789"
    while True:
        pw = "".join(secrets.choice(letters + digits) for _ in range(n))
        if re.search(r"[a-zA-Z]", pw) and re.search(r"\d", pw):
            return pw


# ----------------------------------------------------------------------
# 3. ОГРАНИЧЕНИЕ ЧАСТОТЫ
# ----------------------------------------------------------------------
class SlidingWindowLimiter:
    """Не более `limit` событий за `window` секунд на ключ (IP, логин и т.п.)."""

    def __init__(self, limit: int, window: float, max_keys: int = 50_000):
        self.limit, self.window, self.max_keys = limit, window, max_keys
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > self.max_keys:          # защита памяти от перебора ключей
                self._purge(now)
            q = self._hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def _purge(self, now: float):
        for k in [k for k, q in self._hits.items() if not q or q[-1] <= now - self.window]:
            del self._hits[k]


class LoginGuard:
    """
    Защита входа от подбора пароля. Три счётчика неудачных попыток за `window` секунд:
      - пара (логин + IP): `max_pair`  — главный, не позволяет заблокировать чужой аккаунт одним IP
      - логин с любых IP:   `max_user` — против распределённого подбора
      - IP по всем логинам: `max_ip`   — против «перебора паролей по списку логинов»
    Превышение блокирует вход на `lockout` секунд.
    """

    def __init__(self, max_pair=8, max_user=40, max_ip=150, window=600, lockout=300, ip_lockout=300):
        self.max_pair, self.max_user, self.max_ip = max_pair, max_user, max_ip
        self.window, self.lockout, self.ip_lockout = window, lockout, ip_lockout
        self._fails = defaultdict(deque)
        self._locked = {}
        self._lock = threading.Lock()

    @staticmethod
    def _keys(username, ip):
        u = (username or "").strip().lower()[:64]
        return (f"pair:{u}|{ip}", f"user:{u}", f"ip:{ip}")

    def locked_for(self, username, ip) -> int:
        """Сколько секунд ещё действует блокировка (0 — вход разрешён)."""
        now = time.monotonic()
        with self._lock:
            left = 0
            for k in self._keys(username, ip):
                until = self._locked.get(k)
                if until:
                    if until <= now:
                        del self._locked[k]
                    else:
                        left = max(left, int(until - now) + 1)
            return left

    def fail(self, username, ip) -> bool:
        """Записывает неудачу. True — эта неудача вызвала новую блокировку."""
        now = time.monotonic()
        newly = False
        pair, user, ipk = self._keys(username, ip)
        with self._lock:
            if len(self._fails) > 50_000:
                self._purge(now)
            for k, limit, lock_s in ((pair, self.max_pair, self.lockout),
                                     (user, self.max_user, self.lockout),
                                     (ipk, self.max_ip, self.ip_lockout)):
                q = self._fails[k]
                while q and q[0] <= now - self.window:
                    q.popleft()
                q.append(now)
                if len(q) >= limit and k not in self._locked:
                    self._locked[k] = now + lock_s
                    q.clear()
                    newly = True
        return newly

    def success(self, username, ip):
        pair, _, _ = self._keys(username, ip)
        with self._lock:
            self._fails.pop(pair, None)

    def _purge(self, now):
        for k in [k for k, q in self._fails.items() if not q or q[-1] <= now - self.window]:
            del self._fails[k]


def client_ip(request, trust_proxy: bool = False) -> str:
    """IP клиента. X-Forwarded-For учитывается ТОЛЬКО если сервер стоит за вашим прокси (nginx)."""
    if trust_proxy:
        xff = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1][:64]       # последний адрес добавил ваш прокси; подделать его нельзя
    return (request.client.host if request.client else "unknown")[:64]


# ----------------------------------------------------------------------
# 4. ПРОВЕРКА ВХОДНЫХ ДАННЫХ
# ----------------------------------------------------------------------
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
LESSON_DATE_RE = re.compile(r"^(0?[1-9]|[12]\d|3[01])\.(0?[1-9]|1[0-2]) [А-Яа-яЁё]{3,10}$")
EXAM_LABEL = "ЭКЗАМЕН"


def valid_username(s: str) -> bool:
    return bool(USERNAME_RE.fullmatch(s or ""))


def valid_full_name(s: str) -> bool:
    s = (s or "").strip()
    return 3 <= len(s) <= 100 and not re.search(r"[<>\"'`\x00-\x1f]", s)


def valid_lesson_label(label: str, lesson_type: str) -> bool:
    label = (label or "").strip()
    if lesson_type == "exam":
        return label == EXAM_LABEL
    return bool(LESSON_DATE_RE.fullmatch(label))
