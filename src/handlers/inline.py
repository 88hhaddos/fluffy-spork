"""Inline-режим: юзер пишет @закури в любом чате и получает варианты ответов."""
import logging
import asyncio

from aiogram import Router, F
from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent
from aiogram.filters import Command

from src.config import config
from src.personality import build_system_prompt

logger = logging.getLogger(__name__)

router = Router(name="inline")


@router.inline_query()
async def handle_inline_query(query: InlineQuery, db, ai_manager, context_manager):
    """Обрабатывает @бот запросы в любом чате."""
    text = query.query.strip()

    if not text or len(text) < 2:
        results = [
            InlineQueryResultArticle(
                id="hint",
                title="Закури на связи!",
                description="Напиши вопрос после @бот — Закури ответит прямо тут",
                input_message_content=InputTextMessageContent(
                    message_text="🐉 Закури здесь! Напиши свой вопрос после моего тега."
                ),
            )
        ]
        await query.answer(results, cache_time=10)
        return

    try:
        user_id = query.from_user.id
        username = query.from_user.username or query.from_user.first_name or "Кто-то"

        system_prompt = await build_system_prompt(
            db, chat_id=0, user_id=user_id, username=username,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{username}: {text}"},
        ]

        response = await asyncio.wait_for(
            ai_manager.chat_completion(
                messages=messages,
                temperature=0.7,
                max_tokens=300,
            ),
            timeout=25,
        )

        if not response or not response.strip():
            response = "Закури задумался. Попробуй ещё раз."

        results = [
            InlineQueryResultArticle(
                id="zakuri_answer",
                title=f"🐉 Закури отвечает",
                description=response[:100] + ("..." if len(response) > 100 else ""),
                input_message_content=InputTextMessageContent(
                    message_text=response[:4096]
                ),
            ),
        ]

        await query.answer(results, cache_time=5)

    except asyncio.TimeoutError:
        results = [
            InlineQueryResultArticle(
                id="timeout",
                title="Закури думает слишком долго...",
                description="Попробуй ещё раз через секунду",
                input_message_content=InputTextMessageContent(
                    message_text="🐉 Закури отвлёкся, попробуй ещё раз!"
                ),
            ),
        ]
        await query.answer(results, cache_time=5)

    except Exception as e:
        logger.error(f"Inline query error: {e}")
        results = [
            InlineQueryResultArticle(
                id="error",
                title="Закури запутался",
                description="Что-то пошло не так",
                input_message_content=InputTextMessageContent(
                    message_text="🐉 Закури не может ответить прямо сейчас, попробуй ещё раз!"
                ),
            ),
        ]
        await query.answer(results, cache_time=5)
