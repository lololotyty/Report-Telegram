import os
import logging
import asyncio
import random
import re
from datetime import datetime
from typing import List, Dict, Any, Set

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

# CSRF Token Configuration
CSRF_TOKENS = {
    'token1': "FT6Vp2oJrLLct9xB3VvtQHFr1UNWPHfntUaPTc1WHMP0ITJqbxuPj1iVubuchpv3",
    'token2': "W2L71Ern1tYEOKcaSXQfsD0eBYcsf1Pr5yhMYnASmuSs921xJR86Qtn0pZeeKi2N"
}

SESSION_COOKIES = {
    'csrftoken': CSRF_TOKENS['token1'],
    'sessionid': 'jcl1bqxzby1c8pspe592y5ub55x01scm'
}

class ProxyManager:
    def __init__(self):
        self.proxies = self.load_proxies()
        self.working_proxies = set()
        self.failed_proxies = set()
        self.current_index = 0

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
        if not self.proxies:
            return ""
        
        # Round-robin selection
        proxy = self.proxies[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.proxies)
        return proxy

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
        self.active_tasks: Dict[int, Dict[str, Any]] = {}
        self.current_token_index = 0
        self.session_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
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

    def get_next_token(self) -> str:
        """Get next CSRF token using round-robin"""
        tokens = list(CSRF_TOKENS.values())
        token = tokens[self.current_token_index]
        self.current_token_index = (self.current_token_index + 1) % len(tokens)
        return token

    async def report_with_proxy(self, channel: str, proxy: str, user_id: int) -> bool:
        """Submit a report using a specific proxy"""
        try:
            if user_id in self.active_tasks and self.active_tasks[user_id].get('cancelled', False):
                logger.info(f"Task cancelled for user {user_id}")
                return False

            # Validate proxy format
            if not any(proxy.startswith(prefix) for prefix in ['http://', 'socks4://', 'socks5://']):
                logger.error(f"Invalid proxy format: {proxy}")
                return False

            # Configure timeout and connector
            timeout = aiohttp.ClientTimeout(total=20, connect=10)
            connector = ProxyConnector.from_url(proxy, verify_ssl=False, force_close=True)
            
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                cookie_jar=aiohttp.CookieJar(),
                trust_env=True
            ) as session:
                # Set session cookies
                session.cookie_jar.update_cookies(SESSION_COOKIES)

                # Test proxy connection first
                try:
                    async with session.get('https://api.telegram.org', ssl=False) as test_response:
                        if test_response.status != 200:
                            logger.error(f"Proxy test failed: {proxy}")
                            return False
                except Exception as e:
                    logger.error(f"Proxy connection test failed: {proxy} - {str(e)}")
                    return False

                if user_id in self.active_tasks and self.active_tasks[user_id].get('cancelled', False):
                    return False

                # Get CSRF token
                csrf_token = self.get_next_token()

                # Prepare report data
                headers = self.session_headers.copy()
                headers.update({
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRF-Token': csrf_token,
                    'X-CSRFToken': csrf_token,
                    'Cookie': f'csrftoken={csrf_token}; sessionid={SESSION_COOKIES["sessionid"]}'
                })
                
                form_data = {
                    'csrf_token': csrf_token,
                    'csrfmiddlewaretoken': csrf_token,
                    'channel': channel,
                    'reason': random.choice(self.messages),
                    'submit': 'Report Channel'
                }

                # Submit report
                async with session.post(
                    REPORT_URL,
                    headers=headers,
                    data=form_data,
                    allow_redirects=True,
                    ssl=False
                ) as response:
                    response_text = await response.text()
                    success = response.status == 200 and ('report has been sent' in response_text.lower() or 'thank you' in response_text.lower())
                    if success:
                        logger.info(f"Successfully reported channel using proxy {proxy}")
                    else:
                        logger.error(f"Failed to report channel. Status: {response.status}, Response: {response_text[:200]}")
                    self.proxy_manager.mark_proxy_status(proxy, success)
                    return success

        except Exception as e:
            logger.error(f"Error with proxy {proxy}: {str(e)}")
            self.proxy_manager.mark_proxy_status(proxy, False)
            return False

    def cancel_user_tasks(self, user_id: int) -> int:
        """Cancel all active tasks for a user"""
        if user_id not in self.active_tasks:
            return 0

        # Mark as cancelled
        self.active_tasks[user_id]['cancelled'] = True
        
        # Cancel all tasks
        cancelled = 0
        for task in self.active_tasks[user_id].get('tasks', set()):
            if not task.done():
                task.cancel()
                cancelled += 1

        return cancelled

    async def mass_report(self, channel: str, user_id: int, num_reports: int = 50) -> str:
        """Send multiple reports using different proxies"""
        if not self.proxy_manager.proxies:
            return "No proxies available! Please add proxies to http_proxies.txt"

        if user_id in self.active_tasks and self.active_tasks[user_id].get('tasks'):
            return "You already have an active reporting process. Use /cancel to stop it first."

        self.active_tasks[user_id] = {
            'tasks': set(),
            'cancelled': False,
            'start_time': datetime.now()
        }

        successful_reports = 0
        failed_reports = 0
        semaphore = asyncio.Semaphore(CONCURRENT_REPORTS)

        async def report_with_semaphore():
            try:
                async with semaphore:
                    if self.active_tasks[user_id].get('cancelled', False):
                        raise asyncio.CancelledError()
                    
                    proxy = self.proxy_manager.get_proxy()
                    for _ in range(MAX_RETRIES):
                        if await self.report_with_proxy(channel, proxy, user_id):
                            return True
                        if self.active_tasks[user_id].get('cancelled', False):
                            raise asyncio.CancelledError()
                    return False
            except asyncio.CancelledError:
                logger.info(f"Report task cancelled for user {user_id}")
                raise
            except Exception as e:
                logger.error(f"Error in report task: {str(e)}")
                return False

        try:
            tasks = [asyncio.create_task(report_with_semaphore()) for _ in range(num_reports)]
            self.active_tasks[user_id]['tasks'].update(tasks)
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            successful_reports = sum(1 for r in results if r is True)
            failed_reports = sum(1 for r in results if r is False)
            cancelled_reports = sum(1 for r in results if isinstance(r, asyncio.CancelledError))

            duration = datetime.now() - self.active_tasks[user_id]['start_time']
            
            status = (
                f"📊 Report Results:\n"
                f"✅ Successful: {successful_reports}\n"
                f"❌ Failed: {failed_reports}\n"
            )

            if cancelled_reports:
                status += f"🚫 Cancelled: {cancelled_reports}\n"

            if successful_reports + failed_reports > 0:
                success_rate = (successful_reports/(successful_reports + failed_reports))*100
                status += f"📈 Success Rate: {success_rate:.1f}%\n"

            status += (
                f"🔄 Working Proxies: {len(self.proxy_manager.working_proxies)}\n"
                f"⏱ Duration: {duration.seconds}s"
            )
            return status

        finally:
            self.active_tasks[user_id]['tasks'].clear()
            self.active_tasks[user_id]['cancelled'] = False

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
        "/report [channel_link] - Report a Telegram channel\n"
        "/cancel - Cancel ongoing reporting process"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show help message."""
    help_text = (
        "Here are the available commands:\n\n"
        "/start - Start the bot\n"
        "/help - Show this help message\n"
        "/echo [message] - Echo back your message\n"
        "/time - Get current time\n"
        "/report [channel_link] - Report a Telegram channel\n"
        "/cancel - Cancel ongoing reporting process\n\n"
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

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel ongoing reporting process."""
    user_id = update.effective_user.id
    cancelled = report_bot.cancel_user_tasks(user_id)
    
    if cancelled > 0:
        await update.message.reply_text(
            f"✅ Successfully cancelled {cancelled} active reporting tasks."
        )
    else:
        await update.message.reply_text(
            "❌ No active reporting process to cancel."
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
            "⏳ This may take a few minutes. Please wait...\n"
            "Use /cancel to stop the process."
        )
        
        result = await report_bot.mass_report(channel_username, update.effective_user.id)
        
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
    application.add_handler(CommandHandler("cancel", cancel_command))

    # Handle non-command messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start the Bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
