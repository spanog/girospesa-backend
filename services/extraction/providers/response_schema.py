"""Structured vision response contract shared with the local inference adapter."""


def extraction_response_schema(page_count: int = 1) -> dict:
    coordinates = {name: {"type": "integer", "minimum": 0, "maximum": 1000}
                   for name in ("x_min", "y_min", "x_max", "y_max")}
    box = {"type": "object", "properties": coordinates,
           "required": list(coordinates), "additionalProperties": False}
    product = {"type": "object", "properties": {
        "name": {"type": "string"}, "price_current": {"type": "number"},
        "packshot_bbox": {"anyOf": [box, {"type": "null"}]}},
        "required": ["name", "price_current", "packshot_bbox"], "additionalProperties": True}
    if page_count > 1:
        product["properties"]["source_page"] = {"type": "integer", "minimum": 1, "maximum": page_count}
        product["required"].append("source_page")
    return {"type": "object", "properties": {"products": {"type": "array", "items": product}},
            "required": ["products"], "additionalProperties": False}
