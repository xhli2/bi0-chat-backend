from fastapi import APIRouter

from app.api.v1.admin import router as admin_router
from app.api.v1.agents import router as agents_router
from app.api.v1.auth import router as auth_router
from app.api.v1.collaboration import router as collaboration_router
from app.api.v1.items import router as items_router
from app.api.v1.sessions import router as sessions_router
from app.api.v1.tasks import router as tasks_router

router = APIRouter()
router.include_router(admin_router)
router.include_router(auth_router)
router.include_router(collaboration_router)
router.include_router(items_router)
router.include_router(sessions_router)
router.include_router(tasks_router)
router.include_router(agents_router)
