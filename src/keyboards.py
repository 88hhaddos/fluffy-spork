from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⚙️ Настройки бота", callback_data="menu:settings")
    kb.button(text="🤖 AI Провайдеры", callback_data="menu:providers")
    kb.button(text="📝 Личность и промпт", callback_data="menu:personality")
    kb.button(text="🧠 Контекст и память", callback_data="menu:context")
    kb.button(text="👥 Админы", callback_data="menu:admins")
    kb.button(text="🚫 Бан-лист", callback_data="menu:bans")
    kb.button(text="💚 Отношения юзеров", callback_data="menu:relations")
    kb.button(text="📨 Написать от лица бота", callback_data="menu:say")
    kb.button(text="📊 Статистика", callback_data="menu:stats")
    kb.button(text="❌ Закрыть", callback_data="menu:close")
    kb.adjust(1, 1, 1, 1, 1, 1, 1, 1, 1, 1)
    return kb.as_markup()


def settings_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Имя бота", callback_data="set:bot_name")
    kb.button(text="😈 Злость (0-100%)", callback_data="set:anger")
    kb.button(text="🎨 Стиль фото", callback_data="set:photo_style")
    kb.button(text="🖼️ Кастомный фото-промпт", callback_data="set:photo_custom")
    kb.button(text="🎲 Частота авто-ответов", callback_data="set:frequency")
    kb.button(text="📏 Размер контекста", callback_data="set:context_size")
    kb.button(text="🎯 Триггер-слова", callback_data="set:triggers")
    kb.button(text="🔙 Назад", callback_data="menu:main")
    kb.adjust(1, 1, 1, 1, 1, 1, 1, 1)
    return kb.as_markup()


PHOTO_STYLES = {
    "realistic": "реалистичное, фото",
    "anime": "аниме",
    "cartoon": "мультяшное",
    "art": "картина маслом",
    "digital": "цифровой арт",
    "watercolor": "акварель",
    "pixel": "пиксель-арт",
    "3d": "3D рендер",
    "dark": "тёмное фэнтези",
    "cute": "милый, каваий",
}


def photo_style_kb(current: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for key, label in PHOTO_STYLES.items():
        marker = "✅ " if key == current else "  "
        kb.button(text=f"{marker}{label}", callback_data=f"pstyle:{key}")
    kb.button(text="🔙 Назад", callback_data="menu:settings")
    kb.adjust(2, 2, 2, 2, 2, 1)
    return kb.as_markup()


def providers_kb(providers: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in providers:
        status = "✅" if p["is_active"] else "❌"
        ptype = "📝" if p["provider_type"] == "text" else "🎨"
        label = f"{status} {ptype} {p['name']} — {p['model'][:30]}"
        kb.button(text=label, callback_data=f"prov:{p['id']}")
    kb.button(text="➕ Добавить text-провайдер", callback_data="prov:add_text")
    kb.button(text="➕ Добавить image-провайдер", callback_data="prov:add_image")
    kb.button(text="⚡ Готовые пресеты", callback_data="prov:presets")
    kb.button(text="🧪 Тест генерации фото", callback_data="prov:test_image")
    kb.button(text="🔙 Назад", callback_data="menu:main")
    kb.adjust(1, 1, 1, 1, 1)
    return kb.as_markup()


NVIDIA_PRESETS = {
    "nv_text_nemotron": {
        "name": "NVIDIA Nemotron 70B",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "nvidia/llama-3.1-nemotron-70b-instruct",
        "provider_type": "text",
    },
    "nv_text_llama": {
        "name": "NVIDIA Llama 3.1 405B",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "meta/llama-3.1-405b-instruct",
        "provider_type": "text",
    },
}

OPENROUTER_PRESETS = {
    "or_free_llama": {
        "name": "OpenRouter Llama 3.3 70B (free)",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "provider_type": "text",
    },
    "or_free_qwen": {
        "name": "OpenRouter Qwen 2.5 72B (free)",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "qwen/qwen-2.5-72b-instruct:free",
        "provider_type": "text",
    },
    "or_free_deepseek": {
        "name": "OpenRouter DeepSeek R1 (free)",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-r1:free",
        "provider_type": "text",
    },
    "or_free_mistral": {
        "name": "OpenRouter Mistral 7B (free)",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "mistralai/mistral-7b-instruct:free",
        "provider_type": "text",
    },
    "or_gpt4o": {
        "name": "OpenRouter GPT-4o mini",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o-mini",
        "provider_type": "text",
    },
    "or_claude": {
        "name": "OpenRouter Claude 3.5 Sonnet",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "anthropic/claude-3.5-sonnet",
        "provider_type": "text",
    },
}


def presets_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📝 NVIDIA Nemotron 70B", callback_data="nv_preset:nv_text_nemotron")
    kb.button(text="📝 NVIDIA Llama 405B", callback_data="nv_preset:nv_text_llama")
    kb.button(text="📝 OpenRouter Llama 3.3 70B (free)", callback_data="or_preset:or_free_llama")
    kb.button(text="📝 OpenRouter Qwen 2.5 72B (free)", callback_data="or_preset:or_free_qwen")
    kb.button(text="📝 OpenRouter DeepSeek R1 (free)", callback_data="or_preset:or_free_deepseek")
    kb.button(text="📝 OpenRouter Mistral 7B (free)", callback_data="or_preset:or_free_mistral")
    kb.button(text="📝 OpenRouter GPT-4o mini", callback_data="or_preset:or_gpt4o")
    kb.button(text="📝 OpenRouter Claude 3.5 Sonnet", callback_data="or_preset:or_claude")
    kb.button(text="🔙 К списку", callback_data="menu:providers")
    kb.adjust(1, 1, 1, 1, 1, 1, 1, 1, 1)
    return kb.as_markup()


def provider_detail_kb(provider_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить модель", callback_data=f"prov:add_model:{provider_id}")
    kb.button(text="⚡ Приоритет", callback_data=f"prov:priority:{provider_id}")
    kb.button(text="🔄 Включить/выключить", callback_data=f"prov:toggle:{provider_id}")
    kb.button(text="🗑 Удалить", callback_data=f"prov:delete:{provider_id}")
    kb.button(text="🔙 К списку", callback_data="menu:providers")
    kb.adjust(1, 1, 1, 1, 1)
    return kb.as_markup()


def priority_kb(provider_id: int, current_pos: int, total: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if current_pos > 1:
        kb.button(text="🔝 На первое место", callback_data=f"prov:top:{provider_id}")
        kb.button(text="⬆️ Вверх", callback_data=f"prov:up:{provider_id}")
    if current_pos < total:
        kb.button(text="⬇️ Вниз", callback_data=f"prov:down:{provider_id}")
        kb.button(text="🔚 На последнее место", callback_data=f"prov:bottom:{provider_id}")
    kb.button(text=f"🔢 Задать номер (сейчас #{current_pos})", callback_data=f"prov:pos:{provider_id}")
    kb.button(text="🔙 К провайдеру", callback_data=f"prov:{provider_id}")
    kb.adjust(1, 1, 1, 1, 1)
    return kb.as_markup()


def personality_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="👁 Просмотреть промпт", callback_data="pers:view")
    kb.button(text="✏️ Изменить базовый промпт", callback_data="pers:edit_base")
    kb.button(text="📌 Установить тему", callback_data="pers:edit_topic")
    kb.button(text="📋 Кастомные инструкции", callback_data="pers:edit_custom")
    kb.button(text="📥 Контекст (суммаризация)", callback_data="pers:load_context")
    kb.button(text="🧠 Память чата (raw)", callback_data="pers:load_memory")
    kb.button(text="🔄 Сбросить к стандартному", callback_data="pers:reset")
    kb.button(text="🔙 Назад", callback_data="menu:main")
    kb.adjust(1, 1, 1, 1, 1, 1, 1, 1)
    return kb.as_markup()


def context_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Ключевые события чата", callback_data="ctx:events")
    kb.button(text="🗑 Очистить контекст чата", callback_data="ctx:clear")
    kb.button(text="🗑 Очистить ключевые события", callback_data="ctx:clear_events")
    kb.button(text="🗑 Очистить память чата", callback_data="ctx:clear_memory")
    kb.button(text="🔙 Назад", callback_data="menu:main")
    kb.adjust(1, 1, 1, 1, 1)
    return kb.as_markup()


def admins_kb(admins: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for a in admins:
        name = a["username"] or str(a["user_id"])
        kb.button(text=f"👤 {name}", callback_data=f"adm:{a['user_id']}")
    kb.button(text="➕ Добавить админа", callback_data="adm:add")
    kb.button(text="🔙 Назад", callback_data="menu:main")
    kb.adjust(1, 1)
    return kb.as_markup()


def admin_detail_kb(user_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Удалить", callback_data=f"adm:delete:{user_id}")
    kb.button(text="🔙 К списку", callback_data="menu:admins")
    kb.adjust(1, 1)
    return kb.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Отмена", callback_data="cancel")
    return kb.as_markup()


def bans_kb(banned_users: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for u in banned_users:
        name = u.get("username") or str(u["user_id"])
        warnings = u.get("warnings", 0)
        kb.button(text=f"🚫 {name} (⚠️{warnings})", callback_data=f"ban:{u['user_id']}")
    kb.button(text="➕ Забанить юзера", callback_data="ban:add")
    kb.button(text="🔙 Назад", callback_data="menu:main")
    kb.adjust(1, 1)
    return kb.as_markup()


def ban_detail_kb(user_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔓 Разбанить", callback_data=f"ban:unban:{user_id}")
    kb.button(text="🗑 Очистить предупреждения", callback_data=f"ban:clear_warn:{user_id}")
    kb.button(text="🔙 К списку", callback_data="menu:bans")
    kb.adjust(1, 1, 1)
    return kb.as_markup()


def relations_kb(relations: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for r in relations[:20]:
        name = r.get("username") or str(r["user_id"])
        rel = r["relationship"]
        if rel >= 50:
            emoji = "💚💚"
        elif rel >= 20:
            emoji = "💚"
        elif rel <= -50:
            emoji = "🚫🚫"
        elif rel <= -20:
            emoji = "🚫"
        else:
            emoji = "😐"
        kb.button(text=f"{emoji} {name}: {rel}/100", callback_data=f"rel:{r['user_id']}")
    kb.button(text="➕ Добавить юзера", callback_data="rel:add")
    kb.button(text="🔙 Назад", callback_data="menu:main")
    kb.adjust(1, 1)
    return kb.as_markup()


def relation_detail_kb(user_id: int, current: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="💚💚 Любить (+50)", callback_data=f"rel:set:{user_id}:50")
    kb.button(text="💚 Дружить (+20)", callback_data=f"rel:set:{user_id}:20")
    kb.button(text="😐 Нейтрально (0)", callback_data=f"rel:set:{user_id}:0")
    kb.button(text="🚫 Не любить (-20)", callback_data=f"rel:set:{user_id}:-20")
    kb.button(text="🚫🚫 Ненавидеть (-50)", callback_data=f"rel:set:{user_id}:-50")
    kb.button(text="🔙 К списку", callback_data="menu:relations")
    kb.adjust(1, 1, 1, 1, 1, 1)
    return kb.as_markup()
