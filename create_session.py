"""Run locally once. Keep the output private; put it in Railway TELEGRAM_SESSION."""
import os
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input('Telegram API ID: ').strip())
api_hash = input('Telegram API Hash: ').strip()
with TelegramClient(StringSession(), api_id, api_hash) as client:
    print('\nTELEGRAM_SESSION=' + client.session.save())
