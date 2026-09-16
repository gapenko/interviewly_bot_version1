"""
handlers/resume.py — вспомогательные команды меню резюме и сброса.
"""
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from storage import reset_user
from keyboards import get_resume_menu_keyboard, get_tracks_keyboard
from states import InterviewStates

router = Router(name="resume")


@router.callback_query(F.data == "cmd_reset_confirm")
async def process_reset_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Прогресс сброшен", show_alert=True)
    await reset_user(callback.from_user.id)
    await state.clear()
    await state.set_state(InterviewStates.selecting_track)
    await callback.message.answer(
        "🔄 <b>Прогресс сброшен.</b>\nВыберите направление для нового ассессмента:",
        reply_markup=get_tracks_keyboard(),
        parse_mode="HTML"
    )


@router.message(Command("resume"))
async def cmd_resume_menu(message: Message):
    """Сервисная команда вызова меню резюме."""
    await message.answer("Управление вашим резюме:", reply_markup=get_resume_menu_keyboard())