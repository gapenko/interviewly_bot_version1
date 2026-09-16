"""
states.py — FSM-состояния для прохождения интервью, работы с резюме, поддержки и ответа админа.
"""
from aiogram.fsm.state import State, StatesGroup


class InterviewStates(StatesGroup):
    # Главный экран приветствия
    welcome = State()

    # Выбор IT-направления
    selecting_track = State()

    # Экран с правилами после выбора направления
    ready_to_start = State()

    # Ожидание ответа пользователя на текущий вопрос
    waiting_answer = State()

    # Пауза / стоп интервью
    paused = State()

    # Ожидание оплаты для продолжения после бесплатных вопросов
    waiting_payment = State()

    # Собеседование завершено
    finished = State()


class ResumeStates(StatesGroup):
    # Ожидание данных для генерации Word-резюме
    waiting_full_name = State()
    waiting_contacts = State()
    waiting_experience_years = State()

    # Ожидание резюме пользователя для аудита
    waiting_user_resume = State()


class SupportStates(StatesGroup):
    # Ожидание текста обращения в поддержку от пользователя
    waiting_support_message = State()


class AdminStates(StatesGroup):
    waiting_for_auth_key = State()
    waiting_for_broadcast_msg = State()
    # Ожидание текста ответа админа конкретному пользователю
    waiting_support_reply = State()