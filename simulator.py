import argparse
import hashlib
import random
import signal
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv

# Load PostgreSQL connection settings from .env
load_dotenv(dotenv_path=Path(__file__).resolve().with_name(".env"), override=True)

DEFAULT_CANCEL_RATE = 0.02
DEFAULT_ON_TIME_RATE = 0.85
DEFAULT_MAX_CATCHUP_ORDERS = 5_000
ABSOLUTE_MAX_CATCHUP_ORDERS = 10_000
CANCELLATION_REASON = "LONG_DELIVERY_ESTIMATE"

running = True


@dataclass(frozen=True)
class CatchupOrderOutcome:
    status: str
    estimated_minutes: int
    delivery_minutes: int | None
    cancellation_reason: str | None


def normalize_timestamp(value, db_timezone):
    """Return a database timestamp as an aware UTC datetime."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=db_timezone)
    return value.astimezone(timezone.utc)


def to_database_timestamp(value, data_type, db_timezone):
    """Adapt a UTC timestamp to the PostgreSQL column's declared type."""
    value = normalize_timestamp(value, timezone.utc)
    if data_type == "timestamp with time zone":
        return value
    if data_type == "timestamp without time zone":
        return value.astimezone(db_timezone).replace(tzinfo=None)
    raise ValueError(f"Unsupported timestamp type: {data_type}")


def generate_catchup_timestamps(start, end, interval, max_orders, rng=None):
    """Spread a bounded, deterministic-count sample across an open/closed gap."""
    if interval <= 0:
        raise ValueError("Interval must be positive")
    if max_orders <= 0:
        raise ValueError("Maximum catch-up orders must be positive")
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Catch-up boundaries must be timezone-aware")

    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    span = end - start
    span_microseconds = int(span.total_seconds() * 1_000_000)
    expected_count = int(span.total_seconds() / interval)
    count = min(expected_count, max_orders, span_microseconds)
    if count <= 0:
        return ()

    rng = rng or random
    offsets = rng.sample(range(1, span_microseconds + 1), count)
    return tuple(start + timedelta(microseconds=offset) for offset in sorted(offsets))


def sample_cancelled(cancel_rate, rng=None):
    if not 0 <= cancel_rate <= 1:
        raise ValueError("Cancellation rate must be between 0 and 1")
    rng = rng or random
    return rng.random() < cancel_rate


def deterministic_delivery_minutes(
    order_id, promised_minutes, on_time_rate=DEFAULT_ON_TIME_RATE
):
    """Return a stable duration whose on-time classification uses the configured rate."""
    promised_minutes = int(promised_minutes)
    if promised_minutes <= 0:
        raise ValueError("Promised delivery minutes must be positive")
    if not 0 <= on_time_rate <= 1:
        raise ValueError("On-time rate must be between 0 and 1")

    seed = f"pennlogic-delivery-v2:{order_id}:{promised_minutes}".encode()
    digest = hashlib.sha256(seed).digest()
    roll = int.from_bytes(digest[:8], "big") / (1 << 64)
    variation = int.from_bytes(digest[8:16], "big")
    if roll < on_time_rate:
        earliest_on_time = max(1, promised_minutes - 5)
        return earliest_on_time + variation % (promised_minutes - earliest_on_time + 1)
    return promised_minutes + 1 + variation % 8


def catchup_order_outcome(
    order_id,
    created_at,
    now,
    estimated_minutes,
    will_cancel,
    on_time_rate=DEFAULT_ON_TIME_RATE,
):
    """Choose a stable historical outcome; pending deliveries remain reconstructible."""
    if will_cancel:
        return CatchupOrderOutcome(
            "CANCELLED",
            int(estimated_minutes),
            None,
            CANCELLATION_REASON,
        )

    delivery_minutes = deterministic_delivery_minutes(
        order_id, estimated_minutes, on_time_rate
    )
    delivered_at = created_at + timedelta(minutes=delivery_minutes)
    if delivered_at <= now:
        return CatchupOrderOutcome(
            "DELIVERED", estimated_minutes, delivery_minutes, None
        )
    return CatchupOrderOutcome("IN_PROGRESS", estimated_minutes, None, None)


def stop_simulator(signum, frame):
    global running
    running = False
    print("\nStopping simulator after current order...", flush=True)


signal.signal(signal.SIGINT, stop_simulator)
signal.signal(signal.SIGTERM, stop_simulator)


def create_order(
    conn,
    run_id,
    order_number,
    cancel_rate=DEFAULT_CANCEL_RATE,
    on_time_rate=DEFAULT_ON_TIME_RATE,
):
    """
    Simulate an order lifecycle in PostgreSQL.

    The business delivery duration is simulated. The lifecycle
    is accelerated so the demo can show results in real time.
    """

    merchant_id = random.randint(1, 2000)
    zone_id = random.randint(1, 100)
    order_amount = round(random.uniform(15, 120), 2)

    will_cancel = sample_cancelled(cancel_rate)

    if will_cancel:
        estimated_minutes = random.randint(55, 75)
    else:
        estimated_minutes = random.randint(15, 40)

    # STEP 1: Create the order.
    # PostgreSQL automatically generates order_id.
    with conn.transaction():
        order_id = conn.execute(
            """
            INSERT INTO merchant_portal.orders (
                merchant_id,
                zone_id,
                order_amount,
                order_status,
                estimated_delivery_minutes,
                cancellation_reason,
                simulation_run_id
            )
            VALUES (
                %s, %s, %s, 'IN_PROGRESS', %s, NULL, %s
            )
            RETURNING order_id
            """,
            (
                merchant_id,
                zone_id,
                order_amount,
                estimated_minutes,
                run_id,
            ),
        ).fetchone()[0]

    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] "
        f"ORDER {order_id} CREATED | "
        f"Merchant {merchant_id} | "
        f"Zone {zone_id} | "
        f"${order_amount:.2f} | "
        f"ETA {estimated_minutes} min",
        flush=True,
    )

    # Accelerate the simulated delivery lifecycle.
    time.sleep(1)

    # STEP 2: Complete or cancel the order.
    with conn.transaction():
        if will_cancel:
            conn.execute(
                """
                UPDATE merchant_portal.orders
                SET
                    order_status = 'CANCELLED',
                    cancellation_reason = %s
                WHERE order_id = %s
                """,
                (CANCELLATION_REASON, order_id),
            )

            outcome = "CANCELLED, long delivery estimate"

        else:
            delivery_minutes = deterministic_delivery_minutes(
                order_id, estimated_minutes, on_time_rate
            )

            promised_minutes = estimated_minutes
            on_time = delivery_minutes <= promised_minutes

            conn.execute(
                """
                UPDATE merchant_portal.orders
                SET order_status = 'DELIVERED'
                WHERE order_id = %s
                """,
                (order_id,),
            )

            # PostgreSQL automatically generates delivery_id.
            conn.execute(
                """
                INSERT INTO merchant_portal.deliveries (
                    order_id,
                    delivery_minutes,
                    promised_minutes,
                    delivered_at,
                    on_time
                )
                VALUES (
                    %s, %s, %s, NOW(), %s
                )
                """,
                (
                    order_id,
                    delivery_minutes,
                    promised_minutes,
                    on_time,
                ),
            )

            outcome = f"DELIVERED in {delivery_minutes} simulated minutes"

    print(
        f"    ORDER {order_id} {outcome}",
        flush=True,
    )


def read_catchup_context(conn):
    timestamp_types = dict(conn.execute("""
            SELECT table_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'merchant_portal'
              AND (table_name, column_name) IN (
                  ('orders', 'created_at'),
                  ('deliveries', 'delivered_at')
              )
            """).fetchall())
    if set(timestamp_types) != {"orders", "deliveries"}:
        raise RuntimeError(
            "Catchup requires orders.created_at and deliveries.delivered_at columns"
        )

    timezone_name = conn.execute("SELECT current_setting('TimeZone')").fetchone()[0]
    db_timezone = ZoneInfo(timezone_name)
    latest_raw, now_raw = conn.execute("""
        SELECT
            (SELECT max(created_at)
             FROM merchant_portal.orders
             WHERE simulation_run_id IS NOT NULL),
            CURRENT_TIMESTAMP
        """).fetchone()
    latest = (
        None if latest_raw is None else normalize_timestamp(latest_raw, db_timezone)
    )
    now = normalize_timestamp(now_raw, db_timezone)
    return latest, now, db_timezone, timestamp_types


def insert_catchup_order(
    conn,
    run_id,
    created_at,
    now,
    db_timezone,
    timestamp_types,
    cancel_rate,
    on_time_rate=DEFAULT_ON_TIME_RATE,
):
    will_cancel = sample_cancelled(cancel_rate)
    estimated_minutes = (
        random.randint(55, 75) if will_cancel else random.randint(15, 40)
    )
    merchant_id = random.randint(1, 2000)
    zone_id = random.randint(1, 100)
    order_amount = round(random.uniform(15, 120), 2)
    created_at_db = to_database_timestamp(
        created_at, timestamp_types["orders"], db_timezone
    )

    with conn.transaction():
        order_id = conn.execute(
            """
            INSERT INTO merchant_portal.orders (
                merchant_id,
                zone_id,
                order_amount,
                order_status,
                estimated_delivery_minutes,
                cancellation_reason,
                simulation_run_id,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING order_id
            """,
            (
                merchant_id,
                zone_id,
                order_amount,
                "CANCELLED" if will_cancel else "IN_PROGRESS",
                estimated_minutes,
                CANCELLATION_REASON if will_cancel else None,
                run_id,
                created_at_db,
            ),
        ).fetchone()[0]

        outcome = catchup_order_outcome(
            order_id,
            created_at,
            now,
            estimated_minutes,
            will_cancel,
            on_time_rate,
        )
        if outcome.status == "DELIVERED":
            delivered_at = created_at + timedelta(minutes=outcome.delivery_minutes)
            delivered_at_db = to_database_timestamp(
                delivered_at, timestamp_types["deliveries"], db_timezone
            )
            on_time = outcome.delivery_minutes <= outcome.estimated_minutes
            conn.execute(
                """
                UPDATE merchant_portal.orders
                SET order_status = 'DELIVERED'
                WHERE order_id = %s AND order_status = 'IN_PROGRESS'
                """,
                (order_id,),
            )
            conn.execute(
                """
                INSERT INTO merchant_portal.deliveries (
                    order_id,
                    delivery_minutes,
                    promised_minutes,
                    delivered_at,
                    on_time
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    order_id,
                    outcome.delivery_minutes,
                    outcome.estimated_minutes,
                    delivered_at_db,
                    on_time,
                ),
            )
    return outcome.status


def fetch_pending_orders(conn, lock=False):
    lock_clause = "FOR UPDATE OF o SKIP LOCKED" if lock else ""
    return conn.execute(f"""
        SELECT
            o.order_id,
            o.created_at,
            o.estimated_delivery_minutes,
            EXISTS (
                SELECT 1
                FROM merchant_portal.deliveries AS d
                WHERE d.order_id = o.order_id
            ) AS has_delivery
        FROM merchant_portal.orders AS o
        WHERE o.simulation_run_id IS NOT NULL
          AND o.order_status = 'IN_PROGRESS'
        ORDER BY o.order_id
        {lock_clause}
        """).fetchall()


def planned_delivery(
    created_at,
    order_id,
    estimated_minutes,
    db_timezone,
    on_time_rate=DEFAULT_ON_TIME_RATE,
):
    created_at = normalize_timestamp(created_at, db_timezone)
    delivery_minutes = deterministic_delivery_minutes(
        order_id, estimated_minutes, on_time_rate
    )
    return created_at + timedelta(minutes=delivery_minutes), delivery_minutes


def complete_pending_order(
    conn,
    order_id,
    now,
    db_timezone,
    timestamp_types,
    on_time_rate=DEFAULT_ON_TIME_RATE,
):
    """Lock, recheck, and atomically complete one eligible simulator order."""
    with conn.transaction():
        row = conn.execute(
            """
            SELECT
                o.created_at,
                o.estimated_delivery_minutes,
                EXISTS (
                    SELECT 1
                    FROM merchant_portal.deliveries AS d
                    WHERE d.order_id = o.order_id
                ) AS has_delivery
            FROM merchant_portal.orders AS o
            WHERE o.order_id = %s
              AND o.simulation_run_id IS NOT NULL
              AND o.order_status = 'IN_PROGRESS'
            FOR UPDATE OF o SKIP LOCKED
            """,
            (order_id,),
        ).fetchone()
        if row is None:
            return "NOT_PENDING"

        created_at, estimated_minutes, has_delivery = row
        if has_delivery:
            return "HAS_DELIVERY"

        delivered_at, delivery_minutes = planned_delivery(
            created_at, order_id, estimated_minutes, db_timezone, on_time_rate
        )
        if delivered_at > now:
            return "NOT_DUE"

        delivered_at_db = to_database_timestamp(
            delivered_at, timestamp_types["deliveries"], db_timezone
        )
        on_time = delivery_minutes <= estimated_minutes
        inserted = conn.execute(
            """
            INSERT INTO merchant_portal.deliveries (
                order_id,
                delivery_minutes,
                promised_minutes,
                delivered_at,
                on_time
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (order_id) DO NOTHING
            RETURNING order_id
            """,
            (
                order_id,
                delivery_minutes,
                estimated_minutes,
                delivered_at_db,
                on_time,
            ),
        ).fetchone()
        if inserted is None:
            return "HAS_DELIVERY"

        updated = conn.execute(
            """
            UPDATE merchant_portal.orders
            SET order_status = 'DELIVERED'
            WHERE order_id = %s
              AND simulation_run_id IS NOT NULL
              AND order_status = 'IN_PROGRESS'
            RETURNING order_id
            """,
            (order_id,),
        ).fetchone()
        if updated is None:
            raise RuntimeError("Pending order changed while its completion was locked")
    return "DELIVERED"


def run_complete_pending(args):
    with psycopg.connect(autocommit=not args.dry_run) as conn:
        if args.dry_run:
            conn.execute("SET TRANSACTION READ ONLY")
        _, now, db_timezone, timestamp_types = read_catchup_context(conn)
        pending_rows = fetch_pending_orders(conn)

        eligible_ids = []
        pending_with_delivery = 0
        not_due = 0
        for order_id, created_at, estimated_minutes, has_delivery in pending_rows:
            if has_delivery:
                pending_with_delivery += 1
                continue
            delivered_at, _ = planned_delivery(
                created_at,
                order_id,
                estimated_minutes,
                db_timezone,
                args.on_time_rate,
            )
            if delivered_at <= now:
                eligible_ids.append(order_id)
            else:
                not_due += 1

        print(f"Simulator IN_PROGRESS orders: {len(pending_rows):,}")
        print(f"Eligible for completion: {len(eligible_ids):,}")
        print(f"Not yet due: {not_due:,}")
        print(f"Already have a delivery row: {pending_with_delivery:,}")
        print(
            "Delivery duration policy: stable SHA-256 of order_id, estimate, "
            f"and on-time rate ({args.on_time_rate:.1%}; v2)."
        )
        if args.dry_run:
            print("Dry run only; no orders or deliveries were written.")
            return

        completed = 0
        skipped = 0
        for order_id in eligible_ids:
            result = complete_pending_order(
                conn,
                order_id,
                now,
                db_timezone,
                timestamp_types,
                args.on_time_rate,
            )
            if result == "DELIVERED":
                completed += 1
            else:
                skipped += 1
        print(
            f"Completion pass finished: {completed:,} delivered; "
            f"{skipped:,} skipped after recheck."
        )


def run_catchup(args, run_id):
    with psycopg.connect(autocommit=not args.dry_run) as conn:
        if args.dry_run:
            conn.execute("SET TRANSACTION READ ONLY")
        latest, now, db_timezone, timestamp_types = read_catchup_context(conn)
        if latest is None:
            print(
                "No prior simulator orders found; catchup skipped to avoid "
                "backfilling the historical dataset."
            )
            return
        if latest >= now:
            print(
                "The latest simulator order is already at or beyond database time; no gap found."
            )
            return

        timestamps = generate_catchup_timestamps(
            latest,
            now,
            args.interval,
            args.max_catchup_orders,
        )
        if not timestamps:
            print(
                "No catchup gap found at the configured interval; no orders generated."
            )
            return

        expected = int((now - latest).total_seconds() / args.interval)
        print("Catchup plan")
        print(f"  Gap: {latest.isoformat()} → {now.isoformat()}")
        print(f"  Orders: {len(timestamps):,} (interval target {args.interval:g}s)")
        print(
            f"  Expected cancellations: approximately {len(timestamps) * args.cancel_rate:.1f}"
        )
        print(f"  On-time delivery target: {args.on_time_rate:.1%}")
        if expected > args.max_catchup_orders:
            print(f"  Maximum applied: {args.max_catchup_orders:,} orders")
        if args.dry_run:
            print("Dry run only; no orders or deliveries were written.")
            return

        status_counts = {"CANCELLED": 0, "DELIVERED": 0, "IN_PROGRESS": 0}
        try:
            for created_at in timestamps:
                status = insert_catchup_order(
                    conn,
                    run_id,
                    created_at,
                    now,
                    db_timezone,
                    timestamp_types,
                    args.cancel_rate,
                    args.on_time_rate,
                )
                status_counts[status] += 1
        except Exception as exc:
            completed = sum(status_counts.values())
            print(
                f"Catchup stopped after {completed:,} of {len(timestamps):,} orders "
                f"({type(exc).__name__}); committed rows were preserved."
            )
            return
        print(
            "Catchup complete: "
            f"{len(timestamps):,} orders; "
            f"{status_counts['DELIVERED']:,} delivered, "
            f"{status_counts['CANCELLED']:,} canceled, "
            f"{status_counts['IN_PROGRESS']:,} still in progress."
        )


def main():
    parser = argparse.ArgumentParser(
        description="PennLogic Merchant Portal Order Simulator"
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=10,
        help="Seconds between new orders (default: 10)",
    )

    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="Number of orders to generate, 0 means unlimited",
    )
    operation_group = parser.add_mutually_exclusive_group()
    operation_group.add_argument(
        "--catchup",
        action="store_true",
        help="Backfill the gap after the latest simulator order",
    )
    operation_group.add_argument(
        "--complete-pending",
        action="store_true",
        help="Complete eligible simulator-generated IN_PROGRESS orders",
    )
    parser.add_argument(
        "--cancel-rate",
        type=float,
        default=DEFAULT_CANCEL_RATE,
        help="Cancellation probability from 0 to 1 (default: 0.02)",
    )
    parser.add_argument(
        "--on-time-rate",
        type=float,
        default=DEFAULT_ON_TIME_RATE,
        help="Probability of an on-time delivery from 0 to 1 (default: 0.85)",
    )
    parser.add_argument(
        "--max-catchup-orders",
        type=int,
        default=DEFAULT_MAX_CATCHUP_ORDERS,
        help="Catchup order cap (default: 5000; hard maximum: 10000)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview catchup without writing orders or deliveries",
    )

    args = parser.parse_args()

    if args.interval <= 0 or args.count < 0:
        parser.error("Interval must be positive and count cannot be negative")
    if not 0 <= args.cancel_rate <= 1:
        parser.error("Cancellation rate must be between 0 and 1")
    if not 0 <= args.on_time_rate <= 1:
        parser.error("On-time rate must be between 0 and 1")
    if (
        args.max_catchup_orders <= 0
        or args.max_catchup_orders > ABSOLUTE_MAX_CATCHUP_ORDERS
    ):
        parser.error(
            f"Maximum catchup orders must be between 1 and {ABSOLUTE_MAX_CATCHUP_ORDERS}"
        )
    if args.dry_run and not (args.catchup or args.complete_pending):
        parser.error("--dry-run requires --catchup or --complete-pending")
    if (args.catchup or args.complete_pending) and args.count:
        parser.error(
            "--count cannot be combined with catchup or complete-pending modes"
        )

    run_id = str(uuid.uuid4())

    if args.catchup:
        try:
            run_catchup(args, run_id)
            return
        except Exception as exc:
            print(
                f"Catchup could not run ({type(exc).__name__}); "
                "check the PostgreSQL connection and schema configuration."
            )
            return

    if args.complete_pending:
        try:
            run_complete_pending(args)
        except Exception as exc:
            print(
                f"Pending-order completion could not run ({type(exc).__name__}); "
                "check the PostgreSQL connection and schema configuration."
            )
        return

    print("\nPennLogic Merchant Order Simulator")
    print(f"Simulation run: {run_id}")
    print(f"Order interval: {args.interval} seconds")
    print(f"Order limit: {args.count or 'unlimited'}")
    print("Press Control+C to stop.\n", flush=True)

    with psycopg.connect(autocommit=True) as conn:
        order_number = 0

        while running and (args.count == 0 or order_number < args.count):
            order_number += 1
            start = time.monotonic()

            create_order(
                conn,
                run_id,
                order_number,
                args.cancel_rate,
                args.on_time_rate,
            )

            remaining = args.interval - (time.monotonic() - start)

            if remaining > 0 and running:
                time.sleep(remaining)

    print(f"\nSimulation complete. Orders generated: {order_number}")
    print(f"Simulation run ID: {run_id}")


if __name__ == "__main__":
    main()
