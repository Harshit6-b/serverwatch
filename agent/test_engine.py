"""Checks the detection rules from Chapter 5. Run:  python -m unittest test_engine"""
import unittest

from engine import CRITICAL, OK, UNREACHABLE, WARNING, Engine


def sample(cpu=10, mem=50, disk=40, net=1, svc="Running"):
    return {"host": "T", "cpu": cpu, "mem": mem, "net": net,
            "disks": [{"id": "C:", "free": disk, "size_gb": 100}], "services": {"Spooler": svc}}


class Rules(unittest.TestCase):
    def setUp(self):
        self.e = Engine(["Spooler"], consec=3, hyst=5)

    def feed(self, *samples):
        for s in samples:
            self.e.ingest(s)
        return self.e.snapshot()

    def test_single_spike_is_ignored(self):
        s = self.feed(sample(cpu=97), sample(cpu=12), sample(cpu=11))
        self.assertEqual(s["overall"], OK)

    def test_three_breaches_escalate(self):
        s = self.feed(sample(cpu=95), sample(cpu=95))
        self.assertEqual(s["overall"], OK)
        self.assertEqual(s["metrics"]["cpu"]["streak"], 2)
        s = self.feed(sample(cpu=95))
        self.assertEqual(s["overall"], CRITICAL)

    def test_mixed_breaches_take_lowest_level(self):
        s = self.feed(sample(cpu=95), sample(cpu=80), sample(cpu=95))
        self.assertEqual(s["metrics"]["cpu"]["level"], WARNING)

    def test_straight_to_critical(self):
        s = self.feed(sample(cpu=80), sample(cpu=95), sample(cpu=95), sample(cpu=95))
        self.assertEqual(s["metrics"]["cpu"]["level"], CRITICAL)   # not stuck at WARNING

    def test_hysteresis(self):
        self.feed(*[sample(cpu=95)] * 3)
        s = self.feed(sample(cpu=87))           # below 90 but within 5-point margin
        self.assertEqual(s["metrics"]["cpu"]["level"], CRITICAL)
        s = self.feed(sample(cpu=80))           # 75 <= 80 < 85  -> warning (sticky)
        self.assertEqual(s["metrics"]["cpu"]["level"], WARNING)
        s = self.feed(sample(cpu=60))
        self.assertEqual(s["metrics"]["cpu"]["level"], OK)

    def test_disk_low_is_bad(self):
        s = self.feed(*[sample(disk=4)] * 3)
        self.assertEqual(s["metrics"]["disk"]["level"], CRITICAL)

    def test_service_stop_is_immediate(self):
        s = self.feed(sample(svc="Stopped"))
        self.assertEqual(s["overall"], CRITICAL)
        self.e.service_event("Spooler", "Running")
        self.assertEqual(self.e.snapshot()["overall"], OK)

    def test_unreachable(self):
        self.feed(sample())
        self.e.ingest_failure(ConnectionError("RPC server is unavailable"))
        s = self.e.snapshot()
        self.assertEqual(s["overall"], UNREACHABLE)
        s = self.feed(sample())
        self.assertEqual(s["overall"], OK)

    def test_ack_resets_on_new_escalation(self):
        self.feed(sample(svc="Stopped"))
        self.assertFalse(self.e.snapshot()["acked"])
        self.e.ack()
        self.assertTrue(self.e.snapshot()["acked"])
        self.feed(*[sample(svc="Stopped", mem=95)] * 3)
        self.assertFalse(self.e.snapshot()["acked"])

    def test_alert_log_records_transitions(self):
        self.feed(*[sample(cpu=95)] * 3)
        a = self.e.alert_rows()
        self.assertEqual(a[0]["item"], "cpu")
        self.assertEqual(a[0]["to"], "CRITICAL")

    def test_beacon_payload(self):
        b = self.e.beacon() if self.feed(sample(svc="Stopped")) else None
        self.assertEqual(b["o"], CRITICAL)
        self.assertTrue(b["fix"])
        self.assertEqual(b["hint"], "Spooler:Stopped")


class Insights(unittest.TestCase):
    """Detection latency, prediction, reboot detection, culprit process."""

    def setUp(self):
        self.e = Engine(["Spooler"], consec=3, hyst=5, interval=2)

    def test_latency_measured_from_injection(self):
        t0 = 1000.0
        self.e.mark("cpu", "burn")
        self.e.marks[-1]["t"] = t0
        for i in range(3):
            self.e.ingest(sample(cpu=95), now=t0 + 1 + 2 * i)     # alert on the 3rd reading, t0+5
        inc = self.e.incident_rows()[0]
        self.assertEqual(inc["fault"], "burn")
        self.assertAlmostEqual(inc["latency"], 5.0)
        self.assertEqual(self.e.stats()["detected"], 1)

    def test_spike_mark_counts_as_filtered(self):
        self.e.mark("spike", "3 s burst")
        self.e.marks[-1]["t"] -= 40
        self.e.ingest(sample(cpu=97))
        self.assertEqual(self.e.mark_rows()[0]["outcome"], "filtered (correct)")

    def test_disk_trend_prediction(self):
        for i in range(20):
            self.e.ingest(sample(disk=40 - i), now=1000 + 2 * i)   # -0.5 %/s
        eta = self.e.etas()["disk"]
        self.assertIsNotNone(eta)
        self.assertAlmostEqual(eta, (21 - 5) / 0.5, delta=1)

    def test_no_prediction_on_flat_noise(self):
        for i in range(20):
            self.e.ingest(sample(mem=50 + (i % 2) * 0.3), now=1000 + 2 * i)
        self.assertIsNone(self.e.etas()["mem"])

    def test_reboot_detected(self):
        s = sample(); s["boot"] = "2026-11-16 09:00:00"
        self.e.ingest(s)
        s = sample(); s["boot"] = "2026-11-16 10:30:00"
        self.e.ingest(s)
        self.assertEqual(self.e.incident_rows()[0]["item"], "reboot")

    def test_culprit_in_alert_and_beacon(self):
        s = sample(cpu=96)
        s["top"] = {"cpu": [{"name": "cpu_burn", "pid": 1, "cpu": 91.0, "mem_mb": 10}], "mem": []}
        for _ in range(3):
            self.e.ingest(s)
        self.assertIn("cpu_burn", self.e.alert_rows()[0]["text"])
        self.assertEqual(self.e.beacon()["hint"], "top cpu_burn 91%")

    def test_report_markdown(self):
        self.e.ingest(sample(svc="Stopped"))
        md = self.e.report_markdown()
        self.assertIn("| svc:Spooler |", md)


if __name__ == "__main__":
    unittest.main()
