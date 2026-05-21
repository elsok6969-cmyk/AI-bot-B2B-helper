#!/bin/bash
# Mynota Client Assistant — macOS Launcher (double-click)
# Этот файл можно запускать двойным кликом. Он откроет терминал и запустит бота.

cd "$(dirname "$0")" || exit 1

# Проверяем, что Python 3.12+ доступен
PYTHON=""
for cmd in python3.12 python3 python; do
    if command -v "$cmd" &> /dev/null; then
        ver=$("$cmd" -c 'import sys; print(sys.version_info.major * 100 + sys.version_info.minor)' 2>/dev/null)
        if [ "$ver" -ge "312" ] 2>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ Python 3.12+ не найден."
    echo "Установи через Homebrew:  brew install python@3.12"
    read -r -p "Нажми Enter для выхода..."
    exit 1
fi

# Запускаем установщик/бота
"$PYTHON" start_mac.py
EXIT_CODE=$?

# Если скрипт завершился с ошибкой — не закрываем окно сразу
if [ $EXIT_CODE -ne 0 ]; then
    echo
    read -r -p "Нажми Enter для выхода..."
fi
