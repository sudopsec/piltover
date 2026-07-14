import hashlib

import pytest

from piltover.app.handlers.phone import (
    _merge_protocols, _check_protocol, _normalize_library_versions, _validate_dh_bytes,
)
from piltover.exceptions import ErrorRpc
from piltover.tl import PhoneCallProtocol


def _protocol(**kwargs) -> PhoneCallProtocol:
    defaults = {
        "udp_p2p": True,
        "udp_reflector": True,
        "min_layer": 92,
        "max_layer": 92,
        "library_versions": ["11.0.0"],
    }
    defaults.update(kwargs)
    return PhoneCallProtocol(**defaults)


def test_normalize_library_versions_fallback() -> None:
    assert _normalize_library_versions(["99.0.0"]) == ["2.4.4"]


def test_merge_protocols_intersects_versions() -> None:
    merged = _merge_protocols(
        _protocol(library_versions=["11.0.0", "9.0.0"]),
        _protocol(library_versions=["11.0.0", "8.0.0"]),
    )
    assert "11.0.0" in merged.library_versions


def test_merge_protocols_final_picks_highest() -> None:
    merged = _merge_protocols(
        _protocol(library_versions=["9.0.0", "11.0.0"]),
        _protocol(library_versions=["8.0.0", "11.0.0"]),
        final=True,
    )
    assert merged.library_versions == ["11.0.0"]


def test_merge_protocols_preserves_udp_flags() -> None:
    merged = _merge_protocols(
        _protocol(udp_p2p=False, udp_reflector=True),
        _protocol(udp_p2p=True, udp_reflector=False),
    )
    assert merged.udp_p2p
    assert merged.udp_reflector


def test_check_protocol_rejects_invalid_layers() -> None:
    with pytest.raises(ErrorRpc) as exc:
        _check_protocol(_protocol(min_layer=200, max_layer=92))
    assert exc.value.error_message == "CALL_PROTOCOL_LAYER_INVALID"


def test_validate_dh_bytes() -> None:
    _validate_dh_bytes(b"x" * 256, "G_B_INVALID")
    with pytest.raises(ErrorRpc):
        _validate_dh_bytes(b"", "G_B_INVALID")