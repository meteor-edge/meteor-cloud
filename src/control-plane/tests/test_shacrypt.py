"""SHA-512 crypt against the specification's vectors and the system's own implementation."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from app.core.shacrypt import sha512_crypt


@pytest.mark.parametrize(
    ("password", "salt", "rounds", "expected"),
    [
        (
            "Hello world!",
            "saltstring",
            None,
            "$6$saltstring$svn8UoSVapNtMuq1ukKS4tPQd8iKwSMHWjl/O817G3uBnIFNjnQJuesI68u4OTLiBFdcbYEdFCoEOfaS35inz1",
        ),
        (
            "Hello world!",
            "saltstringsaltstring",
            10000,
            "$6$rounds=10000$saltstringsaltst$OW1/O6BYHV6BcXZu8QVeXbDWra3Oeqh0sbHbbMCVNSnCM/UrjmM0Dp8vOuZeHBy/"
            "YTBmSK6H9qs/y3RnOaw5v.",
        ),
        (
            "This is just a test",
            "toolongsaltstring",
            5000,
            "$6$rounds=5000$toolongsaltstrin$lQ8jolhgVRVhY4b5pZKaysCLi0QBxGoNeKQzQ3glMhwllF7oGDZxUhx1yxdYcz/"
            "e1JSbq3y6JMxxl8audkUEm0",
        ),
    ],
)
def test_specification_vectors(password: str, salt: str, rounds: int | None, expected: str) -> None:
    assert sha512_crypt(password, salt=salt, rounds=rounds) == expected


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")
@pytest.mark.parametrize("password", ["pw", "correct horse battery staple", "pässwörd-ünïcode", "x" * 200])
def test_matches_openssl(password: str) -> None:
    ours = sha512_crypt(password)
    salt = ours.split("$")[2]
    theirs = subprocess.run(
        ["openssl", "passwd", "-6", "-salt", salt, "-stdin"], input=password, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert ours == theirs


def test_random_salt_each_time() -> None:
    assert sha512_crypt("pw") != sha512_crypt("pw")
