"""Helpers for safe log output."""
from __future__ import annotations


def mask_value(value, visible: int = 4) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    if len(s) <= 2:
        return "***"
    if len(s) <= visible * 2:
        return f"{s[0]}***{s[-1]}"
    return f"{s[:visible]}...{s[-visible:]}"


def mask_email(value) -> str:
    s = str(value or "").strip()
    if "@" not in s:
        return mask_value(s, 2)
    name, domain = s.split("@", 1)
    return f"{mask_value(name, 2)}@{domain}"


def mask_topic(topic: str) -> str:
    s = str(topic or "")
    if not s:
        return ""

    parts = s.split("/")
    masked = []
    for part in parts:
        if part.startswith("qd") and len(part) > 10:
            masked.append("qd" + mask_value(part[2:]))
        elif len(part) >= 10 and any(ch.isdigit() for ch in part) and any(ch.isalpha() for ch in part):
            masked.append(mask_value(part))
        else:
            masked.append(part)
    return "/".join(masked)
