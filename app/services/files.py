# app/services/files.py
from __future__ import annotations

from io import BytesIO
from typing import List
import csv
import re

try:
    # openpyxl is in requirements; this import will fail gracefully if not present
    from openpyxl import load_workbook  # type: ignore
except Exception:  # pragma: no cover
    load_workbook = None  # type: ignore

# First 4 digits if present; otherwise first token of letters/numbers/dashes
FOUR_DIGIT = re.compile(r"^(\d{4})")
TOKEN = re.compile(r"^([A-Za-z0-9\-]+)")

def _norm(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    m = FOUR_DIGIT.match(s)
    if m:
        return m.group(1)
    m = TOKEN.match(s)
    return m.group(1) if m else s

def parse_file_to_skus(file_bytes: bytes, filename: str | None = None) -> List[str]:
    """
    Accepts .xlsx, .csv, or .txt (one SKU per line). Returns a de-duplicated list of SKUs.
    We normalize to the first 4 digits when they exist (your workflow), otherwise first token.
    """
    name = (filename or "").lower()
    out: list[str] = []

    def push(v) -> None:
        val = _norm(str(v))
        if val and val not in out:
            out.append(val)

    if name.endswith((".xlsx", ".xlsm", ".xltx", ".xltm")):
        if load_workbook is None:
            raise RuntimeError("openpyxl is required to read .xlsx files")
        wb = load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
        ws = wb.active
        for row in ws.iter_rows(min_row=1, values_only=True):
            if row and row[0] is not None:
                push(row[0])
        return out

    if name.endswith(".csv"):
        text = BytesIO(file_bytes).read().decode("utf-8", errors="ignore")
        for row in csv.reader(text.splitlines()):
            if row:
                push(row[0])
        return out

    # default: plain text
    text = BytesIO(file_bytes).read().decode("utf-8", errors="ignore")
    for line in text.splitlines():
        if line.strip():
            push(line.strip())
    return out
