import random
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import simulator


class FakeResult:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeCompletionConnection:
    def __init__(self, order_id, created_at, estimated_minutes, run_id="run-1"):
        self.order = {
            "order_id": order_id,
            "created_at": created_at,
            "estimated_minutes": estimated_minutes,
            "simulation_run_id": run_id,
            "status": "IN_PROGRESS",
        }
        self.deliveries = {}

    @contextmanager
    def transaction(self):
        yield

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        if normalized.startswith("SELECT o.created_at"):
            order_id = params[0]
            if (
                order_id != self.order["order_id"]
                or self.order["simulation_run_id"] is None
                or self.order["status"] != "IN_PROGRESS"
            ):
                return FakeResult()
            return FakeResult(
                (
                    self.order["created_at"],
                    self.order["estimated_minutes"],
                    order_id in self.deliveries,
                )
            )

        if normalized.startswith("INSERT INTO merchant_portal.deliveries"):
            order_id = params[0]
            if order_id in self.deliveries:
                return FakeResult()
            self.deliveries[order_id] = {
                "delivery_minutes": params[1],
                "promised_minutes": params[2],
                "delivered_at": params[3],
                "on_time": params[4],
            }
            return FakeResult((order_id,))

        if normalized.startswith("UPDATE merchant_portal.orders"):
            order_id = params[0]
            if (
                order_id == self.order["order_id"]
                and self.order["simulation_run_id"] is not None
                and self.order["status"] == "IN_PROGRESS"
            ):
                self.order["status"] = "DELIVERED"
                return FakeResult((order_id,))
            return FakeResult()

        raise AssertionError(f"Unexpected SQL in fake connection: {normalized}")


class SimulatorCatchupTests(unittest.TestCase):
    def test_default_cancel_probability_is_about_two_percent(self):
        rng = random.Random(20261119)
        cancellations = sum(
            simulator.sample_cancelled(0.02, rng) for _ in range(50_000)
        )
        self.assertAlmostEqual(cancellations / 50_000, 0.02, delta=0.002)

    def test_cancel_probability_boundaries(self):
        rng = random.Random(7)
        self.assertFalse(any(simulator.sample_cancelled(0, rng) for _ in range(100)))
        self.assertTrue(all(simulator.sample_cancelled(1, rng) for _ in range(100)))
        with self.assertRaises(ValueError):
            simulator.sample_cancelled(1.01, rng)

    def test_catchup_timestamps_are_unique_and_inside_gap(self):
        start = datetime(2026, 11, 19, 10, tzinfo=timezone.utc)
        end = start + timedelta(hours=1)
        timestamps = simulator.generate_catchup_timestamps(
            start, end, interval=2, max_orders=5_000, rng=random.Random(10)
        )

        self.assertEqual(len(timestamps), 1_800)
        self.assertEqual(len(set(timestamps)), len(timestamps))
        self.assertTrue(all(start < timestamp <= end for timestamp in timestamps))
        minute_counts = [
            sum(start + timedelta(minutes=i) < t <= start + timedelta(minutes=i + 1)
                for t in timestamps)
            for i in range(60)
        ]
        self.assertGreater(max(minute_counts), min(minute_counts))

    def test_catchup_count_is_capped_and_short_gaps_are_skipped(self):
        start = datetime(2026, 11, 19, 10, tzinfo=timezone.utc)
        one_day_later = start + timedelta(days=1)
        capped = simulator.generate_catchup_timestamps(
            start, one_day_later, interval=1, max_orders=5_000,
            rng=random.Random(11),
        )
        self.assertEqual(len(capped), 5_000)
        self.assertEqual(
            simulator.generate_catchup_timestamps(
                start, start + timedelta(seconds=1), interval=2,
                max_orders=5_000, rng=random.Random(12),
            ),
            (),
        )

    def test_naive_and_aware_database_timestamps_normalize_to_utc(self):
        pacific = ZoneInfo("America/Los_Angeles")
        naive = datetime(2026, 11, 19, 10, 0)
        expected = datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(simulator.normalize_timestamp(naive, pacific), expected)

        aware = datetime(2026, 11, 19, 10, 0, tzinfo=pacific)
        self.assertEqual(simulator.normalize_timestamp(aware, pacific), expected)

    def test_database_timestamp_adaptation_matches_column_type(self):
        pacific = ZoneInfo("America/Los_Angeles")
        value = datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(
            simulator.to_database_timestamp(
                value, "timestamp without time zone", pacific
            ),
            datetime(2026, 11, 19, 10, 0),
        )
        self.assertEqual(
            simulator.to_database_timestamp(
                value, "timestamp with time zone", pacific
            ),
            value,
        )

    def test_catchup_never_adds_a_future_delivery(self):
        now = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
        recent_order = simulator.catchup_order_outcome(
            1001, now - timedelta(minutes=1), now, 30, False
        )
        self.assertEqual(recent_order.status, "IN_PROGRESS")
        self.assertIsNone(recent_order.delivery_minutes)

        old_order = simulator.catchup_order_outcome(
            1002, now - timedelta(hours=2), now, 30, False
        )
        self.assertEqual(old_order.status, "DELIVERED")
        delivered_at = now - timedelta(hours=2) + timedelta(
            minutes=old_order.delivery_minutes
        )
        self.assertLessEqual(delivered_at, now)

    def test_canceled_catchup_order_has_reason_and_no_delivery(self):
        now = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
        outcome = simulator.catchup_order_outcome(
            1003, now - timedelta(hours=2), now, 60, True
        )
        self.assertEqual(outcome.status, "CANCELLED")
        self.assertEqual(outcome.cancellation_reason, "LONG_DELIVERY_ESTIMATE")
        self.assertIsNone(outcome.delivery_minutes)

    def test_deterministic_delivery_duration_varies_around_estimate(self):
        for order_id, estimated in [(1, 20), (2, 30), (54321, 40)]:
            first = simulator.deterministic_delivery_minutes(order_id, estimated, 0.85)
            second = simulator.deterministic_delivery_minutes(order_id, estimated, 0.85)
            self.assertEqual(first, second)
            self.assertGreaterEqual(first, max(1, estimated - 5))
            self.assertLessEqual(first, estimated + 8)

    def test_on_time_rate_controls_realistic_duration_classification(self):
        promised = 30
        for order_id in range(1, 2_001):
            duration = simulator.deterministic_delivery_minutes(
                order_id, promised, 0.85
            )
            on_time = duration <= promised
            if on_time:
                self.assertGreaterEqual(duration, max(1, promised - 5))
                self.assertLessEqual(duration, promised)
            else:
                self.assertGreater(duration, promised)
                self.assertLessEqual(duration, promised + 8)

        self.assertGreater(
            simulator.deterministic_delivery_minutes(100, promised, 0), promised
        )
        self.assertLessEqual(
            simulator.deterministic_delivery_minutes(100, promised, 1), promised
        )

    def test_on_time_rate_is_approximately_85_percent_over_large_sample(self):
        promised = 30
        sample_size = 50_000
        on_time = sum(
            simulator.deterministic_delivery_minutes(order_id, promised, 0.85)
            <= promised
            for order_id in range(1, sample_size + 1)
        )
        self.assertAlmostEqual(on_time / sample_size, 0.85, delta=0.01)

    def test_catchup_and_pending_completion_share_delivery_duration(self):
        order_id = 98765
        promised = 32
        on_time_rate = 0.85
        now = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
        created = now - timedelta(minutes=1)
        outcome = simulator.catchup_order_outcome(
            order_id, created, now, promised, False, on_time_rate
        )
        intended_at, pending_duration = simulator.planned_delivery(
            created, order_id, promised, timezone.utc, on_time_rate
        )

        self.assertEqual(outcome.status, "IN_PROGRESS")
        self.assertIsNone(outcome.delivery_minutes)
        self.assertEqual(
            pending_duration,
            simulator.deterministic_delivery_minutes(
                order_id, promised, on_time_rate
            ),
        )
        self.assertEqual(
            intended_at, created + timedelta(minutes=pending_duration)
        )

    def test_completion_dry_eligibility_respects_intended_timestamp(self):
        created = datetime(2026, 11, 19, 10, tzinfo=timezone.utc)
        order_id = 456
        duration = simulator.deterministic_delivery_minutes(order_id, 30)
        intended = created + timedelta(minutes=duration)
        before = intended - timedelta(microseconds=1)
        after = intended + timedelta(microseconds=1)

        planned_at, planned_minutes = simulator.planned_delivery(
            created, order_id, 30, timezone.utc
        )
        self.assertEqual(planned_minutes, duration)
        self.assertEqual(planned_at, intended)
        self.assertFalse(planned_at <= before)
        self.assertTrue(planned_at <= after)

    def test_completion_is_idempotent_and_delivery_is_consistent(self):
        db_timezone = timezone.utc
        created = datetime(2026, 11, 19, 8, tzinfo=timezone.utc)
        order_id = 457
        estimated = 30
        now = created + timedelta(hours=2)
        conn = FakeCompletionConnection(order_id, created, estimated)
        timestamp_types = {
            "orders": "timestamp with time zone",
            "deliveries": "timestamp with time zone",
        }

        first = simulator.complete_pending_order(
            conn, order_id, now, db_timezone, timestamp_types
        )
        second = simulator.complete_pending_order(
            conn, order_id, now, db_timezone, timestamp_types
        )

        self.assertEqual(first, "DELIVERED")
        self.assertEqual(second, "NOT_PENDING")
        self.assertEqual(conn.order["status"], "DELIVERED")
        self.assertEqual(len(conn.deliveries), 1)
        delivery = conn.deliveries[order_id]
        expected_minutes = simulator.deterministic_delivery_minutes(order_id, estimated)
        self.assertEqual(delivery["delivery_minutes"], expected_minutes)
        self.assertEqual(delivery["promised_minutes"], estimated)
        self.assertEqual(delivery["delivered_at"], created + timedelta(minutes=expected_minutes))
        self.assertLessEqual(delivery["delivered_at"], now)
        self.assertEqual(delivery["on_time"], expected_minutes <= estimated)

    def test_completion_leaves_recent_or_unrelated_orders_untouched(self):
        db_timezone = timezone.utc
        created = datetime(2026, 11, 19, 11, 59, tzinfo=timezone.utc)
        order_id = 458
        conn = FakeCompletionConnection(order_id, created, 30)
        timestamp_types = {
            "orders": "timestamp with time zone",
            "deliveries": "timestamp with time zone",
        }
        now = created + timedelta(minutes=1)

        result = simulator.complete_pending_order(
            conn, order_id, now, db_timezone, timestamp_types
        )
        self.assertEqual(result, "NOT_DUE")
        self.assertEqual(conn.order["status"], "IN_PROGRESS")
        self.assertEqual(conn.deliveries, {})

        conn.order["simulation_run_id"] = None
        result = simulator.complete_pending_order(
            conn, order_id, now + timedelta(hours=2), db_timezone, timestamp_types
        )
        self.assertEqual(result, "NOT_PENDING")
        self.assertEqual(conn.order["status"], "IN_PROGRESS")
        self.assertEqual(conn.deliveries, {})


if __name__ == "__main__":
    unittest.main()
