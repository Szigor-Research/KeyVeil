from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Protocol

from .audit_receipt import PaymentIntent


@dataclass(frozen=True)
class ApprovalGrant:
    schema_version: str
    approval_id: str
    intent_id: str
    intent_hash: str
    session_id: str
    policy_version: str
    budget_scope_id: str
    approved_by: str
    issued_at_epoch: int
    expires_at_epoch: int
    signature: str


class ApprovalVerifier(Protocol):
    def verify(
        self,
        grant: ApprovalGrant,
        intent: PaymentIntent,
        *,
        session_id: str,
        policy_version: str,
        budget_scope_id: str,
        now_epoch: int,
    ) -> bool: ...


class HmacApprovalAuthority:
    """Reference approval authority for a trusted local control plane."""

    def __init__(self, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("approval secret must contain at least 32 bytes")
        self._secret = secret

    @staticmethod
    def _payload(grant: ApprovalGrant) -> bytes:
        payload = asdict(grant)
        payload.pop("signature")
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _signature(self, grant: ApprovalGrant) -> str:
        return hmac.new(self._secret, self._payload(grant), hashlib.sha256).hexdigest()

    def issue(
        self,
        *,
        intent: PaymentIntent,
        session_id: str,
        policy_version: str,
        budget_scope_id: str,
        approved_by: str,
        now_epoch: int | None = None,
        ttl_seconds: int = 300,
    ) -> ApprovalGrant:
        now = int(time.time()) if now_epoch is None else now_epoch
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        unsigned = ApprovalGrant(
            schema_version="keyveil.approval.v1",
            approval_id=f"approval_{uuid.uuid4().hex}",
            intent_id=intent.intent_id,
            intent_hash=intent.canonical_digest(),
            session_id=session_id.strip(),
            policy_version=policy_version.strip(),
            budget_scope_id=budget_scope_id.strip(),
            approved_by=approved_by.strip(),
            issued_at_epoch=now,
            expires_at_epoch=now + ttl_seconds,
            signature="",
        )
        if not all(
            (
                unsigned.session_id,
                unsigned.policy_version,
                unsigned.budget_scope_id,
                unsigned.approved_by,
            )
        ):
            raise ValueError(
                "session_id, policy_version, budget_scope_id, and approved_by must not be empty"
            )
        return ApprovalGrant(**{**asdict(unsigned), "signature": self._signature(unsigned)})

    def verify(
        self,
        grant: ApprovalGrant,
        intent: PaymentIntent,
        *,
        session_id: str,
        policy_version: str,
        budget_scope_id: str,
        now_epoch: int,
    ) -> bool:
        try:
            if grant.schema_version != "keyveil.approval.v1":
                return False
            if grant.intent_id != intent.intent_id:
                return False
            if grant.intent_hash != intent.canonical_digest():
                return False
            if grant.session_id != session_id:
                return False
            if grant.policy_version != policy_version:
                return False
            if grant.budget_scope_id != budget_scope_id:
                return False
            if grant.issued_at_epoch > now_epoch or now_epoch >= grant.expires_at_epoch:
                return False
            expected = self._signature(
                ApprovalGrant(**{**asdict(grant), "signature": ""})
            )
            return hmac.compare_digest(grant.signature, expected)
        except (AttributeError, TypeError, ValueError):
            return False
