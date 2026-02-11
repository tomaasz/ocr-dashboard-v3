"""OCR Dashboard V2 - Routes Package"""

from .dashboard import router as dashboard_router
from .limits import router as limits_router
from .profiles import router as profiles_router
from .profiles import single_router as profiles_single_router
from .settings import router as settings_router

__all__ = [
    "dashboard_router",
    "limits_router",
    "profiles_router",
    "profiles_single_router",
    "settings_router",
]
