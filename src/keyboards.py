from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⚙️ Настройки бота", callback_data="menu:settings")
    kb.button(text="🤖 AI Провайдеры", callback_data="menu:providers")
    kb.button(text="📝 Личность и промпт", callback_data="menu:personality")
    kb.button(text="🧠 Контекст и память", callback_data="menu:context")
    kb.button(text="👥 Админы", callback_data="menu:admins")
    kb.button(text="📊 Статистика", callback_data="menu:stats")
    kb.button(text="❌ Закрыть", callback_data="menu:close")
    kb.adjust(1, 1, 1, 1, 1, 1, 1)
    return kb.as_markup()


def settings_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Имя бота", callback_data="set:bot_name")
    kb.button(text="🎲 Частота авто-ответов", callback_data="set:frequency")
    kb.button(text="📏 Размер контекста", callback_data="set:context_size")
    kb.button(text="🔙 Назад", callback_data="menu:main")
    kb.adjust(1, 1, 1, 1)
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
    kb.button(text="⚡ NVIDIA пресеты", callback_data="prov:nvidia_presets")
    kb.button(text="🔙 Назад", callback_data="menu:main")
    kb.adjust(1, 1, 1, 1)
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


def nvidia_presets_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📝 NVIDIA Nemotron 70B (text)", callback_data="nv_preset:nv_text_nemotron")
    kb.button(text="📝 NVIDIA Llama 405B (text)", callback_data="nv_preset:nv_text_llama")
    kb.button(text="🔙 К списку", callback_data="menu:providers")
    kb.adjust(1, 1, 1)
    return kb.as_markup()


def provider_detail_kb(provider_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Включить/выключить", callback_data=f"prov:toggle:{provider_id}")
    kb.button(text="🗑 Удалить", callback_data=f"prov:delete:{provider_id}")
    kb.button(text="🔙 К списку", callback_data="menu:providers")
    kb.adjust(1, 1, 1)
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
