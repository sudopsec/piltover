from __future__ import annotations

LIST_KEY_DEFAULT = "u0"


def encode_list_key(src: str, page: int) -> str:
    return f"{src}{page}"


def parse_list_key(key: str) -> tuple[str, int]:
    return key[0], int(key[1:])


def back_list_data(key: str) -> bytes:
    src, page = parse_list_key(key)
    if src == "a":
        return f"adm:admins:{page}".encode()
    return f"adm:users:{page}".encode()


def split_list_key(data: bytes) -> tuple[bytes, str]:
    text = data.decode()
    parts = text.split(":")
    if len(parts) >= 2 and parts[-1][:1] in ("u", "a") and parts[-1][1:].isdigit():
        body = ":".join(parts[:-1]).encode()
        return body, parts[-1]
    return data, LIST_KEY_DEFAULT


def user_link(user_id: int, list_key: str) -> bytes:
    return f"adm:user:{user_id}:{list_key}".encode()


def user_action(action: str, user_id: int, list_key: str) -> bytes:
    return f"adm:act:{action}:{user_id}:{list_key}".encode()


def stars_action(action: str, user_id: int, amount: int, list_key: str) -> bytes:
    return f"adm:act:stars:{action}:{user_id}:{amount}:{list_key}".encode()


def encode_stars_wait_data(user_id: int, list_key: str) -> bytes:
    return f"{user_id}:{list_key}".encode()


def decode_stars_wait_data(data: bytes | None) -> tuple[int, str]:
    if not data:
        return 0, LIST_KEY_DEFAULT
    text = data.decode()
    user_id_str, list_key = text.split(":", 1)
    return int(user_id_str), list_key