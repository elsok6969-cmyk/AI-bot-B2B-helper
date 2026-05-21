from __future__ import annotations

import base64

from src.ai.client import ai_client
from src.config import settings

_OCR_SYSTEM = (
    "Ты распознаёшь текст переписки на изображениях (скриншоты Telegram, "
    "WhatsApp, email-клиентов и т.п.). Верни только распознанный текст, "
    "одним блоком, без описаний картинки и без дополнительных комментариев. "
    "Если на изображении несколько сообщений — разделяй их переносом строки и "
    "сохраняй порядок сверху вниз. Если на изображении нет читаемого текста — "
    "ответь пустой строкой."
)


async def ocr_image(image_bytes: bytes, media_type: str) -> str:
    """Run OCR on an image via Claude vision. Returns plain text (may be empty)."""
    img_b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    response = await ai_client.call(
        request_type="ocr",
        model=settings.vision_model,
        system=_OCR_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_b64,
                        },
                    },
                    {"type": "text", "text": "Распознай текст."},
                ],
            }
        ],
        max_tokens=2048,
        temperature=0.0,
    )
    text_parts = [b.text for b in response.content if b.type == "text"]
    return "\n".join(text_parts).strip()
