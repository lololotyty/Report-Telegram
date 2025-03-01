import os
import logging
import asyncio
import random
import re
from datetime import datetime
from typing import List, Dict, Any

import aiohttp
from aiohttp_socks import ProxyConnector
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Load environment variables
if os.path.exists('.env'):
    load_dotenv()

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constants
CHANNEL_LINK_PATTERN = r'^(?:https?://)?(?:t\.me|telegram\.me)/(?:joinchat/)?([a-zA-Z0-9_-]+)$'
MAX_RETRIES = 3
CONCURRENT_REPORTS = 10
DEFAULT_TIMEOUT = 30
REPORT_URL = "https://telegram.org/dsa-report"

class ProxyManager:
    def __init__(self):
        self.proxies = self.load_proxies()
        self.working_proxies = set()
        self.failed_proxies = set()

    @staticmethod
    def load_proxies() -> List[str]:
        """Load and validate proxies from http_proxies.txt"""
        try:
            with open('http_proxies.txt', 'r') as f:
                return [line.strip() for line in f if line.strip() and not line.startswith('#')]
        except FileNotFoundError:
            logger.error("http_proxies.txt not found!")
            return []

    def get_proxy(self) -> str:
        """Get a working proxy, preferably from the working_proxies set"""
        if self.working_proxies:
            return random.choice(list(self.working_proxies))
        return random.choice(self.proxies) if self.proxies else ""

    def mark_proxy_status(self, proxy: str, success: bool):
        """Mark proxy as working or failed"""
        if success:
            self.working_proxies.add(proxy)
            self.failed_proxies.discard(proxy)
        else:
            self.failed_proxies.add(proxy)
            self.working_proxies.discard(proxy)

class ReportBot:
    def __init__(self):
        self.proxy_manager = ProxyManager()
        self.messages = self.load_messages()
        self.session_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Origin': 'https://telegram.org',
            'Referer': REPORT_URL
        }

    @staticmethod
    def load_messages() -> List[str]:
        """Load report messages from message.txt"""
        try:
            with open('message.txt', 'r') as f:
                return [line.strip() for line in f if line.strip()]
        except FileNotFoundError:
            logger.error("message.txt not found!")
            return ["This channel violates Telegram's terms of service"]

    async def get_csrf_token(self, session: aiohttp.ClientSession) -> str:
        """Fetch CSRF token from the report page"""
        try:
            async with session.get(REPORT_URL, timeout=DEFAULT_TIMEOUT) as response:
                if response.status != 200:
                    return ""
                text = await response.text()
                soup = BeautifulSoup(text, 'html.parser')
                token = soup.find('input', {'name': 'csrf_token'})
                return token['value'] if token else ""
        except Exception as e:
            logger.error(f"Error getting CSRF token: {str(e)}")
            return ""

    async def report_with_proxy(self, channel: str, proxy: str) -> bool:
        """Submit a report using a specific proxy"""
        try:
            connector = ProxyConnector.from_url(proxy, verify_ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                # Get CSRF token
                csrf_token = await self.get_csrf_token(session)
                if not csrf_token:
                    return False

                # Prepare report data
                headers = self.session_headers.copy()
                headers['Content-Type'] = 'application/x-www-form-urlencoded'
                
                form_data = {
                    'csrf_token': csrf_token,
                    'channel': channel,
                    'reason': random.choice(self.messages),
                    'submit': 'Report Channel'
                }

                # Submit report
                async with session.post(
                    REPORT_URL,
                    headers=headers,
                    data=form_data,
                    timeout=DEFAULT_TIMEOUT
                ) as response:
                    success = response.status == 200 and 'report has been sent' in (await response.text()).lower()
                    self.proxy_manager.mark_proxy_status(proxy, success)
                    return success

        except Exception as e:
            logger.error(f"Error with proxy {proxy}: {str(e)}")
            self.proxy_manager.mark_proxy_status(proxy, False)
            return False

    async def mass_report(self, channel: str, num_reports: int = 50) -> str:
        """Send multiple reports using different proxies"""
        if not self.proxy_manager.proxies:
            return "No proxies available! Please add proxies to http_proxies.txt"

        successful_reports = 0
        failed_reports = 0
        semaphore = asyncio.Semaphore(CONCURRENT_REPORTS)

        async def report_with_semaphore():
            async with semaphore:
                proxy = self.proxy_manager.get_proxy()
                for _ in range(MAX_RETRIES):
                    if await self.report_with_proxy(channel, proxy):
                        return True
                return False

        tasks = [report_with_semaphore() for _ in range(num_reports)]
        results = await asyncio.gather(*tasks)

        successful_reports = sum(1 for r in results if r)
        failed_reports = sum(1 for r in results if not r)

        return (
            f"📊 Report Results:\n"
            f"✅ Successful: {successful_reports}\n"
            f"❌ Failed: {failed_reports}\n"
            f"📈 Success Rate: {(successful_reports/num_reports)*100:.1f}%\n"
            f"🔄 Working Proxies: {len(self.proxy_manager.working_proxies)}"
        )

# Initialize the bot
report_bot = ReportBot()

# Command Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(
        f"Hi {user.mention_html()}! 👋\n\n"
        "I'm your friendly Telegram bot. Here are the commands you can use:\n"
        "/start - Show this welcome message\n"
        "/help - Show available commands\n"
        "/echo [message] - Echo back your message\n"
        "/time - Get current time\n"
        "/report [channel_link] - Report a Telegram channel"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show help message."""
    help_text = (
        "Here are the available commands:\n\n"
        "/start - Start the bot\n"
        "/help - Show this help message\n"
        "/echo [message] - Echo back your message\n"
        "/time - Get current time\n"
        "/report [channel_link] - Report a Telegram channel\n\n"
        "For reporting, use format:\n"
        "/report https://t.me/channelname"
    )
    await update.message.reply_text(help_text)

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message."""
    if not context.args:
        await update.message.reply_text("Please provide a message to echo!\nUsage: /echo [message]")
        return
    
    message = ' '.join(context.args)
    await update.message.reply_text(f"You said: {message}")

async def time_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send current time."""
    current_time = datetime.now().strftime("%H:%M:%S")
    await update.message.reply_text(f"Current time is: {current_time}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle non-command messages."""
    await update.message.reply_text(
        "I can respond to commands! Try /help to see what I can do."
    )

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
    match = re.match(CHANNEL_LINK_PATTERN, channel_link)
    
    if not match:
        await update.message.reply_text(
            "Invalid channel link format. Please use a valid t.me link.\n"
            "Example: https://t.me/channelname"
        )
        return

    try:
        channel_username = match.group(1)
        status_message = await update.message.reply_text(
            "🚀 Starting mass report process...\n"
            "⏳ This may take a few minutes. Please wait..."
        )
        
        result = await report_bot.mass_report(channel_username)
        
        await status_message.edit_text(
            f"Channel: @{channel_username}\n\n{result}\n\n"
            f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    except Exception as e:
        logger.error(f"Error reporting channel: {str(e)}")
        await update.message.reply_text(
            "Sorry, there was an error processing your report. Please try again later."
        )

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

    # Handle non-command messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start the Bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
