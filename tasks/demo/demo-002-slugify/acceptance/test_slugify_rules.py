import pytest

from shopkit.text import slugify


@pytest.mark.parametrize(
    ("raw", "slug"),
    [
        ("Hello World", "hello-world"),
        ("Crème Brûlée!", "creme-brulee"),
        ("C++ & Python 3.12", "c-python-3-12"),
        ("  --Already--Slugged--  ", "already-slugged"),
        ("Ünïcödé   Spaces\tand\nlines", "unicode-spaces-and-lines"),
        ("Naïve café", "naive-cafe"),
        ("!!!", ""),
        ("", ""),
    ],
)
def test_slugify(raw: str, slug: str) -> None:
    assert slugify(raw) == slug
