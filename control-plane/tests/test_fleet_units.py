"""Unit tests for fleet token, identity, and status helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.devices.identity import (
    DeviceIdentity,
    match_existing_device,
    normalize_mac,
    normalize_macs,
)
from app.devices.models import Device
from app.devices.status import ConnectivityStatus, connectivity_status
from app.devices.tokens import (
    API_KEY_PREFIX,
    CLAIM_SECRET_PREFIX,
    DEVICE_TOKEN_PREFIX,
    REGISTRATION_TOKEN_PREFIX,
    generate_api_key,
    generate_claim_secret,
    generate_device_token,
    generate_registration_token,
    hash_token,
)


def test_registration_token_has_prefix_and_hash() -> None:
    generated = generate_registration_token()
    assert generated.plaintext.startswith(REGISTRATION_TOKEN_PREFIX)
    assert generated.token_hash == hash_token(generated.plaintext)
    assert generated.display_prefix == generated.plaintext[:12]
    assert generated.plaintext not in generated.token_hash


def test_device_token_has_prefix() -> None:
    generated = generate_device_token()
    assert generated.plaintext.startswith(DEVICE_TOKEN_PREFIX)
    assert len(generated.token_hash) == 64


def test_api_key_and_claim_secret_prefixes() -> None:
    key = generate_api_key()
    claim = generate_claim_secret()
    assert key.plaintext.startswith(API_KEY_PREFIX)
    assert claim.plaintext.startswith(CLAIM_SECRET_PREFIX)
    assert key.token_hash == hash_token(key.plaintext)
    assert claim.token_hash == hash_token(claim.plaintext)


def test_tokens_are_unique() -> None:
    tokens = {generate_registration_token().plaintext for _ in range(50)}
    assert len(tokens) == 50


def test_normalize_mac_variants() -> None:
    assert normalize_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aabb.ccdd.eeff") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aa-bb-cc-dd-ee-ff") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("00:00:00:00:00:00") is None
    assert normalize_mac("not-a-mac") is None
    assert normalize_mac("") is None


def test_normalize_macs_dedupes_and_orders() -> None:
    result = normalize_macs(["AA:BB:CC:DD:EE:FF", "aabbccddeeff", "11:22:33:44:55:66"])
    assert result == ["aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"]


def test_identity_matches_by_serial_number() -> None:
    identity = DeviceIdentity(serial_number="abc", mac_addresses=[])
    device = Device(serial_number="abc")
    assert identity.matches(device)


def test_identity_matches_by_mac_overlap() -> None:
    identity = DeviceIdentity(serial_number=None, mac_addresses=["aa:bb:cc:dd:ee:ff"])
    device = Device(mac_addresses=["11:22:33:44:55:66", "aa:bb:cc:dd:ee:ff"])
    assert identity.matches(device)


def test_match_existing_device_cross_organization(monkeypatch) -> None:
    import uuid

    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    other = Device(mac_addresses=["aa:bb:cc:dd:ee:01"])
    other.organization_id = org_b
    other.id = uuid.uuid4()
    identity = DeviceIdentity(serial_number=None, mac_addresses=["aa:bb:cc:dd:ee:01"])
    result = match_existing_device(identity, organization_id=org_a, candidates=[other])
    assert result.cross_organization is True
    assert result.device is None


def test_match_existing_device_ambiguous() -> None:
    import uuid

    org = uuid.uuid4()
    d1 = Device(mac_addresses=["aa:bb:cc:dd:ee:01"])
    d1.organization_id = org
    d1.id = uuid.uuid4()
    d2 = Device(serial_number="s1")
    d2.organization_id = org
    d2.id = uuid.uuid4()
    identity = DeviceIdentity(serial_number="s1", mac_addresses=["aa:bb:cc:dd:ee:01"])
    result = match_existing_device(identity, organization_id=org, candidates=[d1, d2])
    assert result.ambiguous is True


def test_connectivity_status() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert connectivity_status(None, offline_threshold_seconds=150, now=now) is ConnectivityStatus.NEVER_SEEN
    assert (
        connectivity_status(now - timedelta(seconds=30), offline_threshold_seconds=150, now=now)
        is ConnectivityStatus.ONLINE
    )
    assert (
        connectivity_status(now - timedelta(seconds=200), offline_threshold_seconds=150, now=now)
        is ConnectivityStatus.OFFLINE
    )
