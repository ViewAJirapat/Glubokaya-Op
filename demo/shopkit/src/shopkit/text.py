"""Text helpers."""


def slugify(text: str) -> str:
    """Turn a product title into a URL slug."""
    return text.strip().lower().replace(" ", "-")
