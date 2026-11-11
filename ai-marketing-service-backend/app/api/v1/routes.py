"""API v1 route registry."""

from fastapi import APIRouter

from app.api.v1 import ads, auth, billing, integrations, projects

router = APIRouter()

router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
router.include_router(projects.router, prefix="/projects", tags=["Projects"])
router.include_router(integrations.router, prefix="/integrations", tags=["Integrations"])
router.include_router(ads.router, prefix="/ads", tags=["Ads"])
router.include_router(billing.router, prefix="/billing", tags=["Billing"])

# Future phases:
# from app.api.v1 import analysis, ideas, posts, media, schedule, analytics
# router.include_router(analysis.router, prefix="/analysis", tags=["Analysis"])
# router.include_router(ideas.router, prefix="/ideas", tags=["Ideas"])
# router.include_router(posts.router, prefix="/posts", tags=["Posts"])
# router.include_router(media.router, prefix="/media", tags=["Media"])
# router.include_router(schedule.router, prefix="/schedule", tags=["Schedule"])
# router.include_router(analytics.router, prefix="/analytics", tags=["Analytics"])
