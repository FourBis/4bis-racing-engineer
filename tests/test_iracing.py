import unittest
from types import SimpleNamespace

from racing_engineer.iracing import IRacingSource
from racing_engineer.model import Sample


class FakeSDK:
    def __init__(self):
        self.connected = True
        self.tick = 1
        self.startup_results = []
        self.raise_once = set()
        self.values = {
            "SessionTime": 1.0,
            "SessionNum": 0,
            "Lap": 2,
            "LapDistPct": 0.25,
            "Speed": 30.0,
            "Brake": 0.4,
            "BrakeRaw": 0.45,
            "Throttle": 0.2,
            "Clutch": 0.0,
            "SteeringWheelAngle": -0.1,
            "RPM": 4000.0,
            "Gear": 3,
            "FuelLevel": 20.0,
            "LapCurrentLapTime": 12.0,
            "PlayerCarMyIncidentCount": 0,
            "IsOnTrack": True,
            "IsReplayPlaying": False,
            "SessionUniqueID": 71,
            "WeekendInfo": {"SessionID": 10, "SubSessionID": 20, "TrackID": 127,
                            "TrackDisplayName": "Road Atlanta", "TrackConfigName": "Full Course",
                            "TrackLength": "4.0569 km"},
            "PlayerCarIdx": 2,
            "DriverInfo": {"Drivers": [
                {"CarIdx": 0, "CarID": 1, "CarScreenName": "Other driver"},
                {"CarIdx": 2, "CarID": 119, "CarScreenName": "Porsche 718 Cayman GT4"},
            ]},
        }
        self.var_headers_names = list(self.values)
        self._var_headers_dict = {
            "Speed": SimpleNamespace(unit="m/s", desc="GPS speed")
        }
        self.started = 0
        self.stopped = 0

    @property
    def is_connected(self):
        return self.connected

    @property
    def _var_buffer_latest(self):
        return SimpleNamespace(tick_count=self.tick)

    def __getitem__(self, name):
        if name in self.raise_once:
            self.raise_once.remove(name)
            raise RuntimeError("lectura temporal fallida")
        return self.values.get(name)

    def startup(self):
        self.started += 1
        return self.startup_results.pop(0) if self.startup_results else True

    def shutdown(self):
        self.stopped += 1

    def freeze_var_buffer_latest(self):
        pass

    def unfreeze_var_buffer_latest(self):
        pass


class IRacingSourceTests(unittest.TestCase):
    def setUp(self):
        self.sdk = FakeSDK()
        self.source = IRacingSource(self.sdk)

    def tearDown(self):
        self.source.close()

    def test_maps_sample_and_uses_boolean_connection_property(self):
        sample = self.source.read()
        self.assertEqual("iracing", sample.source)
        self.assertEqual(1.0, sample.time_s)
        self.assertEqual(0.4, sample.brake)
        self.assertEqual(0.45, sample.brake_raw)
        self.assertEqual(30.0, sample.speed_mps)
        self.assertTrue(sample.on_track)
        self.assertTrue(self.source.connected)

    def test_maps_track_and_own_car_metadata(self):
        sample = self.source.read()
        self.assertEqual(127, sample.track_id)
        self.assertEqual("Road Atlanta", sample.track_name)
        self.assertEqual("Full Course", sample.track_config)
        self.assertAlmostEqual(4056.9, sample.track_length_m)
        self.assertEqual(119, sample.car_id)
        self.assertEqual("Porsche 718 Cayman GT4", sample.car_name)

    def test_uses_driver_car_index_fallback_without_selecting_first_driver(self):
        self.sdk.values.pop("PlayerCarIdx")
        self.sdk.values["DriverInfo"]["DriverCarIdx"] = 2
        sample = self.source.read()
        self.assertEqual(119, sample.car_id)
        self.assertEqual("Porsche 718 Cayman GT4", sample.car_name)

    def test_track_name_falls_back_and_invalid_metadata_stays_none(self):
        self.sdk.values["WeekendInfo"] = {
            "TrackID": "127", "TrackDisplayName": "", "TrackName": "Legacy name",
            "TrackConfigName": 3, "TrackLength": "4.0569 miles",
        }
        self.sdk.values["DriverInfo"] = {"Drivers": [
            {"CarIdx": 0, "CarID": 4, "CarScreenName": "Other"},
        ]}
        sample = self.source.read()
        self.assertEqual("Legacy name", sample.track_name)
        self.assertIsNone(sample.track_id)
        self.assertIsNone(sample.track_config)
        self.assertIsNone(sample.track_length_m)
        self.assertIsNone(sample.car_id)
        self.assertIsNone(sample.car_name)

    def test_metadata_is_none_when_sections_are_missing_or_malformed(self):
        self.sdk.values["WeekendInfo"] = None
        self.sdk.values["DriverInfo"] = {"Drivers": "not a list"}
        self.sdk.values.pop("PlayerCarIdx")
        sample = self.source.read()
        for field in ("track_id", "track_name", "track_config", "track_length_m", "car_id", "car_name"):
            self.assertIsNone(getattr(sample, field))

    def test_legacy_sample_json_uses_none_for_new_optional_fields(self):
        row = Sample(source="demo", session_id="legacy", time_s=1.0).to_dict()
        for field in ("track_id", "track_name", "track_config", "track_length_m", "car_id", "car_name"):
            row.pop(field)
        sample = Sample.from_dict(row)
        self.assertEqual((None,) * 6, tuple(getattr(sample, field) for field in (
            "track_id", "track_name", "track_config", "track_length_m", "car_id", "car_name")))

    def test_does_not_repeat_frozen_tick(self):
        self.assertIsNotNone(self.source.read())
        self.assertIsNone(self.source.read())
        self.sdk.tick += 1
        self.assertIsNotNone(self.source.read())

    def test_missing_optional_channels_stay_none(self):
        for name in ("BrakeRaw", "Clutch", "Speed"):
            self.sdk.values.pop(name)
            self.sdk.var_headers_names.remove(name)
        sample = self.source.read()
        self.assertIsNone(sample.brake_raw)
        self.assertIsNone(sample.clutch)
        self.assertIsNone(sample.speed_mps)

    def test_session_change_starts_new_segment(self):
        first = self.source.read()
        self.sdk.tick += 1
        self.sdk.values["WeekendInfo"] = {"SessionID": 11, "SubSessionID": 20}
        second = self.source.read()
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertIn("session=11", second.session_id)

    def test_session_time_reset_starts_new_segment(self):
        first = self.source.read()
        self.sdk.tick += 1
        self.sdk.values["SessionTime"] = 0.5
        second = self.source.read()
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertEqual("session=10/subsession=20/num=0/unique=71:segment=2", second.session_id)

    def test_reconnection_starts_new_segment(self):
        first = self.source.read()
        self.sdk.connected = False
        self.assertIsNone(self.source.read())
        self.assertEqual(1, self.sdk.stopped)
        self.sdk.connected = True
        self.sdk.tick = 1
        second = self.source.read()
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertEqual(2, self.sdk.started)

    def test_startup_failure_is_retried(self):
        self.sdk.startup_results = [False, True]
        self.assertIsNone(self.source.read())
        sample = self.source.read()
        self.assertIsNotNone(sample)
        self.assertEqual(2, self.sdk.started)

    def test_errors_clear_after_a_valid_sample(self):
        self.sdk.raise_once.add("SessionTime")
        self.assertIsNone(self.source.read())
        self.assertIn("Fallo al leer SessionTime", self.source.error)
        self.sdk.tick += 1
        self.assertIsNotNone(self.source.read())
        self.assertIsNone(self.source.error)

    def test_stale_telemetry_is_reported(self):
        now = [0.0]
        source = IRacingSource(self.sdk, clock=lambda: now[0])
        self.assertIsNotNone(source.read())
        now[0] = 2.1
        self.assertIn("obsoleta", source.status)
        source.close()

    def test_invalid_optional_sentinels_stay_none_with_issue(self):
        self.sdk.values["LapDistPct"] = -1.0
        self.sdk.values["Brake"] = 1.2
        sample = self.source.read()
        self.assertIsNone(sample.lap_dist_pct)
        self.assertIsNone(sample.brake)
        self.assertFalse(self.source.channels["LapDistPct"]["valid"])
        self.assertIn("fuera de rango", self.source.channels["LapDistPct"]["issue"])

    def test_unknown_header_unit_marks_channel_unavailable(self):
        self.sdk._var_headers_dict["Brake"] = SimpleNamespace(unit="psi", desc="unknown")
        sample = self.source.read()
        self.assertIsNone(sample.brake)
        details = self.source.channels["Brake"]
        self.assertFalse(details["available"])
        self.assertFalse(details["valid"])
        self.assertIn("unidad", details["issue"])

    def test_disconnected_is_normal_and_close_releases_sdk(self):
        self.sdk.connected = False
        self.assertIsNone(self.source.read())
        self.assertIn("desconectado", self.source.status)
        self.assertFalse(self.source.connected)
        self.source.close()
        self.assertEqual(1, self.sdk.stopped)

    def test_voice_channels_preserve_abs_boolean_raw_input_and_signed_acceleration(self):
        self.sdk.values.update(BrakeABSactive=True, ThrottleRaw=0.37, VertAccel=-2.0)
        self.sdk.var_headers_names = list(self.sdk.values)
        self.sdk._var_headers_dict["VertAccel"] = SimpleNamespace(unit="m/s^2", desc="Includes gravity")
        sample = self.source.read()
        self.assertTrue(sample.abs_active)
        self.assertEqual(0.37, sample.throttle_raw)
        self.assertEqual(-2.0, sample.vert_accel_mps2)
        self.sdk.tick += 1
        self.sdk.values.update(BrakeABSactive=1, ThrottleRaw=1.2)
        sample = self.source.read()
        self.assertIsNone(sample.abs_active)
        self.assertIsNone(sample.throttle_raw)


if __name__ == "__main__":
    unittest.main()
