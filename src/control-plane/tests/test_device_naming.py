"""Default device names: enrolling a device shouldn't require inventing a name."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.devices.models import Device
from app.devices.naming import default_device_name, match_device_type, model_kind, unique_device_name
from tests.conftest import auth_header, create_org_with_owner, create_user
from tests.test_deployments import _register_device, _setup_release

PI5 = "Raspberry Pi 5 Model B Rev 1.0"


@pytest.mark.parametrize(
    ("requested", "hostname", "serial", "macs", "type_slug", "model", "expected"),
    [
        # A chosen name wins.
        ("edge-01", "kiosk", "S1", ["aa:bb:cc:dd:a1:b2"], "raspberry-pi-5", PI5, "edge-01"),
        ("  edge-01 ", None, None, [], None, None, "edge-01"),
        # Agents up to 0.2.0 ask for their hostname: that is not a choice.
        ("raspberrypi", "raspberrypi", None, ["aa:bb:cc:dd:a1:b2"], None, PI5, "raspberrypi5-dda1b2"),
        ("meteoros", "MeteorOS.local", None, ["aa:bb:cc:dd:a1:b2"], None, None, "meteoros-dda1b2"),
        # Device type, then board model, then hostname, then "device".
        (None, "kiosk", "S1", ["aa:bb:cc:dd:a1:b2"], "raspberry-pi-5", "Something Else", "raspberrypi5-dda1b2"),
        (None, "kiosk", "S1", ["aa:bb:cc:dd:a1:b2"], None, PI5, "raspberrypi5-dda1b2"),
        (None, "kiosk-lobby", "S1", ["aa:bb:cc:dd:a1:b2"], None, None, "kiosk-lobby-dda1b2"),
        (None, "localhost", None, ["aa:bb:cc:dd:a1:b2"], None, "To Be Filled By O.E.M.", "device-dda1b2"),
        # Without a MAC the serial gives the suffix; without either there is none.
        (None, "raspberrypi", "10000000c3d4", [], None, PI5, "raspberrypi5-00c3d4"),
        (None, None, "SN-00-77", [], None, None, "device-sn0077"),
        (None, "raspberrypi", None, [], None, None, "raspberrypi"),
        (None, "", None, [], None, None, "device"),
    ],
)
def test_default_name(
    requested: str | None,
    hostname: str | None,
    serial: str | None,
    macs: list[str],
    type_slug: str | None,
    model: str | None,
    expected: str,
) -> None:
    name = default_device_name(
        requested_name=requested,
        hostname=hostname,
        serial_number=serial,
        mac_addresses=macs,
        device_type_slug=type_slug,
        hardware_model=model,
    )
    assert name == expected


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("Raspberry Pi 5 Model B Rev 1.0", "raspberrypi5"),
        ("Raspberry Pi 3 Model B Plus Rev 1.3", "raspberrypi3"),
        ("Raspberry Pi 400 Rev 1.0", "raspberrypi400"),
        ("Raspberry Pi Zero 2 W Rev 1.0", "raspberrypizero2w"),
        ("Raspberry Pi Compute Module 4 Rev 1.1", "raspberrypicm4"),
        ("NVIDIA Jetson Orin Nano Developer Kit", "nvidiajetsonorinnano"),
        ("NUC13ANHi5", "nuc13anhi5"),
        ("System Product Name", None),
        ("", None),
        (None, None),
    ],
)
def test_model_kind(model: str | None, expected: str | None) -> None:
    assert model_kind(model) == expected


def test_match_device_type_takes_the_most_specific_leading_words() -> None:
    def kind(name: str, model: str | None = None, manufacturer: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(name=name, slug=name.lower().replace(" ", "-"), model=model, manufacturer=manufacturer)

    pi4, pi400, pi5 = kind("Raspberry Pi 4"), kind("Raspberry Pi 400"), kind("Pi five", "5 Model B", "Raspberry Pi")
    nuc = kind("Intel NUC 13", "NUC13ANHi5", "Intel")
    catalog = [pi4, pi400, pi5, nuc]
    assert match_device_type("Raspberry Pi 4 Model B Rev 1.4", catalog) is pi4
    assert match_device_type("Raspberry Pi 400 Rev 1.0", catalog) is pi400
    assert match_device_type("Raspberry Pi 5 Model B Rev 1.0", catalog) is pi5
    assert match_device_type("NUC13ANHi5", catalog) is nuc
    assert match_device_type("Raspberry Pi 3 Model B Rev 1.2", catalog) is None
    assert match_device_type(None, catalog) is None
    # Two equally good fits: no guess.
    assert match_device_type("Raspberry Pi 4 Model B", [pi4, kind("raspberry pi 4")]) is None


def test_unique_name_adds_a_number_ignoring_case() -> None:
    assert unique_device_name("Kiosk", taken=set()) == "Kiosk"
    assert unique_device_name("Kiosk", taken={"kiosk"}) == "Kiosk-2"
    assert unique_device_name("kiosk", taken={"kiosk", "kiosk-2"}) == "kiosk-3"
    long_name = "x" * 255
    assert unique_device_name(long_name, taken={long_name}) == "x" * 253 + "-2"


def _org_with_key(client: TestClient, db_session: Session, email: str = "owner@example.com"):
    owner = create_user(db_session, email=email)
    org, _ = create_org_with_owner(db_session, owner)
    headers = auth_header(client, email)
    key = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-keys", headers=headers, json={"name": "Field"}
    ).json()["api_key"]
    return org, headers, key


def _submit(client: TestClient, key: str, **body) -> dict:
    response = client.post("/api/v1/agent/enroll/request", headers={"Authorization": f"Bearer {key}"}, json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()


def _suggestions(client: TestClient, org, headers) -> dict[str, str | None]:
    rows = client.get(f"/api/v1/organizations/{org.id}/enrollment-requests", headers=headers).json()
    return {row["id"]: row["suggested_name"] for row in rows}


def _claim(client: TestClient, submitted: dict) -> dict:
    response = client.post(
        "/api/v1/agent/enroll/poll",
        json={"request_id": submitted["request_id"], "claim_secret": submitted["claim_secret"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _approve(client: TestClient, org, headers, request_id: str, **body) -> None:
    response = client.post(
        f"/api/v1/organizations/{org.id}/enrollment-requests/{request_id}/approve", headers=headers, json=body
    )
    assert response.status_code == 200, response.text


def test_two_default_hostnames_get_distinct_suggestions_that_the_device_receives(
    client: TestClient, db_session: Session
) -> None:
    org, headers, key = _org_with_key(client, db_session)
    first = _submit(client, key, hostname="raspberrypi", mac_addresses=["aa:bb:cc:dd:00:01"])
    second = _submit(client, key, hostname="raspberrypi", mac_addresses=["aa:bb:cc:dd:00:02"])
    suggestions = _suggestions(client, org, headers)
    assert suggestions[first["request_id"]] == "raspberrypi-dd0001"
    assert suggestions[second["request_id"]] == "raspberrypi-dd0002"

    # Approved without a name: the device still gets the default.
    _approve(client, org, headers, first["request_id"])
    assert _claim(client, first)["name"] == "raspberrypi-dd0001"
    # Once claimed, a request no longer has a suggestion.
    assert _suggestions(client, org, headers)[first["request_id"]] is None


def test_repeated_chosen_name_is_numbered_while_pending_and_after_claim(
    client: TestClient, db_session: Session
) -> None:
    org, headers, key = _org_with_key(client, db_session)
    first = _submit(client, key, name="kiosk", hostname="pi", mac_addresses=["aa:bb:cc:dd:00:03"])
    second = _submit(client, key, name="kiosk", hostname="pi", mac_addresses=["aa:bb:cc:dd:00:04"])
    suggestions = _suggestions(client, org, headers)
    assert suggestions[first["request_id"]] == "kiosk"
    assert suggestions[second["request_id"]] == "kiosk-2"

    _approve(client, org, headers, first["request_id"])
    _approve(client, org, headers, second["request_id"])
    assert _claim(client, first)["name"] == "kiosk"
    assert _claim(client, second)["name"] == "kiosk-2"


def test_typed_name_is_used_as_is_even_if_repeated(client: TestClient, db_session: Session) -> None:
    org, headers, key = _org_with_key(client, db_session)
    for mac in ("aa:bb:cc:dd:00:05", "aa:bb:cc:dd:00:06"):
        submitted = _submit(client, key, hostname="kiosk", mac_addresses=[mac])
        _approve(client, org, headers, submitted["request_id"], name="Lobby")
        assert _claim(client, submitted)["name"] == "Lobby"


def test_reenrolling_device_keeps_its_name(client: TestClient, db_session: Session) -> None:
    org, headers, key = _org_with_key(client, db_session)
    mac = "aa:bb:cc:dd:00:07"
    first = _submit(client, key, hostname="raspberrypi", mac_addresses=[mac])
    _approve(client, org, headers, first["request_id"])
    device_id = _claim(client, first)["device_id"]
    renamed = client.patch(
        f"/api/v1/organizations/{org.id}/devices/{device_id}", headers=headers, json={"name": "Lobby"}
    )
    assert renamed.status_code == 200, renamed.text

    again = _submit(client, key, hostname="raspberrypi", mac_addresses=[mac])
    assert _suggestions(client, org, headers)[again["request_id"]] == "Lobby"
    _approve(client, org, headers, again["request_id"])
    assert _claim(client, again)["name"] == "Lobby"


def test_token_registration_uses_the_same_defaults(client: TestClient, db_session: Session) -> None:
    _org, _headers, token, _release = _setup_release(client, db_session)
    for mac, expected in (("aa:bb:cc:dd:00:08", "raspberrypi5-dd0008"), ("aa:bb:cc:dd:00:09", "raspberrypi5-dd0009")):
        response = client.post(
            "/api/v1/agent/register",
            # An agent up to 0.2.0 asks for its hostname as the name.
            json={
                "token": token,
                "name": "kiosk",
                "hostname": "kiosk",
                "mac_addresses": [mac],
                "hardware_model": "Raspberry Pi 5 Model B Rev 1.0",
            },
        )
        assert response.status_code in (200, 201), response.text
        assert response.json()["name"] == expected


def test_a_device_defined_in_the_console_keeps_the_name_given_there(client: TestClient, db_session: Session) -> None:
    org, headers, _token, _release = _setup_release(client, db_session)

    def define(name: str) -> str:
        response = client.post(
            f"/api/v1/organizations/{org.id}/registration-tokens", headers=headers, json={"name": name, "max_uses": 1}
        )
        assert response.status_code == 201, response.text
        return response.json()["token"]

    body = {"hostname": "meteoros", "hardware_model": "Raspberry Pi 4 Model B Rev 1.5"}
    defined = client.post(
        "/api/v1/agent/register",
        json={"token": define("pi-01"), "name": "meteoros", "mac_addresses": ["aa:bb:cc:dd:00:30"], **body},
    )
    assert defined.status_code in (200, 201), defined.text
    assert defined.json()["name"] == "pi-01"
    # A name given on the device itself still wins.
    named = client.post(
        "/api/v1/agent/register",
        json={"token": define("pi-02"), "name": "Lobby", "mac_addresses": ["aa:bb:cc:dd:00:31"], **body},
    )
    assert named.json()["name"] == "Lobby"


def test_renaming_a_device_keeps_its_public_address(client: TestClient, db_session: Session) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    device = _register_device(client, token, name="Kiosk 12", mac="aa:bb:cc:dd:00:0a")
    base = f"/api/v1/organizations/{org.id}"
    address = f"shop.kiosk-12.{org.slug}.meteor-edge.com"
    created = client.post(
        f"{base}/endpoints", headers=headers, json={"device_id": device["device_id"], "hostname": address, "port": 80}
    )
    assert created.status_code == 201, created.text
    renamed = client.patch(f"{base}/devices/{device['device_id']}", headers=headers, json={"name": "Lobby"})
    assert renamed.status_code == 200, renamed.text
    assert client.get(f"{base}/endpoints/{created.json()['id']}", headers=headers).json()["hostname"] == address


def test_status_filter_still_counts_names_held_by_other_requests(client: TestClient, db_session: Session) -> None:
    org, headers, key = _org_with_key(client, db_session)
    approved = _submit(client, key, hostname="other", mac_addresses=["aa:bb:cc:dd:00:0b"])
    _approve(client, org, headers, approved["request_id"], name="kiosk")
    pending = _submit(client, key, name="kiosk", hostname="pi", mac_addresses=["aa:bb:cc:dd:00:0c"])
    rows = client.get(
        f"/api/v1/organizations/{org.id}/enrollment-requests", headers=headers, params={"status": "pending"}
    ).json()
    assert [(row["id"], row["suggested_name"]) for row in rows] == [(pending["request_id"], "kiosk-2")]


def test_ambiguous_hardware_gets_no_existing_device_name(client: TestClient, db_session: Session) -> None:
    org, headers, key = _org_with_key(client, db_session)
    first = _submit(client, key, hostname="first", serial_number="S1", mac_addresses=["aa:bb:cc:dd:00:0d"])
    _approve(client, org, headers, first["request_id"])
    second = _submit(client, key, hostname="second", serial_number="S2", mac_addresses=["aa:bb:cc:dd:00:0e"])
    _approve(client, org, headers, second["request_id"])
    first_id = _claim(client, first)["device_id"]
    second_id = _claim(client, second)["device_id"]
    # Two devices now report the same serial, which a claim refuses as ambiguous.
    device = db_session.get(Device, UUID(second_id))
    device.serial_number = "S1"
    db_session.commit()
    assert first_id != second_id

    request = _submit(client, key, hostname="third", serial_number="S1", mac_addresses=["aa:bb:cc:dd:00:0f"])
    assert _suggestions(client, org, headers)[request["request_id"]] == "third-dd000f"


def test_device_list_filters_by_exact_name(client: TestClient, db_session: Session) -> None:
    org, headers, token, _release = _setup_release(client, db_session)
    for index, name in enumerate(("Lobby", "Lobby 2", "Old lobby")):
        _register_device(client, token, name=name, mac=f"aa:bb:cc:dd:01:0{index}")
    listed = client.get(f"/api/v1/organizations/{org.id}/devices", headers=headers, params={"name": "lobby"}).json()
    assert [item["name"] for item in listed["items"]] == ["Lobby"]


def test_approved_names_hold_even_when_a_later_request_claims_first(client: TestClient, db_session: Session) -> None:
    org, headers, key = _org_with_key(client, db_session)
    first = _submit(client, key, name="kiosk", mac_addresses=["aa:bb:cc:dd:00:10"])
    second = _submit(client, key, name="kiosk", mac_addresses=["aa:bb:cc:dd:00:11"])
    _approve(client, org, headers, first["request_id"])
    _approve(client, org, headers, second["request_id"])
    assert _claim(client, second)["name"] == "kiosk-2"
    assert _claim(client, first)["name"] == "kiosk"


def test_reregistering_with_a_taken_name_gets_a_number(client: TestClient, db_session: Session) -> None:
    _org, _headers, token, _release = _setup_release(client, db_session)
    _register_device(client, token, name="Lobby", mac="aa:bb:cc:dd:00:12")
    other = _register_device(client, token, name="Kiosk", mac="aa:bb:cc:dd:00:13")
    renamed = _register_device(client, token, name="lobby", mac="aa:bb:cc:dd:00:13")
    assert renamed["device_id"] == other["device_id"]
    assert renamed["name"] == "lobby-2"
    # Asking again for the name it already has keeps it (case changes included).
    assert _register_device(client, token, name="LOBBY-2", mac="aa:bb:cc:dd:00:13")["name"] == "LOBBY-2"


def _device_type(client: TestClient, org, headers, name: str, **fields) -> str:
    response = client.post(
        f"/api/v1/organizations/{org.id}/device-types", headers=headers, json={"name": name, **fields}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_name_follows_the_device_type_picked_at_approval(client: TestClient, db_session: Session) -> None:
    org, headers, key = _org_with_key(client, db_session)
    nuc = _device_type(client, org, headers, "Intel NUC 13")
    # What a card from the imager sends with agent 0.2.0: the hostname as its name, no board model.
    submitted = _submit(client, key, name="raspberrypi", hostname="raspberrypi", mac_addresses=["aa:bb:cc:dd:00:20"])
    row = client.get(f"/api/v1/organizations/{org.id}/enrollment-requests", headers=headers).json()[0]
    assert row["suggested_name"] == "raspberrypi-dd0020"
    assert row["name_suffix"] == "dd0020"
    assert row["suggested_device_type_id"] is None

    _approve(client, org, headers, submitted["request_id"], device_type_id=nuc)
    assert _claim(client, submitted)["name"] == "intelnuc13-dd0020"


def test_board_model_names_the_device_and_suggests_its_type(client: TestClient, db_session: Session) -> None:
    org, headers, key = _org_with_key(client, db_session)
    pi5 = _device_type(client, org, headers, "Pi five", slug="rpi5", manufacturer="Raspberry Pi", model="5 Model B")
    _device_type(client, org, headers, "Raspberry Pi 4")
    known = _submit(
        client,
        key,
        hostname="meteoros",
        mac_addresses=["2c:cf:67:a1:b2:c3"],
        hardware_model="Raspberry Pi 5 Model B Rev 1.0",
    )
    unknown = _submit(
        client,
        key,
        hostname="meteoros",
        mac_addresses=["2c:cf:67:a1:b2:c4"],
        hardware_model="Raspberry Pi Zero 2 W Rev 1.0",
    )
    rows = {
        row["id"]: row
        for row in client.get(f"/api/v1/organizations/{org.id}/enrollment-requests", headers=headers).json()
    }
    assert rows[known["request_id"]]["suggested_name"] == "rpi5-a1b2c3"
    assert rows[known["request_id"]]["suggested_device_type_id"] == pi5
    assert rows[known["request_id"]]["hardware_model"] == "Raspberry Pi 5 Model B Rev 1.0"
    assert rows[unknown["request_id"]]["suggested_name"] == "raspberrypizero2w-a1b2c4"
    assert rows[unknown["request_id"]]["suggested_device_type_id"] is None

    _approve(client, org, headers, known["request_id"])
    assert _claim(client, known)["name"] == "rpi5-a1b2c3"


def test_a_chosen_name_does_not_follow_the_type(client: TestClient, db_session: Session) -> None:
    org, headers, key = _org_with_key(client, db_session)
    nuc = _device_type(client, org, headers, "Intel NUC 13")
    submitted = _submit(client, key, name="Lobby", hostname="raspberrypi", mac_addresses=["aa:bb:cc:dd:00:21"])
    row = client.get(f"/api/v1/organizations/{org.id}/enrollment-requests", headers=headers).json()[0]
    assert (row["suggested_name"], row["name_suffix"]) == ("Lobby", None)
    _approve(client, org, headers, submitted["request_id"], device_type_id=nuc)
    assert _claim(client, submitted)["name"] == "Lobby"


def test_reregistering_with_the_hostname_as_name_keeps_the_device_name(client: TestClient, db_session: Session) -> None:
    _org, _headers, token, _release = _setup_release(client, db_session)
    first = _register_device(client, token, name="Lobby", mac="aa:bb:cc:dd:00:22")
    again = client.post(
        "/api/v1/agent/register",
        json={"token": token, "name": "raspberrypi", "hostname": "raspberrypi", "mac_addresses": ["aa:bb:cc:dd:00:22"]},
    )
    assert again.status_code in (200, 201), again.text
    assert (again.json()["device_id"], again.json()["name"]) == (first["device_id"], "Lobby")
