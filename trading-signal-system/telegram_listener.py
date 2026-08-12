import asyncio
import logging
import httpx
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

class TelegramListener:
    """
    Escucha mensajes entrantes de Telegram usando Long Polling.
    Utilizado para implementar el Kill-Switch (/stop y /start).
    """
    def __init__(self, token: str, on_command: Callable[[str, int], Awaitable[None]], authorized_chat_id: str):
        self.token = token
        self.on_command = on_command
        self.authorized_chat_id = authorized_chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.is_running = False
        self.offset = 0

    async def start(self):
        self.is_running = True
        logger.info("🎧 Telegram Listener iniciado (Kill-Switch activo).")
        
        async with httpx.AsyncClient(timeout=40.0) as client:
            while self.is_running:
                try:
                    url = f"{self.base_url}/getUpdates"
                    params = {"offset": self.offset, "timeout": 30, "allowed_updates": ["message"]}
                    response = await client.get(url, params=params)
                    
                    if response.status_code != 200:
                        logger.error(f"Error HTTP Telegram: {response.status_code}")
                        await asyncio.sleep(5)
                        continue
                        
                    data = response.json()
                    if not data.get("ok"):
                        logger.error(f"Error en Telegram getUpdates: {data}")
                        await asyncio.sleep(5)
                        continue
                        
                    for update in data.get("result", []):
                        self.offset = update["update_id"] + 1
                        message = update.get("message")
                        
                        if not message or "text" not in message:
                            continue
                            
                        text = message["text"].strip().lower()
                        chat_id = message["chat"]["id"]
                        
                        if text in ["/stop", "/start"]:
                            if str(chat_id) != self.authorized_chat_id:
                                logger.warning(f"⚠️ Comando /{text} ignorado de chat no autorizado: {chat_id}")
                                continue
                            await self.on_command(text, chat_id)
                            
                except httpx.ReadTimeout:
                    continue  # Timeout normal esperado en long polling
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error en TelegramListener: {e}")
                    await asyncio.sleep(5)
                    
    def stop(self):
        self.is_running = False
        logger.info("🛑 Telegram Listener detenido.")
