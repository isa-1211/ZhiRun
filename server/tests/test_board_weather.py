import unittest

from server.zhirun_server import compact_board_weather


class BoardWeatherTests(unittest.TestCase):
    def test_returns_today_and_next_two_days_as_scalar_fields(self):
        compact = compact_board_weather({
            "ok": True,
            "latitude": 40.82,
            "longitude": 111.65,
            "current": {"temperature_2m": 21.0, "relative_humidity_2m": 58},
            "daily": {
                "time": ["2026-09-06", "2026-09-07", "2026-09-08"],
                "temperature_2m_max": [23.5, 18.6, 19.2],
                "precipitation_sum": [0.0, 5.7, 0.0],
            },
        })
        self.assertEqual(compact["temperature_2m"], 21.0)
        self.assertEqual(compact["day0_time"], "2026-09-06")
        self.assertEqual(compact["day0_precipitation_sum"], 0.0)
        self.assertEqual(compact["day1_time"], "2026-09-07")
        self.assertEqual(compact["day2_time"], "2026-09-08")
        self.assertEqual(compact["day1_precipitation_sum"], 5.7)
        self.assertNotIn("daily", compact)

    def test_short_daily_response_does_not_invent_values(self):
        compact = compact_board_weather({"ok": True, "current": {}, "daily": {"time": ["today"]}})
        self.assertEqual(compact["day0_time"], "today")
        self.assertNotIn("day1_time", compact)
        self.assertNotIn("day2_time", compact)


if __name__ == "__main__":
    unittest.main()
