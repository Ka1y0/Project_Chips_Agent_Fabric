"""Authenticated transport seam and local derived inbox/outbox; no enrollment or execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain import EventSeverity
from ..heartbeat import parse_time, utc_time
from ..protocols.identity import NodePublicIdentity
from ..protocols.transport import TransportHealth, TransportStatus
from ..store import StateStore, compact_json, redact_sensitive
from .model import BRIDGE_VERSION, IDENTITY_CODEC, BridgeEnvelope, StructuredArtifact


class NodeStatusPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["node-bridge/v1"] = "node-bridge/v1"
    kind: Literal["nodeStatus"] = "nodeStatus"
    node_id: str = Field(min_length=1, max_length=200)
    state: Literal["online", "degraded", "offline", "unprovisioned"]
    worker_count: int = Field(ge=0, le=100000)
    observed_at: str
    expires_at: str

    @field_validator("observed_at", "expires_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        parse_time(value)
        return value


class DelegationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["node-bridge/v1"] = "node-bridge/v1"
    kind: Literal["delegationProposal"] = "delegationProposal"
    task_id: str = Field(min_length=1, max_length=200)
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    permission_class: Literal["green"] = "green"
    code_write_required: bool = Field(default=False, json_schema_extra={"const": False})
    labels: list[Literal["research", "review"]] = Field(min_length=1, max_length=2)
    observed_at: str
    expires_at: str

    @field_validator("code_write_required")
    @classmethod
    def forbid_writes(cls, value: bool) -> bool:
        if value:
            raise ValueError("Bridge delegation cannot request code writing")
        return value


@dataclass(frozen=True)
class VerifiedPeer:
    """Public evidence supplied by the operator-injected transport, never by envelope content."""

    identity: NodePublicIdentity
    transport: TransportStatus


class NodeBridgeTransport(Protocol):
    async def verified_peer(self) -> VerifiedPeer: ...
    async def send(self, encoded: bytes) -> None: ...
    async def receive(self) -> bytes: ...


class NodeBridgeExchange:
    """One peer exchange. The injected transport owns authentication/key proof and private routing.

    Only pre-enrolled public identities are accepted. Receipt creates an advisory record, never a
    node/Worker update, Task, grant, or dispatch. Delegations must match a pre-reviewed local Task.
    """

    def __init__(
        self,
        store: StateStore,
        *,
        local_node_id: str,
        transport: NodeBridgeTransport,
        enabled: bool = False,
    ) -> None:
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        self.store = store
        self.local_node_id = local_node_id
        self.transport = transport
        self.enabled = enabled

    async def _peer(self, now: datetime) -> VerifiedPeer:
        if not self.enabled:
            raise ValueError("node Bridge exchange is disabled")
        peer = await asyncio.wait_for(self.transport.verified_peer(), timeout=2)
        status = peer.transport
        if (
            not status.safe_for_fabric
            or status.health is not TransportHealth.HEALTHY
            or status.peer_identity != peer.identity.node_id
            or status.observed_at.tzinfo is None
            or not timedelta(0) <= now - status.observed_at <= timedelta(seconds=30)
        ):
            raise ValueError("Bridge transport identity/health is unverified or stale")
        with self.store.connect() as connection:
            self._identity(connection, peer)
        return peer

    def _identity(self, connection: sqlite3.Connection, peer: VerifiedPeer) -> None:
        row = connection.execute(
            "SELECT * FROM node_public_identities WHERE node_id=? AND key_id=?",
            (peer.identity.node_id, peer.identity.key_id),
        ).fetchone()
        if (
            row is None
            or row["public_key_fingerprint"] != peer.identity.public_key_fingerprint
            or row["algorithm"] != peer.identity.algorithm
        ):
            raise ValueError("Bridge peer is not a reviewed node identity")
        if connection.execute(
            "SELECT 1 FROM node_public_identities WHERE node_id=? AND rotated_from_key_id=?",
            (peer.identity.node_id, peer.identity.key_id),
        ).fetchone():
            raise ValueError("Bridge peer key has been superseded")
        if not connection.execute(
            "SELECT 1 FROM node_public_identities WHERE node_id=?", (self.local_node_id,)
        ).fetchone():
            raise ValueError("local node has no reviewed identity")

    async def send_status(self, *, now: datetime | None = None) -> str:
        instant = now or datetime.now(UTC)
        peer = await self._peer(instant)
        with self.store.connect() as connection:
            node = connection.execute(
                "SELECT state FROM nodes WHERE id=?", (self.local_node_id,)
            ).fetchone()
            count = connection.execute(
                "SELECT COUNT(*) FROM workers WHERE node_id=?", (self.local_node_id,)
            ).fetchone()[0]
        payload = NodeStatusPayload(
            node_id=self.local_node_id,
            state=node[0],
            worker_count=count,
            observed_at=utc_time(instant),
            expires_at=utc_time(instant + timedelta(minutes=5)),
        )
        return await self._send(peer, payload, task_id="node-status", now=instant)

    async def send_delegation(self, task_id: str, *, now: datetime | None = None) -> str:
        instant = now or datetime.now(UTC)
        peer = await self._peer(instant)
        with self.store.connect() as connection:
            task = self._task(connection, task_id)
            self._grant(connection, task_id, peer.identity.node_id, "bridge.delegate.send", instant)
        payload = DelegationPayload(
            task_id=task_id,
            task_sha256=self._task_hash(task),
            labels=json.loads(task["labels_json"]),
            observed_at=utc_time(instant),
            expires_at=utc_time(instant + timedelta(minutes=5)),
        )
        return await self._send(peer, payload, task_id=task_id, now=instant)

    async def _send(
        self,
        peer: VerifiedPeer,
        payload: NodeStatusPayload | DelegationPayload,
        *,
        task_id: str,
        now: datetime,
    ) -> str:
        envelope = BridgeEnvelope(
            bridge_version=BRIDGE_VERSION,
            codec_id=IDENTITY_CODEC,
            message_id=f"bridge-{uuid.uuid4()}",
            sender_id=self.local_node_id,
            receiver_id=peer.identity.node_id,
            task_id=task_id,
            payload=StructuredArtifact(
                artifact_id=f"artifact-{uuid.uuid4()}",
                artifact_type=payload.kind,
                content=payload.model_dump(),
            ),
        )
        self._persist(envelope, payload, peer, "outbound", now)
        try:
            await asyncio.wait_for(self.transport.send(envelope.to_bytes()), timeout=2)
        except Exception:
            self._delivery_event(envelope.message_id, "bridgeSendFailed")
            raise ValueError("Bridge send failed; durable outbound intent retained") from None
        self._delivery_event(envelope.message_id, "bridgeSent")
        return envelope.message_id

    async def receive(self, *, now: datetime | None = None) -> str:
        instant = now or datetime.now(UTC)
        try:
            peer = await self._peer(instant)
            encoded = await asyncio.wait_for(self.transport.receive(), timeout=2)
            envelope = BridgeEnvelope.from_bytes(encoded)
            if (
                envelope.sender_id != peer.identity.node_id
                or envelope.receiver_id != self.local_node_id
                or envelope.bridge_version != BRIDGE_VERSION
                or envelope.codec_id != IDENTITY_CODEC
            ):
                raise ValueError("Bridge routing/version mismatch")
            if (
                len(envelope.message_id) > 200
                or any(c.isspace() for c in envelope.message_id)
                or redact_sensitive(envelope.message_id) != envelope.message_id
            ):
                raise ValueError("invalid Bridge message identifier")
            content = envelope.payload.content
            if envelope.payload.artifact_type == "nodeStatus":
                payload = NodeStatusPayload.model_validate(content)
                if payload.node_id != peer.identity.node_id or envelope.task_id != "node-status":
                    raise ValueError("status identity mismatch")
            elif envelope.payload.artifact_type == "delegationProposal":
                payload = DelegationPayload.model_validate(content)
                if payload.task_id != envelope.task_id:
                    raise ValueError("delegation Task identity mismatch")
            else:
                raise ValueError("unsupported Bridge artifact type")
            observed, expires = parse_time(payload.observed_at), parse_time(payload.expires_at)
            if not observed <= instant < expires <= observed + timedelta(minutes=5):
                raise ValueError("Bridge message expired or has invalid time bounds")
            self._persist(envelope, payload, peer, "inbound", instant)
            return envelope.message_id
        except Exception:
            self._delivery_event("untrusted-message", "bridgeMessageRejected")
            raise ValueError(
                "Bridge message rejected by identity/schema/authorization policy"
            ) from None

    def _persist(
        self,
        envelope: BridgeEnvelope,
        payload: NodeStatusPayload | DelegationPayload,
        peer: VerifiedPeer,
        direction: str,
        now: datetime,
    ) -> None:
        safe = type(payload).model_validate(redact_sensitive(payload.model_dump()))
        encoded = safe.model_dump_json()
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.store.transaction() as connection:
            self._identity(connection, peer)
            if isinstance(payload, DelegationPayload):
                task = self._task(connection, payload.task_id)
                self._grant(
                    connection,
                    payload.task_id,
                    peer.identity.node_id,
                    "bridge.delegate.receive" if direction == "inbound" else "bridge.delegate.send",
                    now,
                )
                if self._task_hash(task) != payload.task_sha256:
                    raise ValueError("delegation does not match the reviewed local Task")
            prior = connection.execute(
                "SELECT envelope_sha256 FROM bridge_messages WHERE direction=? AND peer_node_id=? "
                "AND message_id=?",
                (direction, peer.identity.node_id, envelope.message_id),
            ).fetchone()
            if prior:
                if prior[0] != envelope.envelope_sha256:
                    raise ValueError("Bridge message ID replay conflict")
                return
            connection.execute(
                "INSERT INTO "
                "bridge_messages(message_id,direction,peer_node_id,kind,envelope_sha256,"
                "content_sha256,content_json,observed_at,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    envelope.message_id,
                    direction,
                    peer.identity.node_id,
                    payload.kind,
                    envelope.envelope_sha256,
                    digest,
                    encoded,
                    payload.observed_at,
                    utc_time(now),
                ),
            )
            self.store._append_event(
                connection,
                kind="bridgeMessageReceived" if direction == "inbound" else "bridgeQueued",
                severity=EventSeverity.INFO,
                entity_type="bridgeMessage",
                entity_id=envelope.message_id,
                summary="Derived Bridge envelope recorded; no execution authority",
                payload={
                    "peerNodeID": peer.identity.node_id,
                    "kind": payload.kind,
                    "envelopeSHA256": envelope.envelope_sha256,
                },
                actor="node-bridge",
            )

    @staticmethod
    def _task(connection: sqlite3.Connection, task_id: str) -> dict:
        row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError("delegation requires a pre-reviewed local Task")
        labels = json.loads(row["labels_json"])
        if (
            row["permission_class"] != "green"
            or row["code_write_required"] != 0
            or not labels
            or not set(labels) <= {"research", "review"}
            or row["topology"] != "single"
        ):
            raise ValueError("only GREEN read-only RESEARCH/REVIEW Tasks may be delegated")
        return dict(row)

    @staticmethod
    def _task_hash(task: dict) -> str:
        spec = {
            key: task[key]
            for key in (
                "id",
                "title",
                "description",
                "permission_class",
                "code_write_required",
                "labels_json",
                "topology",
            )
        }
        return hashlib.sha256(compact_json(redact_sensitive(spec)).encode()).hexdigest()

    @staticmethod
    def _grant(
        connection: sqlite3.Connection, task_id: str, peer: str, capability: str, now: datetime
    ) -> None:
        grants = connection.execute(
            "SELECT * FROM capability_grants WHERE task_id=? AND subject=? AND capability=? "
            "AND state='active'",
            (task_id, peer, capability),
        ).fetchall()
        for grant in grants:
            issued = parse_time(grant["issued_at"])
            expires = parse_time(grant["expires_at"]) if grant["expires_at"] else None
            # Unknown constraints cannot be ignored; require a time-bounded exact Task grant.
            if (
                issued <= now
                and expires is not None
                and now < expires
                and grant["constraints_json"] == "{}"
            ):
                return
        raise ValueError("no active task-scoped Bridge delegation grant")

    def _delivery_event(self, identity: str, kind: str) -> None:
        with self.store.transaction() as connection:
            self.store._append_event(
                connection,
                kind=kind,
                severity=EventSeverity.INFO,
                entity_type="bridgeMessage",
                entity_id=identity,
                summary=kind,
                payload={},
                actor="node-bridge",
            )
