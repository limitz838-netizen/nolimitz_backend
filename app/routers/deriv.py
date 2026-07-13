from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from app.services.deriv_service import DerivService

router = APIRouter(prefix="/deriv", tags=["Deriv"])

service = DerivService()


@router.get("/status")
def deriv_status():
    return service.check_configuration()

@router.get("/login")
def deriv_login():
    return RedirectResponse(service.get_login_url())