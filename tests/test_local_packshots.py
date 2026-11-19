import base64
from io import BytesIO
from unittest.mock import Mock
import pytest
from PIL import Image
from services.repositories.local_packshots import LocalPackshotWriter, decode_image
from services.extraction.local.session import LocalExtractionConflict

BOX = dict(x_min=100, y_min=200, x_max=400, y_max=700)


def image_payload(color='red', size=(80, 150), kind='WEBP'):
    with Image.new('RGB', size, color) as image:
        buffer = BytesIO()
        image.save(buffer, format=kind)
    return base64.b64encode(buffer.getvalue()).decode()


def test_variants_share_one_crop_without_offer_read_or_update():
    sb = Mock()
    bucket = sb.storage.from_.return_value
    bucket.get_public_url.side_effect = lambda path: 'https://storage.test/'+path
    writer = LocalPackshotWriter(lambda: sb)
    rows = [dict(packshot_source_page=1, packshot_bbox=[100, 200, 400, 700]) for _ in range(2)]
    payload = [dict(page=1, bbox=BOX, image=image_payload())]
    result = writer.attach(rows, {'id': 'flyer'}, payload)
    assert result[0]['image_url'] == result[1]['image_url']
    bucket.upload.assert_called_once()
    sb.table.assert_not_called()
    writer.attach(rows, {'id': 'flyer'}, payload)
    assert bucket.upload.call_args_list[0].kwargs['path'] == bucket.upload.call_args_list[1].kwargs['path']


@pytest.mark.parametrize('payload', ['bad', image_payload(kind='PNG'), image_payload(size=(641, 100))])
def test_invalid_image_rejected(payload):
    with pytest.raises(LocalExtractionConflict):
        decode_image(payload)


def test_missing_and_duplicate_images_rejected_before_storage_write():
    sb = Mock()
    writer = LocalPackshotWriter(lambda: sb)
    rows = [dict(packshot_source_page=1, packshot_bbox=[100, 200, 400, 700])]
    with pytest.raises(LocalExtractionConflict, match='Missing crop'):
        writer.attach(rows, {'id': 'flyer'}, [])
    image = dict(page=1, bbox=BOX, image=image_payload())
    with pytest.raises(LocalExtractionConflict, match='Duplicate'):
        writer.attach(rows, {'id': 'flyer'}, [image, image])
    sb.storage.from_.assert_not_called()
