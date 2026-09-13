import unittest
from unittest import mock

from server import zhirun_server


class RecordingScheduleTests(unittest.TestCase):
    def setUp(self):
        self.device_id = "recording-schedule-test"
        zhirun_server._latest_by_device[self.device_id] = {
            "_ts": 995,
            "soilMoist": 38.2,
            "airTemp": 21.5,
        }
        zhirun_server._recordings_by_device.pop(self.device_id, None)

    def tearDown(self):
        zhirun_server._latest_by_device.pop(self.device_id, None)
        zhirun_server._recordings_by_device.pop(self.device_id, None)

    def test_scheduled_recording_starts_samples_and_stops_at_boundaries(self):
        with mock.patch.object(zhirun_server, "now", return_value=1000):
            state = zhirun_server.start_recording(
                self.device_id, interval_seconds=60, start_at=1100, end_at=1300
            )
        self.assertEqual(state["status"], "scheduled")
        self.assertFalse(state["active"])
        self.assertEqual(state["estimated_samples"], 4)

        zhirun_server.maybe_record_sample(self.device_id, {"_ts": 1090, "soilMoist": 39}, 1090)
        self.assertEqual(len(zhirun_server._recordings_by_device[self.device_id]["items"]), 0)
        zhirun_server.maybe_record_sample(self.device_id, {"_ts": 1100, "soilMoist": 40}, 1100)
        zhirun_server.maybe_record_sample(self.device_id, {"_ts": 1159, "soilMoist": 41}, 1159)
        zhirun_server.maybe_record_sample(self.device_id, {"_ts": 1160, "soilMoist": 42}, 1160)
        self.assertEqual(len(zhirun_server._recordings_by_device[self.device_id]["items"]), 2)

        zhirun_server.maybe_record_sample(self.device_id, {"_ts": 1300, "soilMoist": 43}, 1300)
        with mock.patch.object(zhirun_server, "now", return_value=1300):
            state = zhirun_server.recording_snapshot(self.device_id)
        self.assertEqual(state["status"], "completed")
        self.assertFalse(state["active"])
        self.assertEqual(state["sample_count"], 2)

    def test_recording_interval_and_period_are_validated(self):
        with mock.patch.object(zhirun_server, "now", return_value=1000):
            with self.assertRaisesRegex(ValueError, "invalid_recording_interval"):
                zhirun_server.start_recording(self.device_id, interval_seconds=1)
            with self.assertRaisesRegex(ValueError, "invalid_recording_period"):
                zhirun_server.start_recording(
                    self.device_id, interval_seconds=60, start_at=1100, end_at=1050
                )

    def test_immediate_recording_uses_custom_interval(self):
        with mock.patch.object(zhirun_server, "now", return_value=1000):
            state = zhirun_server.start_recording(self.device_id, interval_seconds=30)
        self.assertTrue(state["active"])
        self.assertEqual(state["sample_count"], 1)
        self.assertEqual(state["next_sample_at"], 1030)
        zhirun_server.maybe_record_sample(self.device_id, {"_ts": 1030, "soilMoist": 40}, 1030)
        self.assertEqual(len(zhirun_server._recordings_by_device[self.device_id]["items"]), 2)


if __name__ == "__main__":
    unittest.main()
