import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, BufferedInputFile

from src.config import config
from src.filters import IsAdmin, IsPrivateChat
from src.keyboards import (
    main_menu_kb,
    settings_kb,
    providers_kb,
    provider_detail_kb,
    personality_kb,
    context_kb,
    admins_kb,
    admin_detail_kb,
    cancel_kb,
    presets_kb,
    NVIDIA_PRESETS,
    OPENROUTER_PRESETS,
)
from src.personality import load_personality_from_file

logger = logging.getLogger(__name__)

router = Router(name="admin_panel")


class AdminStates(StatesGroup):
    add_prov_name = State()
    add_prov_url = State()
    add_prov_key = State()
    add_prov_model = State()
    add_model_to_prov = State()

    edit_base_prompt = State()
    edit_topic = State()
    edit_custom = State()
    load_context = State()
    load_memory = State()

    set_bot_name = State()
    set_frequency = State()
    set_context_size = State()
    set_triggers = State()

    add_admin = State()


# ─── /admin command ───

@router.message(Command("admin"), IsAdmin(), IsPrivateChat())
async def cmd_admin(message: Message):
    await message.answer(
        "🮠 <b>Панель управления</b>\n\n"
        "Дракончик Закури — настройки бота",
        reply_markup=main_menu_kb(),
    )


@router.message(Command("admin"), IsPrivateChat())
async def cmd_admin_denied(message: Message):
    await message.answer("У вас нет прав администратора.")


# ─── Main menu navigation ───

@router.callback_query(F.data == "menu:main", IsAdmin())
async def cb_main(callback: CallbackQuery):
    await callback.message.edit_text(
        "🮠 <b>Панель управления</b>\n\nДракончик Закури — настройки бота",
        reply_markup=main_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:close", IsAdmin())
async def cb_close(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer("Меню закрыто")


@router.callback_query(F.data == "cancel", IsAdmin())
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🮠 <b>Панель управления</b>",
        reply_markup=main_menu_kb(),
    )
    await callback.answer("Отменено")


# ─── Settings ───

@router.callback_query(F.data == "menu:settings", IsAdmin())
async def cb_settings(callback: CallbackQuery):
    await callback.message.edit_text(
        "⚙️ <b>Настройки бота</b>",
        reply_markup=settings_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "set:bot_name", IsAdmin())
async def cb_set_bot_name(callback: CallbackQuery, state: FSMContext, db):
    current = await db.get_setting("bot_name") or "Дракончик Закури"
    await state.set_state(AdminStates.set_bot_name)
    await callback.message.edit_text(
        f"Текущее имя: <b>{current}</b>\n\n"
        f"Введите новое имя бота:",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(AdminStates.set_bot_name, IsAdmin())
async def process_bot_name(message: Message, state: FSMContext, db):
    name = message.text.strip()
    if not name:
        await message.answer("Имя не может быть пустым. Попробуйте ещё раз:")
        return
    old = await db.get_setting("bot_name") or "Дракончик Закури"
    await db.set_setting("bot_name", name)
    await state.clear()
    await message.answer(
        f"✅ Имя бота изменено!\n\n"
        f"Было: {old}\n"
        f"Стало: {name}",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "set:frequency", IsAdmin())
async def cb_set_frequency(callback: CallbackQuery, state: FSMContext, db):
    current = await db.get_setting("auto_respond_frequency") or "10"
    await state.set_state(AdminStates.set_frequency)
    await callback.message.edit_text(
        f"Текущая частота: <b>{current}%</b>\n\n"
        f"Введите частоту авто-ответов (0-100, процент сообщений):",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(AdminStates.set_frequency, IsAdmin())
async def process_frequency(message: Message, state: FSMContext, db):
    try:
        freq = int(message.text.strip())
        if not 0 <= freq <= 100:
            raise ValueError
    except ValueError:
        await message.answer("Введите число от 0 до 100:")
        return
    old = await db.get_setting("auto_respond_frequency") or "10"
    await db.set_setting("auto_respond_frequency", str(freq))
    await state.clear()
    await message.answer(
        f"✅ Частота авто-ответов изменена!\n\n"
        f"Было: {old}%\n"
        f"Стало: {freq}%",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "set:context_size", IsAdmin())
async def cb_set_context_size(callback: CallbackQuery, state: FSMContext, db):
    current = await db.get_setting("global_context_size") or "50"
    await state.set_state(AdminStates.set_context_size)
    await callback.message.edit_text(
        f"Текущий размер: <b>{current} сообщений</b>\n\n"
        f"Введите размер контекста (10-5000):\n\n"
        f"Большие значения = больше памяти, но выше расход токенов.\n"
        f"Старые сообщения автоматически суммаризуются в ключевые события.",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(AdminStates.set_context_size, IsAdmin())
async def process_context_size(message: Message, state: FSMContext, db):
    try:
        size = int(message.text.strip())
        if not 10 <= size <= 5000:
            raise ValueError
    except ValueError:
        await message.answer("Введите число от 10 до 5000:")
        return
    old = await db.get_setting("global_context_size") or "50"
    await db.set_setting("global_context_size", str(size))
    await state.clear()
    await message.answer(
        f"✅ Размер контекста изменён!\n\n"
        f"Было: {old} сообщений\n"
        f"Стало: {size} сообщений",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "set:triggers", IsAdmin())
async def cb_set_triggers(callback: CallbackQuery, state: FSMContext, db):
    current = await db.get_setting("trigger_words")
    if current:
        display = current
    else:
        display = "закури, зак, заку, драко, дракон, дракончик, дракоша, драк, закурий, бот"
    await state.set_state(AdminStates.set_triggers)
    await callback.message.edit_text(
        f"🎯 <b>Триггер-слова</b>\n\n"
        f"Бот отвечает когда видит эти слова в сообщении.\n"
        f"Введите слова через запятую:\n\n"
        f"<b>Текущие:</b>\n{display}\n\n"
        f"Или /reset для стандартного набора.",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(AdminStates.set_triggers, IsAdmin())
async def process_triggers(message: Message, state: FSMContext, db):
    text = message.text.strip()
    old = await db.get_setting("trigger_words") or "закури, зак, заку, драко, дракон, дракончик, дракоша, драк, закурий, бот"

    if text.lower() == "/reset":
        await db.set_setting("trigger_words", "")
        await state.clear()
        await message.answer(
            f"✅ Триггер-слова сброшены!\n\n"
            f"Было: {old}\n"
            f"Стало: стандартный набор",
            reply_markup=main_menu_kb(),
        )
        return

    words = [w.strip().lower() for w in text.split(",") if w.strip()]
    if not words:
        await message.answer("Введите хотя бы одно слово:")
        return

    new_val = ", ".join(words)
    await db.set_setting("trigger_words", new_val)
    await state.clear()
    await message.answer(
        f"✅ Триггер-слова обновлены!\n\n"
        f"Было: {old}\n"
        f"Стало: {new_val}\n\n"
        f"Бот будет реагировать на: {', '.join(words)}",
        reply_markup=main_menu_kb(),
    )


# ─── AI Providers ───

@router.callback_query(F.data == "menu:providers", IsAdmin())
async def cb_providers(callback: CallbackQuery, db):
    providers = await db.get_providers()
    if not providers:
        text = "🤖 <b>AI Провайдеры</b>\n\nПровайдеров пока нет. Добавьте новый:"
    else:
        text_providers = [p for p in providers if p["provider_type"] == "text"]
        image_providers = [p for p in providers if p["provider_type"] == "image"]

        lines = []
        if text_providers:
            active_t = sum(1 for p in text_providers if p["is_active"])
            lines.append(f"📝 <b>Текст ({len(text_providers)}, активных {active_t}):</b>")
            for i, p in enumerate(text_providers, 1):
                status = "✅" if p["is_active"] else "❌"
                lines.append(f"  {i}. {status} {p['name']} — {p['model']}")

        if image_providers:
            active_i = sum(1 for p in image_providers if p["is_active"])
            lines.append(f"\n🎨 <b>Изображения ({len(image_providers)}, активных {active_i}):</b>")
            for i, p in enumerate(image_providers, 1):
                status = "✅" if p["is_active"] else "❌"
                lines.append(f"  {i}. {status} {p['name']} — {p['model']}")

        lines.append(f"\n💡 Порядок = приоритет. Верхние используются первыми.")
        text = "🤖 <b>AI Провайдеры</b>\n\n" + "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=providers_kb(providers))
    await callback.answer()


@router.callback_query(F.data.startswith("prov:add_"), IsAdmin())
async def cb_add_provider(callback: CallbackQuery, state: FSMContext):
    ptype = "text" if callback.data == "prov:add_text" else "image"
    await state.set_state(AdminStates.add_prov_name)
    await state.update_data(provider_type=ptype)
    await callback.message.edit_text(
        f"➕ Добавление {ptype}-провайдера\n\n"
        "Шаг 1/4: Введите <b>название</b> провайдера\n"
        "(например: NVIDIA NIM, OpenAI, Groq)",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "prov:presets", IsAdmin())
async def cb_presets(callback: CallbackQuery):
    await callback.message.edit_text(
        "⚡ <b>Готовые пресеты</b>\n\n"
        "Выберите пресет — нужно будет только ввести API ключ.\n\n"
        "🟢 NVIDIA: build.nvidia.com → 1000 кредитов\n"
        "🟢 OpenRouter: openrouter.ai → есть бесплатные модели",
        reply_markup=presets_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("nv_preset:"), IsAdmin())
async def cb_nv_preset(callback: CallbackQuery, state: FSMContext):
    preset_key = callback.data.split(":", 1)[1]
    preset = NVIDIA_PRESETS.get(preset_key)
    if not preset:
        await callback.answer("Пресет не найден")
        return
    await _start_preset(callback, state, preset)


@router.callback_query(F.data.startswith("or_preset:"), IsAdmin())
async def cb_or_preset(callback: CallbackQuery, state: FSMContext):
    preset_key = callback.data.split(":", 1)[1]
    preset = OPENROUTER_PRESETS.get(preset_key)
    if not preset:
        await callback.answer("Пресет не найден")
        return
    await _start_preset(callback, state, preset)


async def _start_preset(callback: CallbackQuery, state: FSMContext, preset: dict):
    await state.set_state(AdminStates.add_prov_key)
    await state.update_data(
        provider_type=preset["provider_type"],
        prov_name=preset["name"],
        prov_url=preset["base_url"],
        preset_model=preset["model"],
    )
    await callback.message.edit_text(
        f"⚡ <b>{preset['name']}</b>\n\n"
        f"Тип: {'Текст' if preset['provider_type'] == 'text' else 'Изображения'}\n"
        f"URL: {preset['base_url']}\n"
        f"Модель: {preset['model']}\n\n"
        f"Введите <b>API ключ</b>:",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "prov:test_image", IsAdmin())
async def cb_test_image(callback: CallbackQuery, db, ai_manager, bot):
    providers = await db.get_active_providers("image")
    if not providers:
        await callback.answer("Нет активных image-провайдеров!", show_alert=True)
        return

    await callback.answer("Генерирую тестовое фото...")
    await callback.message.answer("🧪 Тест генерации фото...\nПромпт: «плюшевый зелёный дракончик, милая иллюстрация»")

    try:
        from aiogram.types import BufferedInputFile
        from aiogram.enums import ChatAction

        await bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.UPLOAD_PHOTO)

        image_bytes = await ai_manager.generate_image(
            "плюшевый зелёный дракончик, милая иллюстрация, cute plush dragon"
        )
        photo = BufferedInputFile(image_bytes, filename="test_dragon.png")
        await callback.message.answer_photo(
            photo,
            caption="🧪 Тест прошёл успешно! Фото сгенерировано.",
        )
    except Exception as e:
        await callback.message.answer(
            f"❌ Ошибка генерации:\n<code>{str(e)[:300]}</code>\n\n"
            f"Проверьте URL, ключ и модель image-провайдера.",
            reply_markup=providers_kb(await db.get_providers()),
        )


@router.message(AdminStates.add_prov_name, IsAdmin())
async def process_prov_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        await message.answer("Название не может быть пустым:")
        return
    await state.update_data(prov_name=name)
    await state.set_state(AdminStates.add_prov_url)
    await message.answer(
        "Шаг 2/4: Введите <b>base URL</b> провайдера\n"
        "(например: https://integrate.api.nvidia.com/v1)",
        reply_markup=cancel_kb(),
    )


@router.message(AdminStates.add_prov_url, IsAdmin())
async def process_prov_url(message: Message, state: FSMContext):
    url = message.text.strip()
    if not url.startswith("http"):
        await message.answer("URL должен начинаться с http:// или https://:")
        return
    await state.update_data(prov_url=url)
    await state.set_state(AdminStates.add_prov_key)
    await message.answer(
        "Шаг 3/4: Введите <b>API ключ</b>\n"
        "(например: nvapi-xxx, sk-xxx)",
        reply_markup=cancel_kb(),
    )


@router.message(AdminStates.add_prov_key, IsAdmin())
async def process_prov_key(message: Message, state: FSMContext):
    key = message.text.strip()
    if not key:
        await message.answer("API ключ не может быть пустым:")
        return
    await state.update_data(prov_key=key)

    data = await state.get_data()
    if data.get("preset_model"):
        model = data["preset_model"]
        await _save_providers_batch(message, state, db=None, raw_models=model)
        return

    await state.set_state(AdminStates.add_prov_model)
    await message.answer(
        "Шаг 4/4: Введите <b>модель(и)</b>\n"
        "Можно несколько через запятую или с новой строки!\n\n"
        "Примеры:\n"
        "  • nvidia/llama-3.1-nemotron-70b-instruct\n"
        "  • model-1, model-2, model-3\n"
        "  • gpt-4o,\n    gpt-4o-mini",
        reply_markup=cancel_kb(),
    )


@router.message(AdminStates.add_prov_model, IsAdmin())
async def process_prov_model(message: Message, state: FSMContext, db):
    raw = message.text.strip()
    if not raw:
        await message.answer("Модель не может быть пустой:")
        return
    await _save_providers_batch(message, state, db, raw)


async def _save_providers_batch(message: Message, state: FSMContext, db, raw_models: str):
    data = await state.get_data()
    ptype = data.get("provider_type", "text")

    if db is None:
        from src.database import Database
        db = Database()
        await db.init()

    models = [m.strip() for m in raw_models.replace("\n", ",").split(",") if m.strip()]
    if not models:
        await message.answer("Не найдено ни одной модели:")
        return

    count = len(await db.get_providers(ptype))
    added = []
    for i, model in enumerate(models):
        await db.add_provider(
            name=data["prov_name"],
            base_url=data["prov_url"],
            api_key=data["prov_key"],
            model=model,
            provider_type=ptype,
            priority=count + i,
        )
        added.append(f"  {i+1}. {model}")

    await state.clear()
    await message.answer(
        f"✅ Добавлено {len(added)} провайдер(ов)!\n\n"
        f"Название: {data['prov_name']}\n"
        f"Тип: {ptype}\n"
        f"URL: {data['prov_url']}\n\n"
        f"Модели:\n" + "\n".join(added),
        reply_markup=main_menu_kb(),
    )


@router.message(AdminStates.add_model_to_prov, IsAdmin())
async def process_add_model_to_prov(message: Message, state: FSMContext, db):
    raw = message.text.strip()
    if not raw:
        await message.answer("Введите хотя бы одну модель:")
        return

    data = await state.get_data()
    ptype = data.get("provider_type", "text")
    pid = data.get("base_provider_id")

    models = [m.strip() for m in raw.replace("\n", ",").split(",") if m.strip()]
    if not models:
        await message.answer("Не найдено ни одной модели:")
        return

    existing = await db.get_providers(ptype)
    existing_models = {p["model"] for p in existing if p["base_url"] == data["prov_url"]}

    count = len(existing)
    added = []
    skipped = []
    for i, model in enumerate(models):
        if model in existing_models:
            skipped.append(model)
            continue
        await db.add_provider(
            name=data["prov_name"],
            base_url=data["prov_url"],
            api_key=data["prov_key"],
            model=model,
            provider_type=ptype,
            priority=count + i,
        )
        added.append(f"  ✅ {model}")
        existing_models.add(model)

    for m in skipped:
        added.append(f"  ⏭️ {m} (уже есть)")

    await state.clear()
    result = f"➕ Модели для «{data['prov_name']}»\n\n" + "\n".join(added)
    if skipped:
        result += f"\n\nПропущено {len(skipped)} (уже существуют)"
    result += f"\n\nДобавлено: {len(added) - len(skipped)}"
    await message.answer(result, reply_markup=main_menu_kb())


@router.callback_query(F.data.startswith("prov:"), IsAdmin())
async def cb_provider_detail(callback: CallbackQuery, db, state: FSMContext):
    data = callback.data

    if data.startswith("prov:toggle:"):
        pid = int(data.split(":")[2])
        await db.toggle_provider(pid)
        await callback.answer("Статус изменён")
        providers = await db.get_providers()
        await callback.message.edit_text(
            "🤖 <b>AI Провайдеры</b>",
            reply_markup=providers_kb(providers),
        )
        return

    if data.startswith("prov:up:") or data.startswith("prov:down:"):
        pid = int(data.split(":")[2])
        direction = "up" if data.startswith("prov:up:") else "down"
        providers = await db.get_providers()
        current = next((p for p in providers if p["id"] == pid), None)
        if not current:
            await callback.answer("Не найдено")
            return

        same_type = [p for p in providers if p["provider_type"] == current["provider_type"]]
        same_type.sort(key=lambda x: x["priority"])

        idx = next((i for i, p in enumerate(same_type) if p["id"] == pid), -1)
        if idx == -1:
            await callback.answer("Не найдено")
            return

        if direction == "up" and idx > 0:
            other = same_type[idx - 1]
            await db.update_provider(pid, priority=other["priority"])
            await db.update_provider(other["id"], priority=current["priority"])
        elif direction == "down" and idx < len(same_type) - 1:
            other = same_type[idx + 1]
            await db.update_provider(pid, priority=other["priority"])
            await db.update_provider(other["id"], priority=current["priority"])

        await callback.answer("Приоритет изменён")
        providers = await db.get_providers()
        await callback.message.edit_text(
            "🤖 <b>AI Провайдеры</b>",
            reply_markup=providers_kb(providers),
        )
        return

    if data.startswith("prov:delete:"):
        pid = int(data.split(":")[2])
        await db.delete_provider(pid)
        await callback.answer("Провайдер удалён")
        providers = await db.get_providers()
        await callback.message.edit_text(
            "🤖 <b>AI Провайдеры</b>",
            reply_markup=providers_kb(providers),
        )
        return

    if data.startswith("prov:add_model:"):
        pid = int(data.split(":")[2])
        providers = await db.get_providers()
        provider = next((p for p in providers if p["id"] == pid), None)
        if not provider:
            await callback.answer("Провайдер не найден")
            return
        await state.set_state(AdminStates.add_model_to_prov)
        await state.update_data(
            base_provider_id=pid,
            prov_name=provider["name"],
            prov_url=provider["base_url"],
            prov_key=provider["api_key"],
            provider_type=provider["provider_type"],
        )
        await callback.message.edit_text(
            f"➕ <b>Добавить модель к: {provider['name']}</b>\n\n"
            f"URL: {provider['base_url']}\n"
            f"Текущая модель: {provider['model']}\n\n"
            f"Введите новые модели через запятую:\n"
            f"(например: model-1, model-2, model-3)",
            reply_markup=cancel_kb(),
        )
        await callback.answer()
        return

    if data in ("prov:add_text", "prov:add_image", "prov:presets", "prov:test_image",
                "prov:nvidia_presets"):
        return

    pid = int(data.split(":")[1])
    providers = await db.get_providers()
    provider = next((p for p in providers if p["id"] == pid), None)
    if not provider:
        await callback.answer("Провайдер не найден")
        return

    same_url = [p for p in providers if p["base_url"] == provider["base_url"] and p["provider_type"] == provider["provider_type"]]
    status = "✅ Активен" if provider["is_active"] else "❌ Выключен"
    ptype = "Текст" if provider["provider_type"] == "text" else "Изображения"
    key_masked = provider["api_key"][:8] + "..." if len(provider["api_key"]) > 8 else "***"

    models_list = "\n".join(
        f"  {'✅' if p['is_active'] else '❌'} {p['model']}" for p in same_url
    )

    await callback.message.edit_text(
        f"🤖 <b>{provider['name']}</b>\n\n"
        f"Тип: {ptype}\n"
        f"URL: {provider['base_url']}\n"
        f"Ключ: {key_masked}\n"
        f"Приоритет: {provider['priority']}\n\n"
        f"Модели ({len(same_url)}):\n{models_list}",
        reply_markup=provider_detail_kb(pid),
    )
    await callback.answer()


# ─── Personality & Prompt ───

@router.callback_query(F.data == "menu:personality", IsAdmin())
async def cb_personality(callback: CallbackQuery):
    await callback.message.edit_text(
        "📝 <b>Личность и промпт</b>",
        reply_markup=personality_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "pers:view", IsAdmin())
async def cb_pers_view(callback: CallbackQuery, db):
    personality = await db.get_setting("base_personality") or "(стандартный из PERSONALITY.md)"
    topic = await db.get_setting("topic") or "(не задана)"
    custom = await db.get_setting("custom_instructions") or "(нет)"
    memory = await db.get_setting("chat_memory") or ""
    memory_info = f"{len(memory):,} символов" if memory else "(не загружена)"

    text = (
        f"📝 <b>Текущий промпт</b>\n\n"
        f"<b>Личность:</b>\n{personality[:800]}{'...' if len(personality) > 800 else ''}\n\n"
        f"<b>Тема:</b> {topic}\n\n"
        f"<b>Инструкции:</b> {custom[:200] if custom else '(нет)'}\n\n"
        f"<b>Память чата:</b> {memory_info}"
    )
    await callback.message.edit_text(text, reply_markup=personality_kb())
    await callback.answer()


@router.callback_query(F.data == "pers:edit_base", IsAdmin())
async def cb_pers_edit_base(callback: CallbackQuery, state: FSMContext, db):
    current = await db.get_setting("base_personality") or "(стандартный из PERSONALITY.md)"
    await state.set_state(AdminStates.edit_base_prompt)
    await callback.message.edit_text(
        f"<b>Текущий промпт:</b>\n\n{current[:500]}{'...' if len(current) > 500 else ''}\n\n"
        f"Отправьте новый базовый промпт (личность бота).\n"
        f"Или /reset для сброса к стандартному из PERSONALITY.md",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(AdminStates.edit_base_prompt, IsAdmin())
async def process_edit_base(message: Message, state: FSMContext, db):
    text = message.text.strip()
    old = await db.get_setting("base_personality") or "(стандартный)"
    if text.lower() == "/reset":
        from src.personality import load_personality_from_file
        default = load_personality_from_file()
        await db.set_setting("base_personality", default)
        await state.clear()
        await message.answer(
            f"✅ Промпт сброшен к стандартному!\n\n"
            f"Было: {old[:100]}...\n"
            f"Стало: стандартный из PERSONALITY.md",
            reply_markup=main_menu_kb(),
        )
        return
    await db.set_setting("base_personality", text)
    await state.clear()
    await message.answer(
        f"✅ Базовый промпт обновлён!\n\n"
        f"Было: {old[:100]}{'...' if len(old) > 100 else ''}\n"
        f"Стало: {text[:100]}{'...' if len(text) > 100 else ''}",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "pers:edit_topic", IsAdmin())
async def cb_pers_edit_topic(callback: CallbackQuery, state: FSMContext, db):
    current = await db.get_setting("topic") or "(не задана)"
    await state.set_state(AdminStates.edit_topic)
    await callback.message.edit_text(
        f"Текущая тема: <b>{current}</b>\n\n"
        f"Отправьте новую тему для обсуждения.\n"
        f"Или /clear чтобы убрать тему.",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(AdminStates.edit_topic, IsAdmin())
async def process_edit_topic(message: Message, state: FSMContext, db):
    text = message.text.strip()
    old = await db.get_setting("topic") or "(не задана)"
    if text.lower() == "/clear":
        await db.set_setting("topic", "")
        await state.clear()
        await message.answer(
            f"✅ Тема очищена!\n\nБыло: {old}\nСтало: (не задана)",
            reply_markup=main_menu_kb(),
        )
        return
    await db.set_setting("topic", text)
    await state.clear()
    await message.answer(
        f"✅ Тема установлена!\n\nБыло: {old}\nСтало: {text}",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "pers:edit_custom", IsAdmin())
async def cb_pers_edit_custom(callback: CallbackQuery, state: FSMContext, db):
    current = await db.get_setting("custom_instructions") or "(нет)"
    await state.set_state(AdminStates.edit_custom)
    await callback.message.edit_text(
        f"Текущие инструкции:\n<b>{current[:300]}</b>\n\n"
        f"Отправьте новые дополнительные инструкции для бота.\n"
        f"Или /clear чтобы убрать инструкции.",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(AdminStates.edit_custom, IsAdmin())
async def process_edit_custom(message: Message, state: FSMContext, db):
    text = message.text.strip()
    old = await db.get_setting("custom_instructions") or "(нет)"
    if text.lower() == "/clear":
        await db.set_setting("custom_instructions", "")
        await state.clear()
        await message.answer(
            f"✅ Инструкции очищены!\n\nБыло: {old[:100]}\nСтало: (нет)",
            reply_markup=main_menu_kb(),
        )
        return
    await db.set_setting("custom_instructions", text)
    await state.clear()
    await message.answer(
        f"✅ Инструкции обновлены!\n\n"
        f"Было: {old[:100]}{'...' if len(old) > 100 else ''}\n"
        f"Стало: {text[:100]}{'...' if len(text) > 100 else ''}",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "pers:reset", IsAdmin())
async def cb_pers_reset(callback: CallbackQuery, db):
    default = load_personality_from_file()
    await db.set_setting("base_personality", default)
    await db.set_setting("topic", "")
    await db.set_setting("custom_instructions", "")
    await callback.answer("Сброшено к стандартному")
    await callback.message.edit_text(
        "✅ Личность сброшена к стандартному из PERSONALITY.md",
        reply_markup=personality_kb(),
    )


@router.callback_query(F.data == "pers:load_context", IsAdmin())
async def cb_pers_load_context(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.load_context)
    await callback.message.edit_text(
        "📥 <b>Загрузка контекста (суммаризация)</b>\n\n"
        "Отправьте текст или .txt файл с контекстом (до 100k символов).\n"
        "Бот суммаризует его и сохранит как ключевые события.\n\n"
        "Текущий чат будет использоваться как целевой.",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(AdminStates.load_context, IsAdmin())
async def process_load_context(
    message: Message, state: FSMContext, db, ai_manager, context_manager,
):
    chat_id = message.chat.id

    if message.document:
        try:
            file = await message.bot.get_file(message.document.file_id)
            downloaded = await message.bot.download_file(file.file_path)
            content = downloaded.read().decode("utf-8", errors="replace")
        except Exception as e:
            await message.answer(f"Ошибка чтения файла: {e}")
            return
    elif message.text:
        content = message.text
    else:
        await message.answer("Отправьте текст или .txt файл.")
        return

    if len(content) > 100_000:
        content = content[:100_000]
        await message.answer("⚠️ Текст обрезан до 100k символов.")

    await message.answer("⏳ Обрабатываю контекст...")

    try:
        count = await context_manager.load_large_context(chat_id, content)
        await state.clear()
        await message.answer(
            f"✅ Контекст загружен! Создано {count} ключевых событий.",
            reply_markup=main_menu_kb(),
        )
    except Exception as e:
        logger.error(f"Context load error: {e}", exc_info=True)
        await message.answer(f"Ошибка: {e}")


@router.callback_query(F.data == "pers:load_memory", IsAdmin())
async def cb_pers_load_memory(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.load_memory)
    await callback.message.edit_text(
        "📥 <b>Загрузка памяти чата (raw)</b>\n\n"
        "Отправьте .txt/.md файл или текст с памятью чата.\n"
        "Файл сохранится целиком — без суммаризации.\n"
        "Будет включаться в системный промпт напрямую.\n\n"
        "Максимум 100k символов. Умное обрезание при превышении лимита модели.",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(AdminStates.load_memory, IsAdmin())
async def process_load_memory(
    message: Message, state: FSMContext, db,
):
    if message.document:
        try:
            file = await message.bot.get_file(message.document.file_id)
            downloaded = await message.bot.download_file(file.file_path)
            content = downloaded.read().decode("utf-8", errors="replace")
        except Exception as e:
            await message.answer(f"Ошибка чтения файла: {e}")
            return
    elif message.text:
        content = message.text
    else:
        await message.answer("Отправьте текст или .txt/.md файл.")
        return

    if len(content) > 100_000:
        content = content[:100_000]

    await db.set_setting("chat_memory", content)
    await state.clear()

    from src.personality import _truncate_memory, MAX_MEMORY_CHARS
    truncated_len = len(_truncate_memory(content))

    await message.answer(
        f"✅ Память чата загружена!\n\n"
        f"Размер: {len(content):,} символов\n"
        f"В промпте: ~{truncated_len:,} символов (лимит {MAX_MEMORY_CHARS:,})\n\n"
        f"Бот теперь знает участников, жаргон и легенды чата.",
        reply_markup=main_menu_kb(),
    )


# ─── Context & Memory ───

@router.callback_query(F.data == "menu:context", IsAdmin())
async def cb_context(callback: CallbackQuery):
    await callback.message.edit_text(
        "🧠 <b>Контекст и память</b>",
        reply_markup=context_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "ctx:events", IsAdmin())
async def cb_ctx_events(callback: CallbackQuery, db):
    chat_id = callback.message.chat.id
    from src.context_manager import ContextManager
    events = await db.get_key_events(chat_id, limit=50)
    if not events:
        text = "🧠 <b>Ключевые события</b>\n\nКлючевых событий нет."
    else:
        lines = [f"• {e['event_text'][:200]}" for e in reversed(events)]
        text = "🧠 <b>Ключевые события</b>\n\n" + "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=context_kb())
    await callback.answer()


@router.callback_query(F.data == "ctx:clear", IsAdmin())
async def cb_ctx_clear(callback: CallbackQuery, db):
    chat_id = callback.message.chat.id
    await db.clear_messages(chat_id)
    await callback.answer("Контекст очищен")
    await callback.message.edit_text(
        "✅ Контекст чата очищен",
        reply_markup=context_kb(),
    )


@router.callback_query(F.data == "ctx:clear_events", IsAdmin())
async def cb_ctx_clear_events(callback: CallbackQuery, db):
    chat_id = callback.message.chat.id
    await db.clear_key_events(chat_id)
    await callback.answer("События очищены")
    await callback.message.edit_text(
        "✅ Ключевые события очищены",
        reply_markup=context_kb(),
    )


@router.callback_query(F.data == "ctx:clear_memory", IsAdmin())
async def cb_ctx_clear_memory(callback: CallbackQuery, db):
    await db.set_setting("chat_memory", "")
    await callback.answer("Память чата очищена")
    await callback.message.edit_text(
        "✅ Память чата очищена",
        reply_markup=context_kb(),
    )


# ─── Admins ───

@router.callback_query(F.data == "menu:admins", IsAdmin())
async def cb_admins(callback: CallbackQuery, db):
    admins = await db.get_admins()
    env_admins = [uid for uid in config.ADMIN_IDS if uid not in [a["user_id"] for a in admins]]
    if not admins and not env_admins:
        text = "👥 <b>Админы</b>\n\nАдминов нет."
    else:
        lines = []
        for a in admins:
            name = a["username"] or str(a["user_id"])
            lines.append(f"👤 {name} ({a['user_id']})")
        for uid in env_admins:
            lines.append(f"👤 (env) {uid}")
        text = "👥 <b>Админы</b>\n\n" + "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=admins_kb(admins))
    await callback.answer()


@router.callback_query(F.data == "adm:add", IsAdmin())
async def cb_add_admin(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.add_admin)
    await callback.message.edit_text(
        "Отправьте ID пользователя или перешлите его сообщение.\n"
        "(ID можно узнать у @userinfobot)",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(AdminStates.add_admin, IsAdmin())
async def process_add_admin(message: Message, state: FSMContext, db):
    user_id = None
    username = ""

    if message.forward_origin:
        origin = message.forward_origin
        if hasattr(origin, "sender_user") and origin.sender_user:
            user_id = origin.sender_user.id
            username = origin.sender_user.username or ""
    elif message.text:
        try:
            user_id = int(message.text.strip())
        except ValueError:
            await message.answer("Введите числовой ID или перешлите сообщение пользователя.")
            return

    if not user_id:
        await message.answer("Не удалось определить ID. Попробуйте ещё раз.")
        return

    await db.add_admin(user_id, username)
    await state.clear()
    await message.answer(
        f"✅ Админ добавлен: {username or user_id} (ID: {user_id})",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data.startswith("adm:delete:"), IsAdmin())
async def cb_delete_admin(callback: CallbackQuery, db):
    uid = int(callback.data.split(":")[2])
    if uid in config.ADMIN_IDS:
        await callback.answer("Нельзя удалить админа из .env")
        return
    await db.remove_admin(uid)
    admins = await db.get_admins()
    await callback.answer("Админ удалён")
    await callback.message.edit_text(
        "👥 <b>Админы</b>",
        reply_markup=admins_kb(admins),
    )


@router.callback_query(F.data.startswith("adm:"), IsAdmin())
async def cb_admin_detail(callback: CallbackQuery, db):
    if callback.data.startswith("adm:delete:") or callback.data == "adm:add":
        return
    uid = int(callback.data.split(":")[1])
    admins = await db.get_admins()
    admin = next((a for a in admins if a["user_id"] == uid), None)
    if not admin:
        await callback.answer("Админ не найден")
        return
    name = admin["username"] or str(admin["user_id"])
    await callback.message.edit_text(
        f"👤 <b>{name}</b>\nID: {admin['user_id']}",
        reply_markup=admin_detail_kb(uid),
    )
    await callback.answer()


# ─── Statistics ───

@router.callback_query(F.data == "menu:stats", IsAdmin())
async def cb_stats(callback: CallbackQuery, db):
    from src.config import config as cfg
    chat_id = callback.message.chat.id

    msg_count = await db.get_message_count(chat_id)
    events = await db.get_key_events(chat_id)
    text_providers = await db.get_providers("text")
    image_providers = await db.get_providers("image")
    admins = await db.get_admins()

    bot_name = await db.get_setting("bot_name") or "Дракончик Закури"
    topic = await db.get_setting("topic") or "(не задана)"
    ctx_size = await db.get_setting("global_context_size") or "50"

    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"<b>Бот:</b> {bot_name}\n"
        f"<b>Тема:</b> {topic}\n"
        f"<b>Контекст:</b> {ctx_size} сообщений\n\n"
        f"<b>Этот чат:</b>\n"
        f"  Сообщений в БД: {msg_count}\n"
        f"  Ключевых событий: {len(events)}\n\n"
        f"<b>AI Провайдеры:</b>\n"
        f"  Текст: {len(text_providers)} ({sum(1 for p in text_providers if p['is_active'])} активных)\n"
        f"  Изображения: {len(image_providers)} ({sum(1 for p in image_providers if p['is_active'])} активных)\n\n"
        f"<b>Админы:</b> {len(admins) + len(cfg.ADMIN_IDS)}"
    )
    await callback.message.edit_text(text, reply_markup=main_menu_kb())
    await callback.answer()
