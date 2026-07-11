from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from typing import ClassVar, Literal, TypeAlias

DecisionStatus: TypeAlias = Literal["approved", "blocked", "pending_human"]


def _require_text(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


@dataclass(frozen=True)
class PaymentIntent:
    schema_version: ClassVar[str] = "keyveil.intent.v1"

    task_id: str
    agent_id: str
    recipient: str
    token: str
    amount_usd: float
    reason: str
    intent_tag: str | None = None
    intent_id: str = field(default_factory=lambda: f"intent_{uuid.uuid4().hex}")

    def __post_init__(self) -> None:
        for name in ("intent_id", "task_id", "agent_id", "recipient", "token", "reason"):
            object.__setattr__(self, name, _require_text(name, getattr(self, name)))
        if isinstance(self.amount_usd, bool) or not isinstance(self.amount_usd, (int, float)):
            raise ValueError("amount_usd must be a finite positive number")
        try:
            amount_usd = float(self.amount_usd)
        except (TypeError, ValueError) as error:
            raise ValueError("amount_usd must be a finite positive number") from error
        if not math.isfinite(amount_usd) or amount_usd <= 0:
            raise ValueError("amount_usd must be a finite positive number")
        object.__setattr__(self, "amount_usd", amount_usd)
        if self.intent_tag is not None:
            intent_tag = _require_text("intent_tag", self.intent_tag).lower()
            object.__setattr__(self, "intent_tag", intent_tag)

    def canonical_digest(self) -> str:
        canonical = json.dumps(
            {"schema_version": self.schema_version, "intent": asdict(self)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PaymentReceipt:
    receipt_id: str
    schema_version: str
    ts_ms: int
    status: DecisionStatus
    intent_id: str
    intent_hash: str
    intent_schema_version: str
    session_id: str
    budget_scope_id: str
    policy_version: str
    task_id: str
    agent_id: str
    recipient: str
    token: str
    amount_usd: float
    reason: str
    intent_tag: str | None
    policy_hits: tuple[str, ...]
    risk_notes: tuple[str, ...]
    budget_reservation_id: str | None
    approval_id: str | None
    approved_by: str | None
    receipt_hash: str

    @staticmethod
    def new_id() -> str:
        return f"rcpt_{uuid.uuid4().hex}"

    @staticmethod
    def _hash_payload(payload: dict[str, object]) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def create(
        cls,
        *,
        ts_ms: int,
        status: DecisionStatus,
        intent: PaymentIntent,
        session_id: str,
        budget_scope_id: str,
        policy_version: str,
        policy_hits: tuple[str, ...],
        risk_notes: tuple[str, ...] = (),
        budget_reservation_id: str | None = None,
        approval_id: str | None = None,
        approved_by: str | None = None,
    ) -> PaymentReceipt:
        unsigned: dict[str, object] = {
            "receipt_id": cls.new_id(),
            "schema_version": "keyveil.receipt.v2",
            "ts_ms": ts_ms,
            "status": status,
            "intent_id": intent.intent_id,
            "intent_hash": intent.canonical_digest(),
            "intent_schema_version": intent.schema_version,
            "session_id": session_id,
            "budget_scope_id": budget_scope_id,
            "policy_version": policy_version,
            "task_id": intent.task_id,
            "agent_id": intent.agent_id,
            "recipient": intent.recipient,
            "token": intent.token,
            "amount_usd": intent.amount_usd,
            "reason": intent.reason,
            "intent_tag": intent.intent_tag,
            "policy_hits": policy_hits,
            "risk_notes": risk_notes,
            "budget_reservation_id": budget_reservation_id,
            "approval_id": approval_id,
            "approved_by": approved_by,
        }
        return cls(**unsigned, receipt_hash=cls._hash_payload(unsigned))

    def unsigned_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("receipt_hash")
        return payload

    def verify_hash(self) -> bool:
        return self.receipt_hash == self._hash_payload(self.unsigned_payload())

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, allow_nan=False)
