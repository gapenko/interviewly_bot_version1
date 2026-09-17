"""
states.py — FSM-состояния.
"""
from aiogram.fsm.state import State, StatesGroup


class InterviewStates(StatesGroup):
    welcome = State()
    selecting_track = State()
    ready_to_start = State()
    waiting_answer = State()
    paused = State()
    waiting_payment = State()
    finished = State()


class ResumeStates(StatesGroup):
    waiting_full_name = State()
    waiting_contacts = State()
    waiting_experience_years = State()
    waiting_user_resume = State()


class SupportStates(StatesGroup):
    waiting_support_message = State()


class ReviewStates(StatesGroup):
    waiting_rating = State()
    waiting_text = State()


class AdminStates(StatesGroup):
    waiting_for_auth_key = State()
    waiting_for_broadcast_msg = State()
    waiting_support_reply = State()
    waiting_camp_tag = State()
    waiting_camp_desc = State()
    waiting_user_search = State()
    waiting_promo_code = State()
    waiting_promo_discount = State()
    waiting_add_admin_id = State()