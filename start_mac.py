#!/usr/bin/env python3
"""
Mynota Client Assistant — macOS Launcher

Автоматически:
  • проверяет Python 3.12+
  • проверяет / ставит PostgreSQL через Homebrew
  • создаёт .env из шаблона (если нужно)
  • создаёт виртуальное окружение и ставит зависимости
  • запускает PostgreSQL, создаёт БД и пользователя
  • накатывает миграции Alembic
  • запускает бота

Запуск:
    python3 start_mac.py
    # или, если файл исполняемый:
    ./start_mac.py
    # или двойным кликом по Start.command
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# Цвета
# ──────────────────────────────────────────────────────────────────────────────

R = "\033[0;31m"
G = "\033[0;32m"
Y = "\033[1;33m"
B = "\033[0;34m"
C = "\033[0;36m"
NC = "\033[0m"


def info(msg: str) -> None:
    print(f"{B}[INFO]{NC}  {msg}")


def ok(msg: str) -> None:
    print(f"{G}[OK]{NC}    {msg}")


def warn(msg: str) -> None:
    print(f"{Y}[WARN]{NC}  {msg}")


def error(msg: str) -> None:
    print(f"{R}[ERROR]{NC} {msg}")


def step(title: str) -> None:
    print(f"\n{C}▶ {title}{NC}")


def confirm(msg: str) -> bool:
    resp = input(f"{msg} [y/N]: ").strip().lower()
    return resp in ("y", "yes")


def run(cmd: list[str] | str, check: bool = True, capture: bool = False, **kwargs):
    """Вспомогателька для subprocess."""
    if isinstance(cmd, str):
        cmd = [cmd]
    kwargs.setdefault("text", True)
    if capture:
        return subprocess.run(cmd, check=check, capture_output=True, **kwargs)
    return subprocess.run(cmd, check=check, **kwargs)


# ──────────────────────────────────────────────────────────────────────────────
# Пути
# ──────────────────────────────────────────────────────────────────────────────

PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / ".venv"
ENV_FILE = PROJECT_DIR / ".env"
ENV_EXAMPLE = PROJECT_DIR / ".env.example"


def main() -> int:
    # ═══════════════════════════════════════════════════════════════════════════
    # 1. Python 3.12+
    # ═══════════════════════════════════════════════════════════════════════════

    step("Проверка Python")

    ver = sys.version_info
    if ver.major < 3 or (ver.major == 3 and ver.minor < 12):
        error(f"Python {ver.major}.{ver.minor} — нужен 3.12+")
        info("Установи через Homebrew:  brew install python@3.12")
        return 1

    ok(f"Python {ver.major}.{ver.minor}.{ver.micro}")

    # ═══════════════════════════════════════════════════════════════════════════
    # 2. Homebrew
    # ═══════════════════════════════════════════════════════════════════════════

    step("Проверка Homebrew")

    brew_path = shutil.which("brew")
    if not brew_path:
        warn("Homebrew не найден")
        if confirm("Установить Homebrew?"):
            info("Устанавливаю Homebrew...")
            install_script = (
                'NONINTERACTIVE=1 /bin/bash -c '
                '"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
            )
            subprocess.run(install_script, shell=True, check=True)
            # Обновляем PATH для текущей сессии
            homebrew_paths = [
                "/opt/homebrew/bin",
                "/usr/local/bin",
            ]
            for p in homebrew_paths:
                if Path(p).exists() and p not in os.environ["PATH"]:
                    os.environ["PATH"] = p + ":" + os.environ["PATH"]
            brew_path = shutil.which("brew")
            if brew_path:
                ok(f"Homebrew установлен: {brew_path}")
            else:
                error("Homebrew установлен, но не найден в PATH. Перезапусти терминал и попробуй снова.")
                return 1
        else:
            error("Без Homebrew не установить PostgreSQL. См. https://brew.sh")
            return 1
    else:
        ok(f"Homebrew: {brew_path}")

    # ═══════════════════════════════════════════════════════════════════════════
    # 3. .env
    # ═══════════════════════════════════════════════════════════════════════════

    step("Проверка .env")

    if not ENV_FILE.exists():
        warn(".env не найден — копирую из .env.example")
        shutil.copy(ENV_EXAMPLE, ENV_FILE)
        ok(".env создан")
        info("BOT_TOKEN/AI ключ и т.д. заполнишь через веб на http://localhost:8090/setup")

    # Парсим .env в dict
    env_vars: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            env_vars[key.strip()] = val.strip()

    os.environ.update(env_vars)
    ok(".env на месте")

    # ═══════════════════════════════════════════════════════════════════════════
    # 4. PostgreSQL
    # ═══════════════════════════════════════════════════════════════════════════

    step("Проверка PostgreSQL")

    psql_path = shutil.which("psql")
    pg_ctl_path = shutil.which("pg_ctl")
    pg_isready_path = shutil.which("pg_isready")

    if not psql_path:
        warn("PostgreSQL не найден")
        if confirm("Установить PostgreSQL 16 через Homebrew?"):
            info("Устанавливаю PostgreSQL...")
            run(["brew", "install", "postgresql@16"])
            # Обновляем PATH
            pg16_bin = Path("/opt/homebrew/opt/postgresql@16/bin")
            if not pg16_bin.exists():
                pg16_bin = Path("/usr/local/opt/postgresql@16/bin")
            if pg16_bin.exists() and str(pg16_bin) not in os.environ["PATH"]:
                os.environ["PATH"] = str(pg16_bin) + ":" + os.environ["PATH"]
            ok("PostgreSQL установлен")
            # Перечитываем пути к свежеустановленным бинарникам
            psql_path = shutil.which("psql")
            pg_ctl_path = shutil.which("pg_ctl")
            pg_isready_path = shutil.which("pg_isready")
        else:
            error("Без PostgreSQL бот не запустится")
            return 1
    else:
        ver_out = run(["psql", "--version"], capture=True).stdout.strip()
        ok(ver_out)

    # Проверяем, запущен ли Postgres (pg_isready возвращает 0 только если принимает подключения)
    pg_running = False
    try:
        result = run(["pg_isready", "-q"], capture=True, check=False)
        pg_running = result.returncode == 0
    except FileNotFoundError:
        pass

    if not pg_running:
        info("Запускаю PostgreSQL...")
        # Пробуем brew services
        try:
            run(["brew", "services", "start", "postgresql@16"], check=False)
        except Exception:
            pass
        try:
            run(["brew", "services", "start", "postgresql"], check=False)
        except Exception:
            pass

        # Ждём готовности (проверяем именно returncode == 0)
        for i in range(30):
            try:
                result = run(["pg_isready", "-q"], capture=True, check=False)
                if result.returncode == 0:
                    ok("PostgreSQL готов к работе")
                    pg_running = True
                    break
            except FileNotFoundError:
                pass
            time.sleep(1)

        if not pg_running:
            error("Не удалось запустить PostgreSQL")
            info("Попробуй вручную:  brew services start postgresql@16")
            return 1
    else:
        ok("PostgreSQL уже работает")

    # ═══════════════════════════════════════════════════════════════════════════
    # 5. Создание БД и пользователя
    # ═══════════════════════════════════════════════════════════════════════════

    step("Создание базы данных")

    db_user = env_vars.get("POSTGRES_USER", "mynota")
    db_pass = env_vars.get("POSTGRES_PASSWORD", "mynota")
    db_name = env_vars.get("POSTGRES_DB", "mynota")

    # Safety: identifiers must be alphanumeric/underscore. Passwords get
    # SQL-quote-escaped before interpolation. Without this, a `'` in the
    # password breaks CREATE USER, and a `;` in the user name = injection.
    import re as _re

    if not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", db_user):
        error(f"POSTGRES_USER `{db_user}` некорректен — только латиница/_/цифры.")
        return 1
    if not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", db_name):
        error(f"POSTGRES_DB `{db_name}` некорректен — только латиница/_/цифры.")
        return 1
    db_pass_sql = db_pass.replace("'", "''")

    # Дополнительно убеждаемся, что psql реально подключается
    for i in range(30):
        try:
            run(["psql", "-d", "postgres", "-c", "SELECT 1;"], capture=True, check=False)
            break
        except FileNotFoundError:
            pass
        time.sleep(1)
    else:
        error("PostgreSQL не отвечает на подключения psql")
        info("Попробуй вручную:  brew services restart postgresql@16")
        return 1

    # Создаём пользователя
    user_exists = False
    try:
        out = run(
            ["psql", "-d", "postgres", "-tc", f"SELECT 1 FROM pg_roles WHERE rolname='{db_user}'"],
            capture=True,
            check=False,
        )
        user_exists = "1" in out.stdout
    except Exception:
        pass

    if not user_exists:
        try:
            run(["psql", "-d", "postgres", "-c", f"CREATE USER {db_user} WITH PASSWORD '{db_pass_sql}';"])
        except subprocess.CalledProcessError:
            pass  # может уже существовать

    # Создаём БД
    db_exists = False
    try:
        out = run(
            ["psql", "-d", "postgres", "-tc", f"SELECT 1 FROM pg_database WHERE datname='{db_name}'"],
            capture=True,
            check=False,
        )
        db_exists = "1" in out.stdout
    except Exception:
        pass

    if not db_exists:
        try:
            run(["psql", "-d", "postgres", "-c", f"CREATE DATABASE {db_name} OWNER {db_user};"])
        except subprocess.CalledProcessError:
            pass

    # Права
    try:
        run(
            ["psql", "-d", db_name, "-c", "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {db_user};".format(db_user=db_user)],
            check=False,
        )
    except Exception:
        pass

    ok(f"База {db_name} и пользователь {db_user} готовы")

    # ═══════════════════════════════════════════════════════════════════════════
    # 6. DATABASE_URL
    # ═══════════════════════════════════════════════════════════════════════════

    step("Проверка DATABASE_URL")

    current_db_url = env_vars.get("DATABASE_URL", "")
    if "@postgres:" in current_db_url or "@localhost" in current_db_url or not current_db_url:
        local_url = f"postgresql+asyncpg://{db_user}:{db_pass}@localhost:5432/{db_name}"
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
        new_lines = []
        replaced = False
        for line in lines:
            if line.strip().startswith("DATABASE_URL="):
                new_lines.append(f"DATABASE_URL={local_url}")
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            new_lines.append(f"DATABASE_URL={local_url}")
        ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        ok(f"DATABASE_URL обновлён на {local_url}")
        os.environ["DATABASE_URL"] = local_url
    else:
        ok(f"DATABASE_URL уже настроен")

    # ═══════════════════════════════════════════════════════════════════════════
    # 7. Виртуальное окружение + зависимости
    # ═══════════════════════════════════════════════════════════════════════════

    step("Установка Python-зависимостей")

    if not VENV_DIR.exists():
        info("Создаю виртуальное окружение...")
        venv.create(VENV_DIR, with_pip=True)
        ok(".venv создан")

    venv_python = VENV_DIR / "bin" / "python"
    venv_pip = VENV_DIR / "bin" / "pip"

    info("Обновляю pip...")
    run([str(venv_pip), "install", "-q", "--upgrade", "pip"])

    info("Устанавливаю зависимости (это может занять минуту)...")
    run([str(venv_pip), "install", "-q", "-e", str(PROJECT_DIR)])

    ok("Зависимости установлены")

    # ═══════════════════════════════════════════════════════════════════════════
    # 7b. SECRETS_KEY (Fernet) — генерируем, если в .env пусто
    # ═══════════════════════════════════════════════════════════════════════════

    step("Проверка SECRETS_KEY")

    if not env_vars.get("SECRETS_KEY", "").strip():
        info("SECRETS_KEY пуст — генерирую ключ Fernet...")
        gen_proc = run(
            [
                str(venv_python),
                "-c",
                "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())",
            ],
            capture=True,
        )
        new_key = gen_proc.stdout.strip()
        if not new_key:
            error("Не удалось сгенерировать SECRETS_KEY")
            return 1

        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
        new_lines: list[str] = []
        replaced = False
        for line in lines:
            if line.strip().startswith("SECRETS_KEY="):
                new_lines.append(f"SECRETS_KEY={new_key}")
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            new_lines.append(f"SECRETS_KEY={new_key}")
        ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        env_vars["SECRETS_KEY"] = new_key
        os.environ["SECRETS_KEY"] = new_key
        ok("SECRETS_KEY записан в .env (нужен для шифрования пароля почты и сессии Telethon)")
    else:
        ok("SECRETS_KEY уже задан")

    # ═══════════════════════════════════════════════════════════════════════════
    # 8. Alembic миграции
    # ═══════════════════════════════════════════════════════════════════════════

    step("Накат миграций")

    venv_alembic = VENV_DIR / "bin" / "alembic"
    run([str(venv_alembic), "upgrade", "head"], cwd=str(PROJECT_DIR))

    ok("Миграции применены")

    # ═══════════════════════════════════════════════════════════════════════════
    # 9. Проверяем, не запущен ли уже бот
    # ═══════════════════════════════════════════════════════════════════════════

    step("Проверка запущенного бота")

    import socket

    def is_port_in_use(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("localhost", port)) == 0

    for port in (8080, 8090):
        if is_port_in_use(port):
            warn(f"Порт {port} занят — останавливаю старый процесс...")
            subprocess.run(
                f"lsof -ti:{port} | xargs kill -9 2>/dev/null",
                shell=True,
                check=False,
            )
            time.sleep(1)
            ok(f"Порт {port} освобождён")
        else:
            ok(f"Порт {port} свободен")

    # ═══════════════════════════════════════════════════════════════════════════
    # 10. Запуск бота + автооткрытие веба
    # ═══════════════════════════════════════════════════════════════════════════

    step("Запуск бота")

    info("Бот стартует... Нажми Ctrl+C для остановки.")
    info("Веб-морда будет на http://localhost:8090 (откроется автоматически).")
    print("═" * 60)

    # Запускаем бота фоновым процессом, чтобы успеть открыть браузер,
    # потом ждём его завершения тут же.
    bot_proc = subprocess.Popen(
        [str(venv_python), "-m", "src.main"],
        cwd=str(PROJECT_DIR),
    )

    # Ждём, пока веб реально поднимется, и открываем браузер.
    def _wait_for_web(port: int, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if is_port_in_use(port):
                return True
            if bot_proc.poll() is not None:
                return False
            time.sleep(0.5)
        return False

    if _wait_for_web(8090):
        ok("Веб-морда доступна на http://localhost:8090")
        with contextlib.suppress(Exception):
            subprocess.Popen(["open", "http://localhost:8090"])
    else:
        warn("Веб-морда не поднялась за 30 сек — открой http://localhost:8090 вручную, когда бот стартует")

    try:
        bot_proc.wait()
    except KeyboardInterrupt:
        info("Останавливаю бота...")
        bot_proc.terminate()
        try:
            bot_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            bot_proc.kill()

    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except SystemExit as e:
        exit_code = e.code if isinstance(e.code, int) else 1
    except Exception as exc:
        error(f"Неожиданная ошибка: {exc}")
        exit_code = 1

    if exit_code != 0:
        print()
        input("Нажми Enter для выхода...")

    sys.exit(exit_code)
