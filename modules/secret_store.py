"""Best-effort Windows DPAPI protection for credentials stored on disk."""

from __future__ import annotations

import base64
import ctypes
import logging
import os
from ctypes import wintypes


logger = logging.getLogger("CS2Rental")
PREFIX = "dpapi:"
_ENTROPY = b"CS2RentalManager/local-credentials/v1"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    value = _DATA_BLOB(
        len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    )
    return value, buffer


def protect_secret(value: str) -> str:
    text = str(value or "")
    if not text or text.startswith(PREFIX) or os.name != "nt":
        return text
    try:
        source, source_buffer = _blob(text.encode("utf-8"))
        entropy, entropy_buffer = _blob(_ENTROPY)
        output = _DATA_BLOB()
        success = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(source),
            "CS2 Rental Manager",
            ctypes.byref(entropy),
            None,
            None,
            0x1,
            ctypes.byref(output),
        )
        _ = source_buffer, entropy_buffer
        if not success:
            raise ctypes.WinError()
        try:
            encrypted = ctypes.string_at(output.pbData, output.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)
        return PREFIX + base64.b64encode(encrypted).decode("ascii")
    except Exception as exc:
        logger.warning("Windows 凭据加密失败，保留原值: %s", exc)
        return text


def unprotect_secret(value: str) -> str:
    text = str(value or "")
    if not text.startswith(PREFIX):
        return text
    if os.name != "nt":
        return ""
    try:
        encrypted = base64.b64decode(text[len(PREFIX):], validate=True)
        source, source_buffer = _blob(encrypted)
        entropy, entropy_buffer = _blob(_ENTROPY)
        output = _DATA_BLOB()
        success = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            ctypes.byref(entropy),
            None,
            None,
            0x1,
            ctypes.byref(output),
        )
        _ = source_buffer, entropy_buffer
        if not success:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)
    except Exception as exc:
        logger.error("Windows 凭据解密失败；请重新填写或从加密同步包导入: %s", exc)
        return ""
