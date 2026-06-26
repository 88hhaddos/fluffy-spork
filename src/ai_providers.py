import aiohttp
import base64
import logging
import random
from typing import Optional

logger = logging.getLogger(__name__)


def _is_nvidia_url(url: str) -> bool:
    return "ai.api.nvidia.com" in url or "integrate.api.nvidia.com" in url


def _nvidia_image_endpoint(base_url: str, model: str) -> str:
    """NVIDIA image API использует /v1/genai/<model> вместо /v1/images/generations."""
    base = base_url.rstrip("/")
    if "/v1/genai/" in base:
        return base
    if base.endswith("/v1"):
        return base + "/genai/" + model
    return base + "/v1/genai/" + model


def _nvidia_text_endpoint(base_url: str) -> str:
    """NVIDIA text API использует /v1/chat/completions (OpenAI-совместимый)."""
    return base_url.rstrip("/") + "/chat/completions"


class AIProviderManager:
    """Мульти-провайдерная AI система с fallback."""

    def __init__(self, db):
        self.db = db
        self.session: Optional[aiohttp.ClientSession] = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=300),
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    # ─── Text Chat Completion ───

    async def chat_completion(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ) -> str:
        providers = await self.db.get_active_providers("text")
        if not providers:
            raise RuntimeError(
                "Нет активных text-провайдеров. Добавьте через /admin → 🤖 AI Провайдеры."
            )

        errors = []
        for p in providers:
            try:
                result = await self._call_chat(p, messages, temperature, max_tokens)
                logger.info(f"✅ Ответ от: {p['name']} — {p['model']}")
                return result
            except Exception as e:
                logger.warning(f"❌ Провайдер {p['name']} ({p['model']}) failed: {e}")
                errors.append(f"{p['name']}: {e}")
                continue

        raise RuntimeError(f"Все text-провайдеры недоступны: {'; '.join(errors)}")

    async def _call_chat(
        self, provider: dict, messages: list[dict],
        temperature: float, max_tokens: int,
    ) -> str:
        session = await self.get_session()
        url = provider["base_url"].rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {provider['api_key']}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": provider["model"],
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 429:
                raise RuntimeError("Rate limited")
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"HTTP {resp.status}: {text[:300]}")
            data = await resp.json()
            return data["choices"][0]["message"]["content"]

    # ─── Summarization ───

    async def summarize(self, text: str, max_tokens: int = 500) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты ассистент для суммаризации. Выдели самое важное и ключевые "
                    "события из текста. Будь краток но информативен. Пиши на русском."
                ),
            },
            {
                "role": "user",
                "content": f"Суммаризуй следующий текст:\n\n{text}",
            },
        ]
        return await self.chat_completion(messages, temperature=0.3, max_tokens=max_tokens)

    # ─── Image Generation ───

    async def generate_image(self, prompt: str, size: str = "1024x1024") -> bytes:
        providers = await self.db.get_active_providers("image")
        if not providers:
            raise RuntimeError(
                "Нет активных image-провайдеров. Добавьте через /admin → 🤖 AI Провайдеры."
            )

        errors = []
        for p in providers:
            try:
                result = await self._call_image_gen(p, prompt, size)
                logger.info(f"✅ Фото от: {p['name']} — {p['model']}")
                return result
            except Exception as e:
                logger.warning(f"❌ Image провайдер {p['name']} ({p['model']}) failed: {e}")
                errors.append(f"{p['name']}: {e}")
                continue

        raise RuntimeError(f"Все image-провайдеры недоступны: {'; '.join(errors)}")

    async def _call_image_gen(self, provider: dict, prompt: str, size: str) -> bytes:
        if _is_nvidia_url(provider["base_url"]):
            return await self._call_nvidia_image_gen(provider, prompt, size)
        return await self._call_openai_image_gen(provider, prompt, size)

    # ─── OpenAI-compatible image generation ───

    async def _call_openai_image_gen(self, provider: dict, prompt: str, size: str) -> bytes:
        session = await self.get_session()
        url = provider["base_url"].rstrip("/") + "/images/generations"
        headers = {
            "Authorization": f"Bearer {provider['api_key']}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": provider["model"],
            "prompt": prompt,
            "n": 1,
            "size": size,
            "response_format": "b64_json",
        }

        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 429:
                raise RuntimeError("Rate limited")
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"HTTP {resp.status}: {text[:300]}")
            data = await resp.json()
            return await self._extract_image(data, session)

    # ─── NVIDIA image generation ───

    async def _call_nvidia_image_gen(self, provider: dict, prompt: str, size: str) -> bytes:
        session = await self.get_session()
        model = provider["model"]
        url = _nvidia_image_endpoint(provider["base_url"], model)
        headers = {
            "Authorization": f"Bearer {provider['api_key']}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        aspect = self._size_to_aspect(size)

        if "stable-diffusion-xl" in model or "sdxl" in model:
            payload = self._nvidia_sdxl_payload(model, prompt)
        elif "stable-diffusion-3" in model or "sd3" in model:
            payload = self._nvidia_sd3_payload(prompt, aspect)
        else:
            payload = self._nvidia_sdxl_payload(model, prompt)

        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 429:
                raise RuntimeError("Rate limited")
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"HTTP {resp.status}: {text[:300]}")
            data = await resp.json()
            return await self._extract_nvidia_image(data, session)

    def _nvidia_sdxl_payload(self, model: str, prompt: str) -> dict:
        is_turbo = "turbo" in model
        return {
            "text_prompts": [{"text": prompt, "weight": 1.0}],
            "cfg_scale": 1 if is_turbo else 7,
            "seed": random.randint(0, 2**32 - 1),
            "steps": 1 if is_turbo else 30,
            "sampler": "K_EULER_ANCESTRAL" if is_turbo else "K_DPMPP_2M",
        }

    def _nvidia_sd3_payload(self, prompt: str, aspect_ratio: str) -> dict:
        return {
            "prompt": prompt,
            "cfg_scale": 7,
            "aspect_ratio": aspect_ratio,
            "seed": random.randint(0, 2**32 - 1),
            "steps": 30,
            "negative_prompt": "",
        }

    @staticmethod
    def _size_to_aspect(size: str) -> str:
        mapping = {
            "1024x1024": "1:1",
            "768x1344": "9:16",
            "1344x768": "16:9",
            "1216x832": "3:2",
            "832x1216": "2:3",
        }
        return mapping.get(size, "1:1")

    # ─── Image Editing ───

    async def edit_image(
        self, image_bytes: bytes, prompt: str, size: str = "1024x1024",
    ) -> bytes:
        providers = await self.db.get_active_providers("image")
        if not providers:
            raise RuntimeError(
                "Нет активных image-провайдеров для редактирования."
            )

        errors = []
        for p in providers:
            try:
                if _is_nvidia_url(p["base_url"]):
                    return await self._call_nvidia_image_edit(p, image_bytes, prompt, size)
                return await self._call_openai_image_edit(p, image_bytes, prompt, size)
            except Exception as e:
                logger.warning(f"Image edit провайдер {p['name']} failed: {e}")
                errors.append(f"{p['name']}: {e}")
                continue

        raise RuntimeError(f"Все image-провайдеры недоступны: {'; '.join(errors)}")

    async def _call_openai_image_edit(
        self, provider: dict, image_bytes: bytes, prompt: str, size: str,
    ) -> bytes:
        session = await self.get_session()
        url = provider["base_url"].rstrip("/") + "/images/edits"
        headers = {
            "Authorization": f"Bearer {provider['api_key']}",
        }

        form = aiohttp.FormData()
        form.add_field("image", image_bytes, filename="image.png", content_type="image/png")
        form.add_field("prompt", prompt)
        form.add_field("n", "1")
        form.add_field("size", size)
        form.add_field("response_format", "b64_json")

        async with session.post(url, data=form, headers=headers) as resp:
            if resp.status == 429:
                raise RuntimeError("Rate limited")
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"HTTP {resp.status}: {text[:300]}")
            data = await resp.json()
            return await self._extract_image(data, session)

    async def _call_nvidia_image_edit(
        self, provider: dict, image_bytes: bytes, prompt: str, size: str,
    ) -> bytes:
        """NVIDIA image edit: img2img через base64 + prompt."""
        session = await self.get_session()
        model = provider["model"]
        url = _nvidia_image_endpoint(provider["base_url"], model)
        headers = {
            "Authorization": f"Bearer {provider['api_key']}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        aspect = self._size_to_aspect(size)

        if "stable-diffusion-xl" in model or "sdxl" in model:
            payload = self._nvidia_sdxl_payload(model, prompt)
            payload["init_image"] = b64_image
            payload["image_strength"] = 0.5
        elif "stable-diffusion-3" in model or "sd3" in model:
            payload = self._nvidia_sd3_payload(prompt, aspect)
            payload["init_image"] = b64_image
            payload["image_strength"] = 0.5
        else:
            payload = {
                "text_prompts": [{"text": prompt, "weight": 1.0}],
                "init_image": b64_image,
                "image_strength": 0.5,
                "cfg_scale": 7,
                "seed": random.randint(0, 2**32 - 1),
                "steps": 30,
            }

        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 429:
                raise RuntimeError("Rate limited")
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"HTTP {resp.status}: {text[:300]}")
            data = await resp.json()
            return await self._extract_nvidia_image(data, session)

    # ─── Image extraction helpers ───

    async def _extract_image(self, data: dict, session: aiohttp.ClientSession) -> bytes:
        if "data" in data and len(data["data"]) > 0:
            item = data["data"][0]
            if "b64_json" in item:
                return base64.b64decode(item["b64_json"])
            if "url" in item:
                async with session.get(item["url"]) as img_resp:
                    return await img_resp.read()
        raise RuntimeError("Неожиданный формат ответа image API")

    async def _extract_nvidia_image(self, data: dict, session: aiohttp.ClientSession) -> bytes:
        if "artifacts" in data and len(data["artifacts"]) > 0:
            artifact = data["artifacts"][0]
            if "b64" in artifact:
                return base64.b64decode(artifact["b64"])
            if "base64" in artifact:
                return base64.b64decode(artifact["base64"])
            if "url" in artifact:
                async with session.get(artifact["url"]) as img_resp:
                    return await img_resp.read()

        if "image" in data:
            if isinstance(data["image"], str):
                return base64.b64decode(data["image"])
            if isinstance(data["image"], dict) and "b64" in data["image"]:
                return base64.b64decode(data["image"]["b64"])

        if "data" in data:
            return await self._extract_image(data, session)

        raise RuntimeError(f"Неожиданный формат ответа NVIDIA image API: {str(data)[:300]}")
