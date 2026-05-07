from core.runtime import ensure_supported_python

ensure_supported_python()

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import (
    admin_products,
    analytics,
    favorites,
    flyer_requests,
    flyers,
    invite,
    lists,
    offers,
    optimize,
    products,
    purchases,
    push,
    supermarkets,
    users,
)
from core.config import settings
from services.flyer_cleanup import FlyerCleanupService
from services.purchased_items_cleanup import PurchasedItemsCleanupService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        FlyerCleanupService().run,
        CronTrigger(hour=0, minute=0, timezone="Europe/Rome"),
        id="flyer_cleanup",
        replace_existing=True,
    )
    scheduler.add_job(
        PurchasedItemsCleanupService().run,
        CronTrigger(hour=0, minute=0, timezone="Europe/Rome"),
        id="purchased_items_cleanup",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Nightly schedulers started (fire daily at midnight Europe/Rome)")
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="GiroSpesa API",
    description="Backend API for GiroSpesa — grocery deal optimizer.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(favorites.router, prefix="/favorites", tags=["favorites"])
app.include_router(flyers.router, prefix="/flyers", tags=["flyers"])
app.include_router(products.router, prefix="/products", tags=["products"])
app.include_router(supermarkets.router, prefix="/supermarkets", tags=["supermarkets"])
app.include_router(lists.router, prefix="/lists", tags=["lists"])
app.include_router(invite.router, prefix="/invite", tags=["invite"])
app.include_router(optimize.router, prefix="/optimize", tags=["optimize"])
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
app.include_router(push.router, prefix="/push", tags=["push"])
app.include_router(flyer_requests.router, prefix="/flyer-requests", tags=["flyer-requests"])
app.include_router(purchases.router, prefix="/purchases", tags=["purchases"])
app.include_router(admin_products.router, prefix="/admin/products", tags=["admin-products"])
app.include_router(offers.router, prefix="/offers", tags=["offers"])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
