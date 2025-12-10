import httpx

class Messenger:
    def __init__(self, bot_token: str):
        self.base = f"https://api.telegram.org/bot{bot_token}"

    async def send_message(self, chat_id: int, text: str) -> bool:
        url = f"{self.base}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.post(url, data={"chat_id": chat_id, "text": text})
                return resp.status_code == 200 and resp.json().get("ok") is True
            except Exception:
                return False
