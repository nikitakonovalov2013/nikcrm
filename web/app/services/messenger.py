import json

import httpx


def _extract_message_id(data: dict) -> int | None:
    msg = (data.get("result") or {}).get("message_id")
    try:
        return int(msg) if msg is not None else None
    except Exception:
        return None

class Messenger:
    def __init__(self, bot_token: str):
        self.base = f"https://api.telegram.org/bot{bot_token}"

    async def send_message_ex(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict | None = None,
        parse_mode: str = "HTML",
    ) -> tuple[bool, int | None, str | None]:
        url = f"{self.base}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                payload: dict = {"chat_id": int(chat_id), "text": str(text), "parse_mode": parse_mode}
                if reply_markup is not None:
                    payload["reply_markup"] = reply_markup
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    return False, None, f"HTTP {resp.status_code}"
                data = resp.json() or {}
                if data.get("ok") is not True:
                    return False, None, str(data.get("description") or "Telegram API error")
                return True, _extract_message_id(data), None
            except Exception as e:
                return False, None, str(e)

    async def send_photo_by_id_ex(
        self,
        chat_id: int,
        *,
        photo: str,
        caption: str | None = None,
        reply_markup: dict | None = None,
        parse_mode: str = "HTML",
    ) -> tuple[bool, int | None, str | None]:
        url = f"{self.base}/sendPhoto"
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                payload: dict = {"chat_id": int(chat_id), "photo": str(photo), "parse_mode": parse_mode}
                if caption is not None:
                    payload["caption"] = str(caption)
                if reply_markup is not None:
                    payload["reply_markup"] = reply_markup
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    return False, None, f"HTTP {resp.status_code}"
                data = resp.json() or {}
                if data.get("ok") is not True:
                    return False, None, str(data.get("description") or "Telegram API error")
                return True, _extract_message_id(data), None
            except Exception as e:
                return False, None, str(e)

    async def send_photo_ex(
        self,
        chat_id: int,
        *,
        file_bytes: bytes,
        filename: str,
        caption: str | None = None,
        reply_markup: dict | None = None,
        parse_mode: str = "HTML",
    ) -> tuple[bool, int | None, str | None]:
        url = f"{self.base}/sendPhoto"
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                data: dict = {"chat_id": str(int(chat_id)), "parse_mode": parse_mode}
                if caption is not None:
                    data["caption"] = str(caption)
                if reply_markup is not None:
                    data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
                files = {"photo": (filename or "photo", file_bytes)}
                resp = await client.post(url, data=data, files=files)
                if resp.status_code != 200:
                    return False, None, f"HTTP {resp.status_code}"
                payload = resp.json() or {}
                if payload.get("ok") is not True:
                    return False, None, str(payload.get("description") or "Telegram API error")
                return True, _extract_message_id(payload), None
            except Exception as e:
                return False, None, str(e)

    async def send_video_ex(
        self,
        chat_id: int,
        *,
        file_bytes: bytes,
        filename: str,
        caption: str | None = None,
        reply_markup: dict | None = None,
        parse_mode: str = "HTML",
    ) -> tuple[bool, int | None, str | None]:
        url = f"{self.base}/sendVideo"
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                data: dict = {"chat_id": str(int(chat_id)), "parse_mode": parse_mode}
                if caption is not None:
                    data["caption"] = str(caption)
                if reply_markup is not None:
                    data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
                files = {"video": (filename or "video", file_bytes)}
                resp = await client.post(url, data=data, files=files)
                if resp.status_code != 200:
                    return False, None, f"HTTP {resp.status_code}"
                payload = resp.json() or {}
                if payload.get("ok") is not True:
                    return False, None, str(payload.get("description") or "Telegram API error")
                return True, _extract_message_id(payload), None
            except Exception as e:
                return False, None, str(e)

    async def send_message(self, chat_id: int, text: str, *, reply_markup: dict | None = None, parse_mode: str = "HTML") -> bool:
        ok, _, _ = await self.send_message_ex(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        return bool(ok)

    async def edit_message_text_ex(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
    ) -> tuple[bool, str | None]:
        url = f"{self.base}/editMessageText"
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                payload: dict = {
                    "chat_id": int(chat_id),
                    "message_id": int(message_id),
                    "text": str(text),
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": bool(disable_web_page_preview),
                }
                if reply_markup is not None:
                    payload["reply_markup"] = reply_markup
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    return False, f"HTTP {resp.status_code}"
                data = resp.json() or {}
                if data.get("ok") is not True:
                    return False, str(data.get("description") or "Telegram API error")
                return True, None
            except Exception as e:
                return False, str(e)

    async def edit_message_caption_ex(
        self,
        *,
        chat_id: int,
        message_id: int,
        caption: str,
        reply_markup: dict | None = None,
        parse_mode: str = "HTML",
    ) -> tuple[bool, str | None]:
        url = f"{self.base}/editMessageCaption"
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                payload: dict = {
                    "chat_id": int(chat_id),
                    "message_id": int(message_id),
                    "caption": str(caption),
                    "parse_mode": parse_mode,
                }
                if reply_markup is not None:
                    payload["reply_markup"] = reply_markup
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    return False, f"HTTP {resp.status_code}"
                data = resp.json() or {}
                if data.get("ok") is not True:
                    return False, str(data.get("description") or "Telegram API error")
                return True, None
            except Exception as e:
                return False, str(e)

    async def edit_message_reply_markup_ex(
        self,
        *,
        chat_id: int,
        message_id: int,
        reply_markup: dict | None,
    ) -> tuple[bool, str | None]:
        url = f"{self.base}/editMessageReplyMarkup"
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                payload: dict = {
                    "chat_id": int(chat_id),
                    "message_id": int(message_id),
                    "reply_markup": (reply_markup or {"inline_keyboard": []}),
                }
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    return False, f"HTTP {resp.status_code}"
                data = resp.json() or {}
                if data.get("ok") is not True:
                    return False, str(data.get("description") or "Telegram API error")
                return True, None
            except Exception as e:
                return False, str(e)
