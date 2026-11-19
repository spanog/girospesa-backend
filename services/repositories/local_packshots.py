"""Validate client-rendered crops and publish immutable content-addressed images."""
from collections.abc import Callable
from typing import Any
import base64
import binascii
from hashlib import sha256
from io import BytesIO
from PIL import Image, UnidentifiedImageError
from services.extraction.bounding_box import BoundingBox, InvalidBoundingBox
from services.extraction.local.session import LocalExtractionConflict

MAX_PACKSHOT_BYTES = 524_288
MAX_PACKSHOT_SIDE = 640

type CropKey = tuple[int, int, int, int, int]


def crop_key(page: object, bbox: object) -> CropKey:
    if type(page) is not int or page < 1:
        raise LocalExtractionConflict('Packshot page must be a positive integer')
    try:
        return (page, *BoundingBox.parse(bbox).as_tuple())
    except InvalidBoundingBox as error:
        raise LocalExtractionConflict('Invalid packshot coordinates') from error


def decode_image(value: object) -> bytes:
    if not isinstance(value, str) or len(value) > 4 * ((MAX_PACKSHOT_BYTES + 2) // 3):
        raise LocalExtractionConflict('Invalid packshot size')
    try:
        content = base64.b64decode(value, validate=True)
        if len(content) > MAX_PACKSHOT_BYTES:
            raise LocalExtractionConflict('Invalid packshot size')
        with Image.open(BytesIO(content)) as image:
            if image.format != 'WEBP' or max(image.size) > MAX_PACKSHOT_SIDE or image.n_frames != 1:
                raise LocalExtractionConflict('Packshot must be a static WebP up to 640 pixels')
            image.verify()
    except (binascii.Error, UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise LocalExtractionConflict('Invalid WebP packshot') from error
    return content


def validated_images(packshots: list[dict]) -> dict[CropKey, bytes]:
    images = {}
    for item in packshots:
        if set(item) != {'page', 'bbox', 'image'}:
            raise LocalExtractionConflict('Invalid packshot fields')
        key = crop_key(item['page'], item['bbox'])
        if key in images:
            raise LocalExtractionConflict('Duplicate packshot coordinates')
        images[key] = decode_image(item['image'])
    return images


class LocalPackshotWriter:
    def __init__(self, supabase_factory: Callable[[], Any]) -> None:
        self._supabase_factory = supabase_factory

    def attach(self, rows: list[dict], flyer: dict, packshots: list[dict]) -> list[dict]:
        images = validated_images(packshots)
        selected = {}
        for row in rows:
            if row.get('packshot_bbox') is None:
                continue
            key = crop_key(row['packshot_source_page'], row['packshot_bbox'])
            if key not in images:
                raise LocalExtractionConflict('Missing crop for an extracted offer')
            selected[key] = images[key]
        urls = self._upload(flyer['id'], selected)
        for row in rows:
            if row.get('packshot_bbox') is not None:
                row['image_url'] = urls[crop_key(row['packshot_source_page'], row['packshot_bbox'])]
        return rows

    def _upload(self, flyer_id: str, images: dict[CropKey, bytes]) -> dict[CropKey, str]:
        if not images:
            return {}
        bucket = self._supabase_factory().storage.from_('product-images')
        urls = {}
        for key, content in images.items():
            path = f'local-extraction/{flyer_id}/{sha256(content).hexdigest()}.webp'
            bucket.upload(path=path, file=content, file_options={
                'content-type': 'image/webp', 'cache-control': '31536000', 'upsert': 'true'})
            urls[key] = bucket.get_public_url(path)
        return urls
