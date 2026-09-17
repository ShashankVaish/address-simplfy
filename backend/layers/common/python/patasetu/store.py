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

    def query_status(
        self, status: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """GSI-1 equivalent: the review queue, oldest first."""
        rows = [
            dict(item) for item in self._items.values() if item.get("status") == status
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
