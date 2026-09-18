"""DynamoDB access, and its in-memory twin.

One table, five access patterns, no scans:

    ADDR#<sha256>   RESOLUTION      cached resolution      <- the cost saver
    ORDER#<id>      META            order and status
    ORDER#<id>      EVENT#<ts>      audit trail, time-sorted
    DIGIPIN#<cell>  NOTE#<ts>       rider access notes
    PIN#<pincode>   LOCALITY#<name> gazetteer rows

The single-table design is not decoration: the review queue is one query on
GSI-1 (`status`, `created_at`), and a per-order timeline is one query on the
partition key. Anything that needed a scan would be a design error, because a
scan over a demo table is fast and a scan over a real one is a bill.
"""

from __future__ import annotations

import time
from typing import Any


def address_pk(cache_key: str) -> str:
    return f"ADDR#{cache_key}"


def order_pk(order_id: str) -> str:
    return f"ORDER#{order_id}"


def digipin_pk(cell: str) -> str:
    return f"DIGIPIN#{cell}"


def pincode_pk(pincode: str) -> str:
    return f"PIN#{pincode}"


def ttl_at(days: int) -> int:
    """Absolute epoch seconds for a TTL `days` from now."""
    return int(time.time()) + days * 86_400


class InMemoryStore:
    """Dict-backed store for local mode and tests.

    Implements the same key structure and the same prefix-query semantics as
    DynamoDB, so code written against it behaves identically against the real
    table. Notably it sorts by sort key, because `EVENT#<ts>` ordering is what
    makes the audit trail a timeline.
    """

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict[str, Any]] = {}

    def get(self, pk: str, sk: str) -> dict[str, Any] | None:
        item = self._items.get((pk, sk))
        if item is None:
            return None
        # Honour TTL, so cache-expiry behaviour is testable without waiting.
        ttl = item.get("ttl")
        if ttl is not None and ttl < time.time():
            del self._items[(pk, sk)]
            return None
        return dict(item)

    def put(
        self, pk: str, sk: str, item: dict[str, Any], *, ttl_days: int | None = None
    ) -> None:
        record = {**item, "PK": pk, "SK": sk}
        if ttl_days is not None:
            record["ttl"] = ttl_at(ttl_days)
        self._items[(pk, sk)] = record

    def query_prefix(
        self, pk: str, sk_prefix: str = "", limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = [
            dict(item)
            for (item_pk, item_sk), item in self._items.items()
            if item_pk == pk and item_sk.startswith(sk_prefix)
        ]
        rows.sort(key=lambda r: r["SK"])
        return rows[:limit]

    def query_status(self, status: str, limit: int = 50) -> list[dict[str, Any]]:
        """GSI-1 equivalent: the review queue, oldest first.

        A DynamoDB GSI is sparse: an item is indexed only if it carries *both*
        key attributes. Audit EVENT rows have a `status` but no `created_at`,
        so the real index never sees them -- and this emulation must not
        either, or the local queue shows timeline events as if they were
        cases.
        """
        rows = [
            dict(item)
            for item in self._items.values()
            if item.get("status") == status and item.get("created_at")
        ]
        rows.sort(key=lambda r: r.get("created_at", ""))
        return rows[:limit]

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


class DynamoStore:
    """The real table."""

    def __init__(self, table_name: str, region: str) -> None:
        self.table_name = table_name
        self.region = region
        self._table: Any = None

    @property
    def table(self) -> Any:
        if self._table is None:
            import boto3

            self._table = boto3.resource("dynamodb", region_name=self.region).Table(
                self.table_name
            )
        return self._table

    def get(self, pk: str, sk: str) -> dict[str, Any] | None:
        try:
            response = self.table.get_item(Key={"PK": pk, "SK": sk})
        except Exception:
            # A cache read is never worth failing a request for. A miss costs
            # one pipeline run; an exception costs the customer their answer.
            return None
        return response.get("Item")

    def put(
        self, pk: str, sk: str, item: dict[str, Any], *, ttl_days: int | None = None
    ) -> None:
        record = {**item, "PK": pk, "SK": sk}
        if ttl_days is not None:
            record["ttl"] = ttl_at(ttl_days)
        try:
            self.table.put_item(Item=_to_dynamo(record))
        except Exception:
            # Same reasoning: a failed cache write must not fail the response.
            return

    def query_prefix(
        self, pk: str, sk_prefix: str = "", limit: int = 50
    ) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        condition = Key("PK").eq(pk)
        if sk_prefix:
            condition = condition & Key("SK").begins_with(sk_prefix)
        try:
            response = self.table.query(KeyConditionExpression=condition, Limit=limit)
        except Exception:
            return []
        return response.get("Items", [])

    def query_status(self, status: str, limit: int = 50) -> list[dict[str, Any]]:
        """The review queue, via GSI-1. One query, oldest first, no scan."""
        from boto3.dynamodb.conditions import Key

        try:
            response = self.table.query(
                IndexName="GSI-1",
                KeyConditionExpression=Key("status").eq(status),
                ScanIndexForward=True,
                Limit=limit,
            )
        except Exception:
            return []
        return response.get("Items", [])


def _to_dynamo(value: Any) -> Any:
    """Convert floats to Decimal, recursively.

    DynamoDB rejects Python floats outright. Every confidence score and every
    coordinate in this system is a float, so without this every write fails --
    and because we swallow write errors to protect the response, it would fail
    *silently* and the cache would simply never work.
    """
    from decimal import Decimal

    if isinstance(value, float):
        # Via str, not Decimal(float): Decimal(0.1) carries the full binary
        # expansion and DynamoDB rejects numbers with more than 38 digits.
        return Decimal(str(round(value, 8)))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_dynamo(v) for v in value]
    return value


def from_dynamo(value: Any) -> Any:
    """Convert Decimal back to float, recursively."""
    from decimal import Decimal

    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_dynamo(v) for v in value]
    return value


# --- audit trail ---------------------------------------------------------------


def record_resolution(
    kv: Any, *, order_id: str, resolution: dict[str, Any], correlation_id: str
) -> None:
    """Write the order row and an audit event for one resolution (NFR-22).

    Two items: `ORDER#<id> / META` carries the current status and is what
    GSI-1 indexes for the review queue; `ORDER#<id> / EVENT#<ts>` is the
    append-only timeline. The summary an operator sees is built from the
    *resolved* fields, never from the raw request body, so a phone number the
    customer typed cannot reach the queue screen.
    """
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat()
    status = resolution.get("status", "NEEDS_INFO")
    structured = resolution.get("structured") or {}
    summary = ", ".join(
        str(structured[k])
        for k in ("building", "locality", "city", "pincode")
        if structured.get(k)
    )
    meta = {
        "order_id": order_id,
        "status": status,
        "created_at": now,
        "updated_at": now,
        "confidence": resolution.get("confidence"),
        "summary": summary,
        "structured": structured,
        "clarification": resolution.get("clarification"),
        "alternatives": resolution.get("alternatives") or [],
        "digipin": resolution.get("digipin"),
        "geo": resolution.get("geo"),
        "evidence": resolution.get("evidence") or [],
        "correlation_id": correlation_id,
    }
    existing = kv.get(order_pk(order_id), "META")
    if existing and existing.get("created_at"):
        # Keep the original arrival time: the queue is oldest-first by when the
        # case first needed a human, not by its latest re-resolution.
        meta["created_at"] = existing["created_at"]
    kv.put(order_pk(order_id), "META", meta)
    kv.put(
        order_pk(order_id),
        f"EVENT#{now}",
        {
            "event": "resolved",
            "new_status": status,
            "confidence": resolution.get("confidence"),
            "correlation_id": correlation_id,
        },
    )


def record_feedback(
    kv: Any,
    *,
    order_id: str,
    action: str,
    actor: str,
    edits: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Apply an operator decision and append it to the timeline.

    Returns the updated order, or None if the order does not exist.
    """
    from datetime import UTC, datetime

    meta = kv.get(order_pk(order_id), "META")
    if meta is None:
        return None
    now = datetime.now(UTC).isoformat()
    new_status = "RESOLVED" if action in ("approve", "edit") else "REJECTED"
    meta = {k: v for k, v in meta.items() if k not in ("PK", "SK")}
    meta.update({"status": new_status, "updated_at": now, "reviewed_by": actor})
    if edits:
        meta["structured"] = {**(meta.get("structured") or {}), **edits}
        meta["edits"] = edits
    kv.put(order_pk(order_id), "META", meta)
    kv.put(
        order_pk(order_id),
        f"EVENT#{now}",
        {
            "event": f"operator_{action}",
            "actor": actor,
            "edits": edits or {},
            "new_status": new_status,
        },
    )
    return meta
