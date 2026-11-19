"""Signed desired software: Ed25519 verification, key pinning and reconcile refusal."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from edge_agent import signing
from edge_agent.client import AgentApiError
from edge_agent.signing import (
    DESIRED_CONTEXT,
    ENDORSEMENT_CONTEXT,
    ManifestVerifier,
    _verify_pure,
    canonical_json,
    desired_payload,
)
from edge_agent.software import _ensure_artifact, reconcile_software

DEVICE = "6f9c2a1e-0000-4000-8000-000000000001"

# RFC 8032 section 7.1, tests 1 and 2.
RFC_VECTORS = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
]


# --- a tiny test-only signer (the agent itself only verifies) -------------


def _compress(point: tuple[int, int, int, int]) -> bytes:
    zinv = pow(point[2], signing._P - 2, signing._P)
    x, y = point[0] * zinv % signing._P, point[1] * zinv % signing._P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _keypair(seed: bytes) -> tuple[bytes, int, bytes]:
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return _compress(signing._mul(scalar, signing._G)), scalar, digest[32:]


def _sign(seed: bytes, message: bytes) -> bytes:
    public, scalar, prefix = _keypair(seed)
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % signing._L
    big_r = _compress(signing._mul(r, signing._G))
    k = int.from_bytes(hashlib.sha512(big_r + public + message).digest(), "little") % signing._L
    return big_r + ((r + k * scalar) % signing._L).to_bytes(32, "little")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _key(seed: bytes, *, endorsed_by: bytes | None = None) -> dict[str, Any]:
    public = _keypair(seed)[0]
    key_id = hashlib.sha256(public).hexdigest()[:16]
    key: dict[str, Any] = {"key_id": key_id, "public_key": _b64(public), "endorsed_by": None, "endorsement": None}
    if endorsed_by is not None:
        message = ENDORSEMENT_CONTEXT + canonical_json({"key_id": key_id, "public_key": key["public_key"]})
        key["endorsed_by"] = hashlib.sha256(_keypair(endorsed_by)[0]).hexdigest()[:16]
        key["endorsement"] = _b64(_sign(endorsed_by, message))
    return key


SEED_A = bytes(range(32))
SEED_B = bytes(range(1, 33))
SEED_EVIL = bytes(range(2, 34))


def _item(seed: bytes = SEED_A, **changes: Any) -> dict[str, Any]:
    item = {
        "workload_key": "cam.service",
        "kind": "systemd",
        "release_id": "8a1b",
        "release_name": "cam",
        "release_version": "1.2.0",
        "artifact_id": "art-1",
        "artifact_sha256": hashlib.sha256(b"unit").hexdigest(),
        "revision": 3,
        "spec": {"unit_name": "cam.service", "note": "Grüße", "ratio": 0.5},
    }
    signed = json.loads(json.dumps(item))  # as received over HTTP
    signed["key_id"] = _key(seed)["key_id"]
    signed["signature"] = _b64(_sign(seed, DESIRED_CONTEXT + canonical_json(desired_payload(DEVICE, signed))))
    signed.update(changes)
    return signed


def _verifier(tmp_path: Path, keys: list[dict[str, Any]] | None) -> tuple[ManifestVerifier, MagicMock]:
    fetch = MagicMock(return_value=keys)
    return ManifestVerifier(tmp_path / "signing-keys.json", DEVICE, fetch), fetch


# --- tests ----------------------------------------------------------------


@pytest.mark.parametrize(("seed", "public", "message", "signature"), RFC_VECTORS)
def test_rfc8032_vectors(seed: str, public: str, message: str, signature: str) -> None:
    assert _keypair(bytes.fromhex(seed))[0].hex() == public
    assert _sign(bytes.fromhex(seed), bytes.fromhex(message)).hex() == signature
    assert _verify_pure(bytes.fromhex(public), bytes.fromhex(message), bytes.fromhex(signature))
    assert not _verify_pure(bytes.fromhex(public), b"other", bytes.fromhex(signature))
    bad = bytearray(bytes.fromhex(signature))
    bad[5] ^= 1
    assert not _verify_pure(bytes.fromhex(public), bytes.fromhex(message), bytes(bad))


def test_first_use_pins_keys_and_accepts_a_valid_item(tmp_path: Path) -> None:
    verifier, fetch = _verifier(tmp_path, [_key(SEED_A)])
    assert verifier.check(_item()) is None
    stored = json.loads((tmp_path / "signing-keys.json").read_text())
    assert stored["keys"] == {_key(SEED_A)["key_id"]: _key(SEED_A)["public_key"]}
    assert stored["device_id"] == DEVICE
    assert verifier.check(_item()) is None
    assert fetch.call_count == 1  # pinned keys are reused


def test_pins_for_another_credential_are_ignored(tmp_path: Path) -> None:
    from edge_agent.signing import credential_fingerprint

    old = ManifestVerifier(
        tmp_path / "signing-keys.json",
        DEVICE,
        MagicMock(return_value=[_key(SEED_A)]),
        credential_fingerprint=credential_fingerprint("old-token"),
    )
    assert old.check(_item()) is None

    fresh = ManifestVerifier(
        tmp_path / "signing-keys.json",
        DEVICE,
        MagicMock(return_value=[_key(SEED_B, endorsed_by=SEED_A)]),
        credential_fingerprint=credential_fingerprint("new-token"),
    )
    # Stale pins belong to the previous credential: first-use pin of the new fetch.
    assert fresh.check(_item(SEED_B)) is None
    assert json.loads((tmp_path / "signing-keys.json").read_text())["credential_fp"] == (
        credential_fingerprint("new-token")
    )


@pytest.mark.parametrize(
    "change",
    [
        {"revision": 2},
        {"spec": {"unit_name": "evil.service"}},
        {"artifact_sha256": "0" * 64},
        {"release_id": "other"},
    ],
)
def test_tampered_items_are_refused(tmp_path: Path, change: dict[str, Any]) -> None:
    verifier, _ = _verifier(tmp_path, [_key(SEED_A)])
    assert "does not verify" in (verifier.check(_item(**change)) or "")


def test_item_signed_for_another_device_is_refused(tmp_path: Path) -> None:
    verifier, _ = _verifier(tmp_path, [_key(SEED_A)])
    other = ManifestVerifier(tmp_path / "signing-keys.json", "another-device", verifier.fetch_keys)
    assert other.check(_item()) is not None


def test_unsigned_items_are_refused_once_keys_are_pinned(tmp_path: Path) -> None:
    verifier, _ = _verifier(tmp_path, [_key(SEED_A)])
    unsigned = _item(signature=None, key_id=None)
    assert "not signed" in (verifier.check(unsigned) or "")


def test_unsigned_items_pass_while_the_control_plane_does_not_sign(tmp_path: Path) -> None:
    verifier, _ = _verifier(tmp_path, None)  # older server: /agent/signing-keys is 404
    assert verifier.check(_item(signature=None, key_id=None)) is None
    assert not (tmp_path / "signing-keys.json").exists()


def test_rotated_key_is_trusted_only_when_endorsed(tmp_path: Path) -> None:
    verifier, fetch = _verifier(tmp_path, [_key(SEED_A)])
    assert verifier.check(_item()) is None

    fetch.return_value = [_key(SEED_A), _key(SEED_B, endorsed_by=SEED_A)]
    assert verifier.check(_item(SEED_B)) is None

    # A key the server offers without a valid endorsement (or endorsed by itself) is not pinned.
    fetch.return_value = [_key(SEED_A), _key(SEED_EVIL, endorsed_by=SEED_EVIL)]
    assert "untrusted key" in (verifier.check(_item(SEED_EVIL)) or "")


def test_key_fetch_failure_refuses_and_retries(tmp_path: Path) -> None:
    verifier, fetch = _verifier(tmp_path, None)
    fetch.side_effect = AgentApiError(503, "unavailable", "down")
    assert "will retry" in (verifier.check(_item()) or "")


def test_reconcile_does_not_apply_a_refused_item(tmp_path: Path) -> None:
    client = MagicMock()
    client.get_software.return_value = [_item(revision=99)]
    verifier, _ = _verifier(tmp_path, [_key(SEED_A)])
    with patch("edge_agent.software._ensure_artifact") as ensure, patch("edge_agent.software._apply_systemd") as apply:
        reports = reconcile_software(client, "dev", tmp_path / "artifacts", verifier=verifier)
    assert reports[0]["sync_status"] == "failed"
    assert "does not verify" in reports[0]["last_error"]
    ensure.assert_not_called()
    apply.assert_not_called()


def test_reconcile_refuses_an_older_revision(tmp_path: Path) -> None:
    from edge_agent.software import _save_applied_workload, applied_state_path

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _save_applied_workload(
        applied_state_path(artifacts),
        workload_key="cam.service",
        release_id="8a1b",
        revision=3,
    )
    # Signature must verify so the refusal is specifically about the revision.
    item = _item(SEED_A)
    item["revision"] = 2
    item["signature"] = _b64(_sign(SEED_A, DESIRED_CONTEXT + canonical_json(desired_payload(DEVICE, item))))
    client = MagicMock()
    client.get_software.return_value = [item]
    verifier, _ = _verifier(tmp_path, [_key(SEED_A)])
    with patch("edge_agent.software._ensure_artifact") as ensure, patch("edge_agent.software._apply_systemd") as apply:
        reports = reconcile_software(client, "dev", artifacts, verifier=verifier)
    assert reports[0]["sync_status"] == "failed"
    assert "older than applied revision" in reports[0]["last_error"]
    ensure.assert_not_called()
    apply.assert_not_called()


def test_cached_artifact_bytes_must_match_the_signed_checksum(tmp_path: Path) -> None:
    from edge_agent.software import checksum_sidecar_path

    dest = tmp_path / "art-1"
    dest.write_bytes(b"tampered")
    checksum_sidecar_path(dest).write_text(hashlib.sha256(b"unit").hexdigest() + "\n", encoding="utf-8")
    client = MagicMock()

    def download(*, destination: Path, **_kwargs: Any) -> str:
        destination.write_bytes(b"unit")
        return hashlib.sha256(b"unit").hexdigest()

    client.download_artifact.side_effect = download
    with patch("edge_agent.software._report_updating"):
        path = _ensure_artifact(
            client,
            "dev",
            artifact_id="art-1",
            workload_key="cam.service",
            artifacts_dir=tmp_path,
            expected_sha256=hashlib.sha256(b"unit").hexdigest(),
        )
    assert path.read_bytes() == b"unit"
    client.download_artifact.assert_called_once()


def test_download_must_match_the_signed_checksum(tmp_path: Path) -> None:
    client = MagicMock()

    def download(*, destination: Path, **_kwargs: Any) -> str:
        destination.write_bytes(b"tampered")
        return hashlib.sha256(b"tampered").hexdigest()

    client.download_artifact.side_effect = download
    with patch("edge_agent.software._report_updating"), pytest.raises(AgentApiError) as error:
        _ensure_artifact(
            client,
            "dev",
            artifact_id="art-1",
            workload_key="cam.service",
            artifacts_dir=tmp_path,
            expected_sha256=hashlib.sha256(b"unit").hexdigest(),
        )
    assert error.value.code == "checksum_mismatch"
    assert not (tmp_path / "art-1").exists()


def test_new_device_credentials_forget_pinned_keys(tmp_path: Path) -> None:
    from edge_agent.persist import persist_device_secrets

    (tmp_path / "signing-keys.json").write_text('{"keys": {"old": "x"}}', encoding="utf-8")
    persist_device_secrets(tmp_path, tmp_path / "device-token", "dev_new", None)
    assert not (tmp_path / "signing-keys.json").exists()
