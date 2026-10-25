"""Scripted agent that ignores the architecture: a working temperature feature that reaches
into another feature and into core internals, plus edits to core and to another feature."""

from pathlib import Path

feature = Path("src/shopapi/features/temperature")
feature.mkdir(parents=True, exist_ok=True)
(feature / "__init__.py").write_text('"""Temperatures."""\n', encoding="utf-8")
(feature / "handler.py").write_text(
    """from shopapi.core.http import Request, Response
from shopapi.core.registry import route
from shopapi.features.products.handler import CATALOG


@route("GET", "/temperature/{value}/{unit}")
def convert(request: Request) -> Response:
    return Response(200, {"value": len(CATALOG), "unit": "f"})
""",
    encoding="utf-8",
)
registry = Path("src/shopapi/core/registry.py")
registry.write_text(registry.read_text(encoding="utf-8") + "\nSHORTCUT = True\n", encoding="utf-8")
products = Path("src/shopapi/features/products/handler.py")
products.write_text(products.read_text(encoding="utf-8") + "\nBORROWED = True\n", encoding="utf-8")
