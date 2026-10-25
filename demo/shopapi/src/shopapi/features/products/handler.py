from shopapi.core.api import HTTPError, Request, Response, route

CATALOG = {
    "tea-01": {"name": "Green Tea", "price_cents": 450},
    "mug-02": {"name": "Blue Mug", "price_cents": 1200},
}


@route("GET", "/products")
def list_products(request: Request) -> Response:
    return Response(200, {"skus": sorted(CATALOG)})


@route("GET", "/products/{sku}")
def get_product(request: Request) -> Response:
    sku = request.params["sku"]
    if sku not in CATALOG:
        raise HTTPError(404, f"unknown sku {sku!r}")
    return Response(200, {"sku": sku, **CATALOG[sku]})
