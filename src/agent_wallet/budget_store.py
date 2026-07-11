from __future__ import annotations

import math
import re
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Protocol

ReservationStatus = Literal["reserved", "committed", "released"]


@dataclass(frozen=True)
class BudgetReservation:
    reservation_id: str
    session_id: str
    budget_scope_id: str
    intent_id: str
    intent_hash: str
    amount_usd: float
    day_bucket: str
    week_bucket: str
    status: ReservationStatus = "reserved"


@dataclass(frozen=True)
class BudgetResult:
    approved: bool
    reservation: BudgetReservation | None = None
    policy_hit: str | None = None
    risk_note: str | None = None


class BudgetStore(Protocol):
    def reserve(
        self,
        *,
        session_id: str,
        budget_scope_id: str,
        intent_id: str,
        intent_hash: str,
        amount_usd: float,
        daily_limit_usd: float,
        weekly_limit_usd: float | None,
        now_epoch: int,
    ) -> BudgetResult: ...

    def commit(self, reservation_id: str) -> BudgetReservation: ...

    def release(self, reservation_id: str) -> BudgetReservation: ...


def _money(value: float) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("budget values must be finite and non-negative")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("budget values must be finite and non-negative") from error
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError("budget values must be finite and non-negative")
    return Decimal(str(normalized))


def _identifier(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _intent_hash(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("intent_hash must be a 64-character SHA-256 hex digest")
    normalized = value.strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
        raise ValueError("intent_hash must be a 64-character SHA-256 hex digest")
    return normalized


class InMemoryBudgetStore:
    """Thread-safe reference store with intent-level idempotency."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, BudgetReservation] = {}
        self._intent_index: dict[tuple[str, str], str] = {}

    @staticmethod
    def _buckets(now_epoch: int) -> tuple[str, str]:
        now = datetime.fromtimestamp(now_epoch, timezone.utc)
        iso_year, iso_week, _ = now.isocalendar()
        return now.date().isoformat(), f"{iso_year}-W{iso_week:02d}"

    def _spent(self, scope_id: str, bucket: str, *, weekly: bool) -> Decimal:
        total = Decimal("0")
        for record in self._records.values():
            record_bucket = record.week_bucket if weekly else record.day_bucket
            record_scope = record.budget_scope_id if weekly else record.session_id
            if (
                record_scope == scope_id
                and record_bucket == bucket
                and record.status in {"reserved", "committed"}
            ):
                total += _money(record.amount_usd)
        return total

    def reserve(
        self,
        *,
        session_id: str,
        budget_scope_id: str,
        intent_id: str,
        intent_hash: str,
        amount_usd: float,
        daily_limit_usd: float,
        weekly_limit_usd: float | None,
        now_epoch: int,
    ) -> BudgetResult:
        session_id = _identifier("session_id", session_id)
        budget_scope_id = _identifier("budget_scope_id", budget_scope_id)
        intent_id = _identifier("intent_id", intent_id)
        intent_hash = _intent_hash(intent_hash)
        amount = _money(amount_usd)
        daily_limit = _money(daily_limit_usd)
        weekly_limit = _money(weekly_limit_usd) if weekly_limit_usd is not None else None
        if amount <= 0:
            raise ValueError("amount_usd must be positive")

        day_bucket, week_bucket = self._buckets(now_epoch)
        key = (budget_scope_id, intent_id)
        with self._lock:
            existing_id = self._intent_index.get(key)
            if existing_id:
                existing = self._records[existing_id]
                if existing.status == "released":
                    return BudgetResult(
                        False,
                        policy_hit="intent_replay_released",
                        risk_note="released intents cannot be reserved again",
                    )
                if existing.session_id != session_id:
                    return BudgetResult(
                        False,
                        policy_hit="intent_session_mismatch",
                        risk_note="intent id was already reserved in a different session",
                    )
                if _money(existing.amount_usd) != amount:
                    return BudgetResult(
                        False,
                        policy_hit="intent_amount_mismatch",
                        risk_note="intent id was already reserved with a different amount",
                    )
                if existing.intent_hash != intent_hash:
                    return BudgetResult(
                        False,
                        policy_hit="intent_payload_mismatch",
                        risk_note="intent id was already reserved with a different payload",
                    )
                return BudgetResult(True, reservation=existing)

            if self._spent(session_id, day_bucket, weekly=False) + amount > daily_limit:
                return BudgetResult(
                    False,
                    policy_hit="session_daily_budget",
                    risk_note="daily budget would be exceeded",
                )
            if weekly_limit is not None and (
                self._spent(budget_scope_id, week_bucket, weekly=True) + amount > weekly_limit
            ):
                return BudgetResult(
                    False,
                    policy_hit="policy_weekly_budget",
                    risk_note="weekly budget would be exceeded",
                )

            reservation = BudgetReservation(
                reservation_id=f"budget_{uuid.uuid4().hex}",
                session_id=session_id,
                budget_scope_id=budget_scope_id,
                intent_id=intent_id,
                intent_hash=intent_hash,
                amount_usd=amount_usd,
                day_bucket=day_bucket,
                week_bucket=week_bucket,
            )
            self._records[reservation.reservation_id] = reservation
            self._intent_index[key] = reservation.reservation_id
            return BudgetResult(True, reservation=reservation)

    def _transition(self, reservation_id: str, status: ReservationStatus) -> BudgetReservation:
        with self._lock:
            current = self._records.get(reservation_id)
            if current is None:
                raise KeyError(f"unknown reservation: {reservation_id}")
            if current.status == "released" and status == "committed":
                raise ValueError("released reservations cannot be committed")
            if current.status == "committed" and status == "released":
                raise ValueError("committed reservations cannot be released")
            if current.status == status:
                return current
            updated = replace(current, status=status)
            self._records[reservation_id] = updated
            return updated

    def commit(self, reservation_id: str) -> BudgetReservation:
        return self._transition(reservation_id, "committed")

    def release(self, reservation_id: str) -> BudgetReservation:
        return self._transition(reservation_id, "released")
