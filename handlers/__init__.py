"""
handlers/__init__.py — объединение роутеров приложения.
"""
from aiogram import Router
from .admin import router as admin_router
from .payment import router as payment_router
from .resume import router as resume_router
from .interview import router as interview_router
from .start import router as start_router

main_router = Router(name="main")

main_router.include_router(admin_router)
main_router.include_router(payment_router)
main_router.include_router(resume_router)
main_router.include_router(interview_router)
# start_router подключается последним, чтобы catch-all для неизвестных команд
# не перехватывал команды из других роутеров (/pay, /admin, /test и т.д.)
main_router.include_router(start_router)