"""Optional photo-match helper (docs/STATUS.md). Not on the hot path unless a shopper photo exists."""

from __future__ import annotations

from .decisions import ask

QUESTION = {
    "photo_match": {
        "type": "noul",
        "instructions": (
            "Is the product in image 2 the same kind of item as in image 1 "
            "(same type, color, design)?"
        ),
    }
}


def _image_block(image) -> dict:
    if isinstance(image, dict) and image.get("type") == "image":
        return image
    if isinstance(image, str) and image.startswith("http"):
        return {"type": "image", "uri": image, "mime_type": "image/jpeg", "resolution": "low"}
    if isinstance(image, str) and len(image) > 32:
        # raw base64 data, never a file path we would fetch remotely
        return {"type": "image", "mime_type": "image/jpeg", "data": image, "resolution": "low"}
    raise ValueError("photo_match expected an image uri, data string, or image content block")


def photo_match(shopper_photo, product_image_uri) -> dict:
    """noul question over shopper photo (image 1) + product image uri (image 2). Never downloads."""
    blocks = [_image_block(shopper_photo), _image_block(product_image_uri)]
    return ask(blocks, QUESTION)
