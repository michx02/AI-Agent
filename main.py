from bot import bot

from dotenv import load_dotenv  # pip install python-dotenv
import os

load_dotenv()  # reads .env in project root

YOUR_BOT_TOKEN = os.getenv("YOUR_BOT_TOKEN")
if not YOUR_BOT_TOKEN:
    raise RuntimeError("Missing YOUR_BOT_TOKEN")

if __name__ == "__main__":
    bot.run(YOUR_BOT_TOKEN)