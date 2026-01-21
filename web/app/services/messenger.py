import httpx

class Messenger:
    def __init__(self, bot_token: str):
        self.base = f"https://api.telegram.org/bot{bot_token}"

    async def send_message(self, chat_id: int, text: str, *, reply_markup: dict | None = None, parse_mode: str = "HTML") -> bool:
        url = f"{self.base}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
                if reply_markup is not None:
                    payload["reply_markup"] = reply_markup
                resp = await client.post(url, json=payload)
                return resp.status_code == 200 and resp.json().get("ok") is True
            except Exception:
                return False
