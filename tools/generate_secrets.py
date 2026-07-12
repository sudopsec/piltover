#!/usr/bin/env python3
"""Generate new secret values for Piltover config/*.toml files."""

from __future__ import annotations

import argparse
import base64
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import tomllib

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = _PROJECT_ROOT / "config.custom"
CONFIG_FILES = ("app.toml", "gateway.toml", "system.toml", "worker.toml")

DEFAULT_HMAC_KEY = "Gt1BNkYR0JGkJIntZWEBwub8UAZuNiqoXvKRQ6HWJ40="
DEFAULT_SALT_KEY = "V0643QqIQ1HgoIgoK24PJ9iMUoNBniF2Ak3otH0DvMA="


@dataclass(frozen=True, slots=True)
class SecretTarget:
    name: str
    description: str
    config_file: str
    toml_key: str
    current_value: str | None = None
    is_default: bool = False
    auto_generate: bool = True


def _load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def _scan_secrets(config_dir: Path) -> list[SecretTarget]:
    app_cfg = _load_toml(config_dir / "app.toml")
    gateway_cfg = _load_toml(config_dir / "gateway.toml")
    system_cfg = _load_toml(config_dir / "system.toml")

    app = app_cfg.get("app", {})
    gateway = gateway_cfg.get("gateway", {})
    system = system_cfg.get("system", {})

    targets: list[SecretTarget] = [
        SecretTarget(
            name="hmac_key",
            description="Подпись access hash, file reference, contact token и т.д.",
            config_file="app.toml",
            toml_key="hmac_key",
            current_value=app.get("hmac_key"),
            is_default=app.get("hmac_key") == DEFAULT_HMAC_KEY,
        ),
        SecretTarget(
            name="salt_key",
            description="Генерация MTProto salt на gateway.",
            config_file="gateway.toml",
            toml_key="salt_key",
            current_value=gateway.get("salt_key"),
            is_default=gateway.get("salt_key") == DEFAULT_SALT_KEY,
        ),
    ]

    gifs = app.get("gifs")
    if isinstance(gifs, dict) and gifs.get("api_key"):
        targets.append(
            SecretTarget(
                name="gifs.api_key",
                description="API-ключ провайдера GIF (Klipy).",
                config_file="app.toml",
                toml_key="api_key",
                current_value=gifs["api_key"],
                auto_generate=False,
            )
        )

    rabbitmq = system.get("rabbitmq_address")
    if rabbitmq:
        targets.append(
            SecretTarget(
                name="rabbitmq_address",
                description="Учётные данные RabbitMQ в connection string.",
                config_file="system.toml",
                toml_key="rabbitmq_address",
                current_value=rabbitmq,
                auto_generate=False,
            )
        )

    return targets


def _generate_random_b64_key() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


def _replace_toml_value(path: Path, key: str, value: str) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = rf'^(\s*{re.escape(key)}\s*=\s*)".*?"\s*$'
    replacement = rf'\1"{value}"'
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f'Не удалось обновить "{key}" в {path}')
    path.write_text(new_text, encoding="utf-8")


def _print_header(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _print_secret_block(target: SecretTarget, generated_value: str | None = None) -> None:
    print()
    print(f"[{target.name}]")
    print(f"  Назначение: {target.description}")
    print(f"  Куда: config/{target.config_file}")
    print(f"  Параметр: {target.toml_key}")

    if target.current_value is not None:
        print(f"  Текущее значение: {target.current_value}")

    if target.is_default:
        print("  ⚠ Сейчас значение по умолчанию из репозитория — обязательно замените!")

    if generated_value is not None:
        print(f"  Новое значение: {generated_value}")
        print()
        print(f'  {target.toml_key} = "{generated_value}"')
    elif not target.auto_generate:
        print("  Новое значение: задаётся вручную (скрипт не генерирует).")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Сгенерировать секретные значения для config/*.toml и показать, куда их поставить.",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=CONFIG_DIR,
        help="Папка с app/gateway/system/worker .toml (по умолчанию: config/)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Записать сгенерированные значения в config/*.toml",
    )
    args = parser.parse_args()

    config_dir = args.config_dir.resolve()
    missing = [name for name in CONFIG_FILES if not (config_dir / name).exists()]
    if missing:
        print(f"Ошибка: не найдены файлы в {config_dir}: {', '.join(missing)}", file=sys.stderr)
        return 1

    targets = _scan_secrets(config_dir)
    generated: dict[str, str] = {
        "hmac_key": _generate_random_b64_key(),
        "salt_key": _generate_random_b64_key(),
    }

    _print_header("Секреты в config/*.toml")
    print()
    print("Файлы:")
    for name in CONFIG_FILES:
        print(f"  - {config_dir / name}")

    defaults = [t.name for t in targets if t.is_default]
    if defaults:
        print()
        print("Небезопасные значения по умолчанию:")
        for name in defaults:
            print(f"  - {name}")

    _print_header("Новые значения и куда их поставить")

    for target in targets:
        value = generated.get(target.name) if target.auto_generate else None
        _print_secret_block(target, generated_value=value)

    if args.apply:
        _print_header("Запись в config/*.toml (--apply)")
        for target in targets:
            if not target.auto_generate:
                continue
            value = generated[target.name]
            _replace_toml_value(config_dir / target.config_file, target.toml_key, value)
            print(f"  ✓ config/{target.config_file} → {target.toml_key}")
    else:
        _print_header("Как применить")
        print()
        print("  Скопируйте значения в соответствующие .toml файлы, либо:")
        print(f"     python tools/generate_secrets.py --apply")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())