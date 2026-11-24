import pytest

from e2e.state import Host
from e2e.suite.s4_onboarding import configure


class FakeContext:
    api_url = "http://198.51.100.1"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def meteorcli(self, device: Host, args: str) -> None:
        self.calls.append(args)


@pytest.mark.parametrize(("user", "expected"), [
    ("ubuntu", "config --api-base http://198.51.100.1 --ssh-user ubuntu"),
    ("root", "config --api-base http://198.51.100.1"),
])
def test_configure_restores_the_terminal_account(user: str, expected: str) -> None:
    ctx = FakeContext()
    configure(ctx, Host("device-1", "device", "198.51.100.3", user))
    assert ctx.calls == [expected]
