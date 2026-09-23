"""
states.py — FSM-состояния бота.

Состояния используются только там, где бот ждёт ввода текста. Нажатия кнопок от состояний
не зависят: FSM хранится в памяти и сбрасывается при перезапуске бота, а кнопки должны
продолжать работать и после перезапуска.
"""
from aiogram.fsm.state import State, StatesGroup


class InterviewStates(StatesGroup):
    waiting_answer = State()


class ResumeStates(StatesGroup):
    waiting_user_resume = State()


class SupportStates(StatesGroup):
    waiting_support_message = State()


class ReviewStates(StatesGroup):
    waiting_text = State()


class PaymentStates(StatesGroup):
    waiting_promocode = State()


class AdminStates(StatesGroup):
    waiting_for_auth_key = State()
    waiting_user_search = State()
    waiting_user_message = State()
    waiting_broadcast = State()
    waiting_promo_code = State()
    waiting_promo_discount = State()
    waiting_promo_uses = State()
    waiting_camp_tag = State()
    waiting_camp_desc = State()
    waiting_add_admin_id = State()
