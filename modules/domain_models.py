"""Typed domain values shared by UI, storage and platform adapters."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import StrEnum
from typing import Any


class Platform(StrEnum):
    BUFF = "BUFF"
    C5 = "C5GAME"
    ECO = "ECOSteam"
    YYYP = "悠悠有品"
    IGXE = "IGXE"


def money_to_cents(value: Any) -> int:
    """Convert a user/API money value into an exact integer number of cents."""
    try:
        amount = Decimal(str(value or 0)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("金额必须是有效数字") from exc
    if not amount.is_finite():
        raise ValueError("金额必须是有限数字")
    return int(amount * 100)


def cents_to_money(value: Any) -> float:
    try:
        return float(Decimal(int(value or 0)) / Decimal(100))
    except (TypeError, ValueError, InvalidOperation):
        return 0.0


@dataclass(slots=True)
class InventoryItemDraft:
    name: str
    market_hash_name: str
    phase: str
    pattern: str
    float_val: str
    cost: float
    platform: str
    status: str
    rent: float
    days: int
    income: float
    expire_hours: float
    note: str = ""
    asset_id: str = ""
    cooldown_until: str = ""

    @staticmethod
    def _finite_number(value: Any, label: str, *, minimum: float = 0.0) -> float:
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}必须是数字") from exc
        if not math.isfinite(number) or number < minimum:
            raise ValueError(f"{label}必须是不小于 {minimum:g} 的有限数字")
        return number

    @classmethod
    def from_form(cls, values: dict[str, Any]) -> "InventoryItemDraft":
        name = str(values.get("name") or "").strip()
        if not name:
            raise ValueError("饰品名称不能为空")

        float_text = str(values.get("float_val") or "").strip()
        float_number = cls._finite_number(float_text, "磨损值")
        if float_number >= 1:
            raise ValueError("磨损值必须介于 0（含）和 1（不含）之间")

        try:
            days = int(str(values.get("days") or "0").strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("累计出租天数必须是整数") from exc
        if days < 0:
            raise ValueError("累计出租天数不能小于 0")

        return cls(
            name=name,
            market_hash_name=str(values.get("market_hash_name") or "").strip(),
            phase=str(values.get("phase") or "-").strip() or "-",
            pattern=str(values.get("pattern") or "-").strip() or "-",
            float_val=float_text,
            cost=cls._finite_number(values.get("cost", 0), "买入成本"),
            platform=str(values.get("platform") or Platform.C5),
            status=str(values.get("status") or "在库"),
            rent=cls._finite_number(values.get("rent", 0), "日租金"),
            days=days,
            income=cls._finite_number(values.get("income", 0), "累计收益"),
            expire_hours=cls._finite_number(
                values.get("expire_hours", 999), "到期剩余小时"
            ),
            note=str(values.get("note") or ""),
            asset_id=str(values.get("asset_id") or "").strip(),
            cooldown_until=str(values.get("cooldown_until") or "").strip(),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "market_hash_name": self.market_hash_name,
            "phase": self.phase,
            "pattern": self.pattern,
            "float_val": self.float_val,
            "cost": self.cost,
            "platform": self.platform,
            "status": self.status,
            "rent": self.rent,
            "days": self.days,
            "income": self.income,
            "expire_hours": self.expire_hours,
            "note": self.note,
            "asset_id": self.asset_id,
            "cooldown_until": self.cooldown_until,
        }
