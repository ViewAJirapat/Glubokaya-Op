from shopkit.text import slugify


def test_simple_title() -> None:
    assert slugify("Hello World") == "hello-world"


def test_surrounding_whitespace() -> None:
    assert slugify("  Shop Kit ") == "shop-kit"
