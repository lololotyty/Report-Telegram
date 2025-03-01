import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import aiohttp
import re
import random
import asyncio
from datetime import datetime

# Load environment variables
if os.path.exists('.env'):
    load_dotenv()

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Regular expression for Telegram channel links
CHANNEL_LINK_PATTERN = r'^(?:https?://)?(?:t\.me|telegram\.me)/(?:joinchat/)?([a-zA-Z0-9_-]+)$'

def load_proxies():
    """Load proxies from http_proxies.txt"""
    try:
        with open('http_proxies.txt', 'r') as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        logger.error("http_proxies.txt not found!")
        return []

def load_messages():
    """Load messages from message.txt"""
    try:
        with open('message.txt', 'r') as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        logger.error("message.txt not found!")
        return ["This channel violates Telegram's terms of service"]

async def report_with_proxy(session, channel, reason, proxy):
    """Report channel using a specific proxy"""
    try:
        report_url = "https://telegram.org/dsa-report"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        proxy_url = f"http://{proxy}"
        
        async with session.post(report_url, 
                              proxy=proxy_url,
                              headers=headers,
                              data={
                                  'channel': channel,
                                  'reason': reason
                              },
                              timeout=30) as response:
            return response.status == 200
    except Exception as e:
        logger.error(f"Error with proxy {proxy}: {str(e)}")
        return False

async def mass_report(channel_username, num_reports=50):
    """Send multiple reports using different proxies and messages"""
    proxies = load_proxies()
    messages = load_messages()
    
    if not proxies:
        return "No proxies found in http_proxies.txt!"
    if not messages:
        return "No messages found in message.txt!"
    
    successful_reports = 0
    async with aiohttp.ClientSession() as session:
        tasks = []
        for _ in range(num_reports):
            proxy = random.choice(proxies)
            reason = random.choice(messages)
            tasks.append(report_with_proxy(session, channel_username, reason, proxy))
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        successful_reports = sum(1 for r in results if r is True)
    
    return f"Completed reporting process:\n✅ Successful reports: {successful_reports}\n❌ Failed reports: {num_reports - successful_reports}"

async def report_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle channel reporting."""
    if len(context.args) < 1:
        await update.message.reply_text(
            "Please provide the channel link.\n"
            "Usage: /report [channel_link]\n"
            "Example: /report https://t.me/channelname"
        )
        return

    channel_link = context.args[0]

    # Validate channel link format
    if not re.match(CHANNEL_LINK_PATTERN, channel_link):
        await update.message.reply_text(
            "Invalid channel link format. Please use a valid t.me link.\n"
            "Example: https://t.me/channelname"
        )
        return

    try:
        # Extract channel username from the link
        channel_username = re.search(CHANNEL_LINK_PATTERN, channel_link).group(1)
        
        # Send initial message
        status_message = await update.message.reply_text("🚀 Starting mass report process...")
        
        # Start mass reporting
        result = await mass_report(channel_username)
        
        # Update status message with results
        await status_message.edit_text(
            f"📊 Report Status for @{channel_username}:\n\n{result}\n\n"
            f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    except Exception as e:
        logger.error(f"Error reporting channel: {str(e)}")
        await update.message.reply_text(
            "Sorry, there was an error processing your report. Please try again later."
        )
        return

# [Keep other existing functions: start, help_command, echo, time_command, handle_message]

def main() -> None:
    """Start the bot."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("No bot token found! Make sure TELEGRAM_BOT_TOKEN environment variable is set")
        return

    application = Application.builder().token(token).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("echo", echo))
    application.add_handler(CommandHandler("time", time_command))
    application.add_handler(CommandHandler("report", report_channel))

    # Handle all non-command messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start the Bot using polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
