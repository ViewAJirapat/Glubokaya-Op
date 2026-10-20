import pytest

from shopkit import parse_duration
from shopkit.durations import parse_duration as direct


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("45s", 45),
        ("1h30m", 5400),
        ("2d 3h", 183600),
        ("0s", 0),
        ("1d2h3m4s", 93784),
        ("  10m  ", 600),
        ("90m", 5400),
    ],
)
def test_valid(text: str, seconds: int) -> None:
    assert parse_duration(text) == seconds


@pytest.mark.parametrize(
    "text",
    ["", "   ", "10", "h", "1x", "-5m", "+5m", "1.5h", "30m1h", "1h1h", "1H", "1h 30"],
)
def test_invalid(text: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(text)


def test_exported_from_package() -> None:
    assert direct is parse_duration


def test_returns_int() -> None:
    assert type(parse_duration("1m")) is int
