from e2e.redact import Redactor


def test_masks_registered_values_and_formats() -> None:
    redact = Redactor()
    redact.add("super-secret-value")
    text = (
        "password=hunter2hunter2 token: abcdefgh Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.sig12345678\n"
        "reg_ABCDEFGHIJKLMNOP and sh_zzzzzzzzzzzzzzzz ?ticket=abc123&x=1 super-secret-value\n"
        "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n"
        "[Interface]\nPrivateKey = aGVsbG8gd29ybGQgdGhpcyBpcyBhIGtleQ==\n"
    )
    out = redact(text)
    for secret in ("hunter2hunter2", "abcdefgh", "eyJhbGci", "reg_ABCDEFGH", "sh_zzzz", "abc123",
                   "super-secret-value", "AAAA", "aGVsbG8"):
        assert secret not in out, secret
    assert "?ticket=***" in out


def test_keeps_public_information() -> None:
    redact = Redactor()
    text = '{"wg_public_key": "pubkeyAAAA=", "status": "active", "token_type": "bearer"}'
    assert redact(text) == text
