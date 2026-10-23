"""Admin-only transport for inference performed outside the web service."""
from functools import partial
from typing import Annotated
from uuid import UUID
import json
import psycopg2
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from core.auth import require_admin
from core.database import get_database_dsn
from services.extraction.bounding_box import BoundingBox
from services.extraction.local.application import LocalExtractionApplication
from services.extraction.local.repository import LocalExtractionRepository, LocalFlyerNotFound
from services.extraction.local.session import LocalExtractionConflict
from services.extraction.service import ExtractionService
from services.repositories.flyer_source_cache import FlyerSourceIntegrityError

router = APIRouter()


class WorkerRequest(BaseModel):
    worker_id: UUID


class ClaimRequest(WorkerRequest):
    model: str = Field(min_length=1, max_length=150)
    pages_total: int = Field(ge=1, le=500)
    chunk_size_pages: int = Field(ge=1, le=2)
    file_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    image_dpi: int = Field(default=150, ge=72, le=300)
    context_tokens: int = Field(default=32768, ge=4096, le=131072)
    max_output_tokens: int = Field(default=16384, ge=1024, le=65536)
    prompt_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class ChunkRequest(WorkerRequest):
    products: list[dict] = Field(max_length=500)

    @field_validator('products')
    @classmethod
    def valid_products(cls, products):
        if len(json.dumps(products, allow_nan=False).encode()) > 1_000_000:
            raise ValueError('Chunk exceeds 1 MB')
        for product in products:
            if product.get('packshot_bbox') is not None:
                if not isinstance(product['packshot_bbox'], dict):
                    raise ValueError('Named XY coordinates are required')
                BoundingBox.parse(product['packshot_bbox'])
        return products


class FailureRequest(WorkerRequest):
    message: str = Field(min_length=1, max_length=500)


def application():
    dsn = get_database_dsn()
    if not dsn:
        raise HTTPException(503, 'Local extraction requires server-side DB_DSN or DATABASE_URL')
    service = ExtractionService(provider=object())
    repository = LocalExtractionRepository(partial(psycopg2.connect, dsn))
    return LocalExtractionApplication(repository, service.prepare_local_offer_rows,
                                      service.save_local_packshots, service.local_pdf_page_count)


def invoke(operation, *args):
    try:
        return operation(*args)
    except LocalFlyerNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except (LocalExtractionConflict, FlyerSourceIntegrityError) as exc:
        raise HTTPException(409, str(exc)) from exc


Admin = Annotated[dict, Depends(require_admin)]
Application = Annotated[LocalExtractionApplication, Depends(application)]


@router.post('/{flyer_id}/local-extraction/claim')
def claim(flyer_id: UUID, body: ClaimRequest, profile: Admin, service: Application):
    config = body.model_dump(exclude={'worker_id'})
    return invoke(service.claim, str(flyer_id), profile['id'], str(body.worker_id), config)


@router.post('/{flyer_id}/local-extraction/heartbeat')
def heartbeat(flyer_id: UUID, body: WorkerRequest, profile: Admin, service: Application):
    return invoke(service.heartbeat, str(flyer_id), profile['id'], str(body.worker_id))


@router.put('/{flyer_id}/local-extraction/chunks/{index}')
def chunk(flyer_id: UUID, index: int, body: ChunkRequest, profile: Admin, service: Application):
    if index < 1:
        raise HTTPException(422, 'Chunk index must be positive')
    return invoke(service.chunk, str(flyer_id), profile['id'], str(body.worker_id), index, body.products)


@router.post('/{flyer_id}/local-extraction/finish')
def finish(flyer_id: UUID, body: WorkerRequest, profile: Admin, service: Application):
    return invoke(service.finish, str(flyer_id), profile['id'], str(body.worker_id))


@router.post('/{flyer_id}/local-extraction/fail')
def fail(flyer_id: UUID, body: FailureRequest, profile: Admin, service: Application):
    return invoke(service.fail, str(flyer_id), profile['id'], str(body.worker_id), body.message)


@router.get('/local-extraction/capabilities')
def capabilities(profile: Admin, service: Application):
    return {"protocol_version": 1, "lease_seconds": 600, "chunk_size_pages": [1, 2]}
