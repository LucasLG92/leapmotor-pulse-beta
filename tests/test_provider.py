"""Provider contract tests; fixture readers are local and never use a network."""

import builtins
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from pulse.provider import configure_provider
from pulse.providers.base import ProviderUnavailable, UnconfiguredProvider
from pulse.providers.mate_api import MateApiB10Provider
from pulse.providers.readiness import inspect_certificate_path_metadata


TEST_ONLY = "synthetic unit-test fixture; not a cloud response"
VEHICLE_ID = "TESTB10IDENTITY0001"


@dataclass
class TelemetryFixture:
    """Test-only stand-in for the public SDK TelemetrySnapshot."""

    vin: str
    values: dict
    observed_at: datetime | None
    received_at: datetime | None


@dataclass
class ConfigurationFixture:
    """Test-only stand-in for the public SDK ConfigurationSnapshot."""

    vin: str
    observed_at: datetime | None


@dataclass
class BundleFixture:
    """Test-only stand-in for the public SDK VehicleReadBundle."""

    telemetry: TelemetryFixture
    configuration: ConfigurationFixture


class FakeReadClient:
    """Local fixture reader; it has no transport and cannot make network calls."""

    fixture_label = TEST_ONLY

    def __init__(self, bundle=None, error=None):
        self.bundle = bundle
        self.error = error
        self.calls = []
        self.command_calls = []

    def read(self, vin):
        self.calls.append(vin)
        if self.error:
            raise self.error
        return self.bundle

    def send_command(self, *args, **kwargs):
        self.command_calls.append((args, kwargs))


def make_bundle(*, vin=VEHICLE_ID, values=None, observed_at=None, received_at=None, config_at=None):
    """Build clearly synthetic snapshots, never copied from a real vehicle."""
    return BundleFixture(
        telemetry=TelemetryFixture(vin, values or {}, observed_at, received_at),
        configuration=ConfigurationFixture(vin, config_at),
    )


def test_default_provider_reports_missing_material_without_attempting_connection():
    provider = UnconfiguredProvider()
    status = provider.connection_status()

    assert status["status"] == "missing_material"
    assert status["capabilities"]["availability"] == "unknown"
    assert status["commands_enabled"] is False
    assert status["last_observed_at"] is None
    with pytest.raises(ProviderUnavailable) as error:
        provider.fetch_snapshot()
    assert error.value.code == "missing_material"


def test_provider_configuration_rejects_an_incomplete_contract():
    with pytest.raises(TypeError, match="contrato Pulse"):
        configure_provider(object())


def test_certificate_readiness_checks_only_path_metadata_and_redacts_paths(tmp_path, monkeypatch):
    material = tmp_path / "test-only-certificate.pem"
    material.touch()

    def forbidden_open(*args, **kwargs):
        raise AssertionError("readiness must not open material files")

    monkeypatch.setattr(builtins, "open", forbidden_open)
    result = inspect_certificate_path_metadata({"application_certificate": material})

    assert result == {
        "ready": True,
        "paths": {"application_certificate": "ready"},
        "reason": "metadata_ready",
    }
    assert str(material) not in repr(result)


def test_certificate_readiness_fails_closed_for_missing_path_or_bad_structure(tmp_path):
    missing = tmp_path / "missing-certificate.pem"

    missing_result = inspect_certificate_path_metadata({"application_certificate": missing})
    bad_structure = inspect_certificate_path_metadata({"unexpected\nname": missing})

    assert missing_result["ready"] is False
    assert missing_result["paths"]["application_certificate"] == "unavailable"
    assert str(missing) not in repr(missing_result)
    assert bad_structure == {
        "ready": False,
        "paths": {},
        "reason": "invalid_or_missing_structure",
    }


def test_configured_reader_is_not_connected_until_an_explicit_read():
    reader = FakeReadClient(make_bundle())
    provider = MateApiB10Provider(reader, VEHICLE_ID)

    assert provider.connection_status()["status"] == "not_connected"
    assert reader.calls == []


def test_snapshot_maps_only_known_fields_and_keeps_provider_timestamps_separate():
    observed = datetime(2026, 10, 8, 8, 20, tzinfo=timezone.utc)
    received = datetime(2026, 10, 8, 8, 21, tzinfo=timezone.utc)
    config_observed = datetime(2026, 10, 8, 8, 19, tzinfo=timezone.utc)
    reader = FakeReadClient(
        make_bundle(
            values={
                "precise_soc_percent": 78.5,
                "soc_percent": 77,
                "expected_range_km": 328,
                "odometer_km": 912,
                "interior_temperature_c": 21.5,
                "battery_min_temperature_c": 18.25,
                "front_left_pressure_kpa": 245,
                "front_right_pressure_kpa": 246,
                "rear_left_pressure_kpa": 247,
                "rear_right_pressure_kpa": 248,
                "session_token": "SYNTHETIC_SECRET_SENTINEL",
                "vehicle_locked": True,
                "charging": True,
            },
            observed_at=observed,
            received_at=received,
            config_at=config_observed,
        )
    )
    provider = MateApiB10Provider(reader, VEHICLE_ID)

    snapshot = provider.fetch_snapshot()

    assert reader.calls == [VEHICLE_ID]
    assert snapshot["vehicle"] == {
        "model": None,
        "battery_pct": 78.5,
        "range_km": 328,
        "odometer_km": 912,
        "interior_temperature_c": 21.5,
        "battery_min_temperature_c": 18.25,
        "front_left_pressure_kpa": 245,
        "front_right_pressure_kpa": 246,
        "rear_left_pressure_kpa": 247,
        "rear_right_pressure_kpa": 248,
        "vehicle_security_code": None,
        "sentry_code": None,
        "locked": None,
        "climate_on": None,
        "charging": None,
    }
    assert snapshot["field_sources"] == {
        "battery_pct": "precise_soc_percent",
        "range_km": "expected_range_km",
        "odometer_km": "odometer_km",
        "interior_temperature_c": "interior_temperature_c",
        "battery_min_temperature_c": "battery_min_temperature_c",
        "front_left_pressure_kpa": "front_left_pressure_kpa",
        "front_right_pressure_kpa": "front_right_pressure_kpa",
        "rear_left_pressure_kpa": "rear_left_pressure_kpa",
        "rear_right_pressure_kpa": "rear_right_pressure_kpa",
        "vehicle_security_code": None,
        "sentry_code": None,
        "locked": None,
        "climate_on": None,
        "charging": None,
    }
    assert snapshot["observed_at"] == "2026-10-08T08:20:00+00:00"
    assert snapshot["provider_received_at"] == "2026-10-08T08:21:00+00:00"
    assert snapshot["configuration_observed_at"] == "2026-10-08T08:19:00+00:00"
    assert snapshot["freshness"] == "unknown"
    assert "SYNTHETIC_SECRET_SENTINEL" not in repr(snapshot)
    assert provider.connection_status()["status"] == "connected"
    assert provider.connection_status()["last_observed_at"] == snapshot["observed_at"]


def test_new_metrics_reject_bool_text_nonfinite_out_of_range_and_unlisted_sources():
    reader = FakeReadClient(make_bundle(values={
        "interior_temperature_c": True,
        "battery_min_temperature_c": "18",
        "front_left_pressure_kpa": float("inf"),
        "front_right_pressure_kpa": 1001,
        "rear_left_pressure_kpa": 247,
    }))
    snapshot = MateApiB10Provider(reader, VEHICLE_ID).fetch_snapshot()

    for field in (
        "interior_temperature_c", "battery_min_temperature_c", "front_left_pressure_kpa",
        "front_right_pressure_kpa", "rear_right_pressure_kpa",
    ):
        assert snapshot["vehicle"][field] is None
        assert snapshot["field_sources"][field] is None
    assert snapshot["vehicle"]["rear_left_pressure_kpa"] == 247
    assert snapshot["field_sources"]["rear_left_pressure_kpa"] == "rear_left_pressure_kpa"


@pytest.mark.parametrize("value", [0, 1, 255, 0.0, 255.0])
def test_security_codes_preserve_integer_sdk_enumerations_without_enabling_commands(value):
    reader = FakeReadClient(make_bundle(values={"vehicle_security_code": value, "sentry_code": value}))
    provider = MateApiB10Provider(reader, VEHICLE_ID)
    snapshot = provider.fetch_snapshot()
    for field in ("vehicle_security_code", "sentry_code"):
        assert type(snapshot["vehicle"][field]) is int
        assert snapshot["vehicle"][field] == int(value)
        assert snapshot["field_sources"][field] == field
    assert snapshot["vehicle"]["locked"] is None
    assert provider.connection_status()["commands_enabled"] is False
    assert provider.connection_status()["capabilities"]["availability"] == "unknown"
    with pytest.raises(ProviderUnavailable):
        provider.execute_command("sentry_mode_on", confirmed=True)
    assert reader.command_calls == []


@pytest.mark.parametrize("value", [None, True, False, "1", -1, 256, 1.5, float("nan"), float("inf"), 10**1000])
def test_security_codes_discard_invalid_or_missing_values_and_sources(value):
    reader = FakeReadClient(make_bundle(values={"vehicle_security_code": value, "sentry_code": value}))
    snapshot = MateApiB10Provider(reader, VEHICLE_ID).fetch_snapshot()
    for field in ("vehicle_security_code", "sentry_code"):
        assert snapshot["vehicle"][field] is None
        assert snapshot["field_sources"][field] is None


def test_security_codes_from_actual_sdk_normalizer_use_only_public_signal_catalog():
    sdk_data = pytest.importorskip("leapmotor_cloud.vehicle_data")
    sdk_reads = pytest.importorskip("leapmotor_cloud.b10_reads")
    from types import MappingProxyType

    received = datetime(2026, 10, 8, 8, 21, tzinfo=timezone.utc)
    telemetry = sdk_data.normalize_telemetry({"vin": VEHICLE_ID, "signalMap": {"1255": 0, "3636": 255}},
                                           expected_vin=VEHICLE_ID, received_at=received)
    configuration = sdk_data.ConfigurationSnapshot(
        vin=VEHICLE_ID, received_at=received, observed_at=None,
        group_times=MappingProxyType({}), raw=MappingProxyType({}), charge=MappingProxyType({}))
    reader = FakeReadClient(sdk_reads.VehicleReadBundle(telemetry, configuration))
    snapshot = MateApiB10Provider(reader, VEHICLE_ID).fetch_snapshot()
    assert snapshot["vehicle"]["vehicle_security_code"] == 0
    assert snapshot["vehicle"]["sentry_code"] == 255
    assert snapshot["vehicle"]["locked"] is None
    assert reader.command_calls == []


def test_pinned_sdk_snapshot_types_match_adapter_without_transport():
    """Use the optional SDK's real immutable types with synthetic values only."""
    sdk_reads = pytest.importorskip("leapmotor_cloud.b10_reads")
    sdk_data = pytest.importorskip("leapmotor_cloud.vehicle_data")
    from types import MappingProxyType

    received = datetime(2026, 10, 8, 8, 21, tzinfo=timezone.utc)
    observed = datetime(2026, 10, 8, 8, 20, tzinfo=timezone.utc)
    telemetry = sdk_data.TelemetrySnapshot(
        vin=VEHICLE_ID,
        received_at=received,
        observed_at=observed,
        raw=MappingProxyType({"vin": VEHICLE_ID}),
        values=MappingProxyType(
            {
                "precise_soc_percent": 79.0,
                "expected_range_km": 312.0,
                "odometer_km": 1_024,
                "interior_temperature_c": 22.0,
                "battery_min_temperature_c": 18.0,
                "front_left_pressure_kpa": 245.0,
                "front_right_pressure_kpa": 246.0,
                "rear_left_pressure_kpa": 247.0,
                "rear_right_pressure_kpa": 248.0,
            }
        ),
        unknown_signals=MappingProxyType({}),
    )
    configuration = sdk_data.ConfigurationSnapshot(
        vin=VEHICLE_ID,
        received_at=received,
        observed_at=observed,
        group_times=MappingProxyType({}),
        raw=MappingProxyType({"vin": VEHICLE_ID}),
        charge=MappingProxyType({}),
    )
    bundle = sdk_reads.VehicleReadBundle(telemetry, configuration)

    class InMemorySdkClient:
        """Test-only reader returning real SDK models; contains no transport."""

        fixture_label = TEST_ONLY

        def read(self, vin):
            assert vin == VEHICLE_ID
            return bundle

    provider = MateApiB10Provider(InMemorySdkClient(), VEHICLE_ID)
    snapshot = provider.fetch_snapshot()

    assert snapshot["vehicle"]["battery_pct"] == 79.0
    assert snapshot["vehicle"]["range_km"] == 312.0
    assert snapshot["vehicle"]["odometer_km"] == 1_024
    assert snapshot["vehicle"]["interior_temperature_c"] == 22.0
    assert snapshot["vehicle"]["battery_min_temperature_c"] == 18.0
    assert snapshot["vehicle"]["front_left_pressure_kpa"] == 245.0
    assert snapshot["vehicle"]["rear_right_pressure_kpa"] == 248.0
    assert snapshot["observed_at"] == "2026-10-08T08:20:00+00:00"
    assert snapshot["provider_received_at"] == "2026-10-08T08:21:00+00:00"


def test_actual_sdk_read_client_is_accepted_but_cannot_reach_network_without_session():
    sdk_reads = pytest.importorskip("leapmotor_cloud.b10_reads")
    sdk_session = pytest.importorskip("leapmotor_cloud.session")

    class NoNetworkTransport:
        def __init__(self):
            self.calls = []

        def send(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise AssertionError("test transport must never be reached")

    transport = NoNetworkTransport()
    read_client = sdk_reads.B10ReadClient(
        transport,
        sdk_session.SessionStore(),
        center="https://appgateway.leapmotor-international.de",
        region="https://appgateway.leapmotor-international.de",
    )
    provider = MateApiB10Provider(read_client, VEHICLE_ID)

    assert provider.connection_status()["status"] == "not_connected"
    with pytest.raises(ProviderUnavailable) as error:
        provider.fetch_snapshot()

    assert error.value.code == "read_failed"
    assert transport.calls == []


def test_missing_observation_time_stays_null_instead_of_using_receive_time():
    received = datetime(2026, 10, 8, 8, 21, tzinfo=timezone.utc)
    reader = FakeReadClient(
        make_bundle(values={"soc_percent": 80}, observed_at=None, received_at=received)
    )
    provider = MateApiB10Provider(reader, VEHICLE_ID)

    snapshot = provider.fetch_snapshot()

    assert snapshot["observed_at"] is None
    assert snapshot["provider_received_at"] == "2026-10-08T08:21:00+00:00"
    assert provider.connection_status()["last_observed_at"] is None
    assert provider.connection_status()["freshness"] == "unknown"


def test_identity_mismatch_fails_closed_without_exposing_vehicle_identity():
    reader = FakeReadClient(make_bundle(vin="OTHERTESTIDENTITY0001"))
    provider = MateApiB10Provider(reader, VEHICLE_ID)

    with pytest.raises(ProviderUnavailable) as error:
        provider.fetch_snapshot()

    assert error.value.code == "vehicle_identity_mismatch"
    assert VEHICLE_ID not in str(error.value)
    assert provider.connection_status()["status"] == "unavailable"


def test_sdk_errors_are_redacted_and_not_retried():
    private_detail = "synthetic-test-secret-marker"
    reader = FakeReadClient(error=RuntimeError(private_detail))
    provider = MateApiB10Provider(reader, VEHICLE_ID)

    with pytest.raises(ProviderUnavailable) as error:
        provider.fetch_snapshot()

    assert error.value.code == "read_failed"
    assert private_detail not in str(error.value)
    assert reader.calls == [VEHICLE_ID]
    assert provider.connection_status()["status"] == "unavailable"


def test_commands_stay_blocked_after_read_and_never_call_sdk_command_surface():
    reader = FakeReadClient(make_bundle())
    provider = MateApiB10Provider(reader, VEHICLE_ID)
    provider.fetch_snapshot()

    assert provider.command_status("lock") == {
        "command": "lock",
        "availability": "unknown",
        "enabled": False,
        "reason": "capabilities_not_validated",
    }
    with pytest.raises(ProviderUnavailable) as error:
        provider.execute_command("lock", confirmed=True)

    assert error.value.code == "capability_unknown"
    assert reader.command_calls == []


@pytest.mark.parametrize(
    "values",
    [
        {"precise_soc_percent": True, "soc_percent": 150},
        {"expected_range_km": float("nan")},
        {"odometer_km": -1},
    ],
)
def test_invalid_measurements_remain_null(values):
    reader = FakeReadClient(make_bundle(values=values))
    provider = MateApiB10Provider(reader, VEHICLE_ID)

    snapshot = provider.fetch_snapshot()

    assert snapshot["vehicle"]["battery_pct"] is None
    assert snapshot["vehicle"]["range_km"] is None
    assert snapshot["vehicle"]["odometer_km"] is None
    assert snapshot["observed_at"] is None
