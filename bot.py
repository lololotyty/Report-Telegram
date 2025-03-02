import os
import logging
import asyncio
import random
import re
from datetime import datetime
from typing import List, Dict, Any, Set

import aiohttp
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
CONCURRENT_REPORTS = 5  # Reduced to avoid detection
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

# Browser-like Headers
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,de;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'same-origin',
    'Sec-Fetch-User': '?1',
    'Cache-Control': 'max-age=0',
    'Pragma': 'no-cache',
    'DNT': '1'
}

class PhoneNumberManager:
    # EU country codes and their corresponding country names
    EU_COUNTRIES = {
        '43': 'AT',  # Austria
        '32': 'BE',  # Belgium
        '45': 'DK',  # Denmark
        '358': 'FI', # Finland
        '33': 'FR',  # France
        '49': 'DE',  # Germany
        '353': 'IE', # Ireland
        '39': 'IT',  # Italy
        '31': 'NL',  # Netherlands
        '48': 'PL',  # Poland
        '34': 'ES',  # Spain
        '46': 'SE',  # Sweden
    }

    def __init__(self):
        self.phone_numbers = self.load_phone_numbers()
        self.working_numbers = set()
        self.failed_numbers = set()
        self.current_index = 0

    @staticmethod
    def is_valid_number(number: str) -> bool:
        """Check if the phone number is valid EU or Indian"""
        if not number.startswith('+'):
            return False
        
        # Remove + prefix for checking
        number = number[1:]
        
        # Check for Indian numbers
        if number.startswith('91') and len(number) == 12:
            return True
            
        # Check for EU numbers
        for country_code in PhoneNumberManager.EU_COUNTRIES.keys():
            if number.startswith(country_code):
                remaining_digits = number[len(country_code):]
                # Most EU mobile numbers are 9-10 digits after country code
                if 9 <= len(remaining_digits) <= 10:
                    return True
        return False

    def get_country_code(self, number: str) -> str:
        """Get the country code for a phone number"""
        if not number.startswith('+'):
            return 'UN'  # Unknown
            
        number = number[1:]  # Remove + prefix
        
        if number.startswith('91'):
            return 'IN'
            
        for code, country in self.EU_COUNTRIES.items():
            if number.startswith(code):
                return country
                
        return 'UN'  # Unknown

    def load_phone_numbers(self) -> List[str]:
        """Load phone numbers from phone_numbers.txt"""
        try:
            with open('phone_numbers.txt', 'r') as f:
                numbers = []
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if self.is_valid_number(line):
                        numbers.append(line)
                        country = self.get_country_code(line)
                        logger.info(f"Added {country} phone number: {line}")
                    else:
                        logger.debug(f"Invalid phone number format: {line}")
                
                if not numbers:
                    logger.warning("No valid phone numbers found in phone_numbers.txt!")
                else:
                    logger.info(f"Loaded {len(numbers)} phone numbers")
                return numbers
        except FileNotFoundError:
            logger.error("phone_numbers.txt not found!")
            return []

    def get_phone_number(self) -> str:
        """Get a working phone number"""
        if self.working_numbers:
            return random.choice(list(self.working_numbers))
        if not self.phone_numbers:
            logger.error("No phone numbers available!")
            return ""
        
        # Round-robin selection
        number = self.phone_numbers[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.phone_numbers)
        return number

    def mark_number_status(self, number: str, success: bool):
        """Mark phone number as working or failed"""
        if success:
            self.working_numbers.add(number)
            self.failed_numbers.discard(number)
        else:
            self.failed_numbers.add(number)
            self.working_numbers.discard(number)

class ReportBot:
    def __init__(self):
        self.phone_manager = PhoneNumberManager()
        self.messages = self.load_messages()
        self.active_tasks: Dict[int, Dict[str, Any]] = {}
        self.current_token_index = 0

    @staticmethod
    def load_messages() -> List[str]:
        """Load report messages from message.txt"""
        try:
            with open('message.txt', 'r') as f:
                return [line.strip() for line in f if line.strip() and not line.startswith('#')]
        except FileNotFoundError:
            logger.error("message.txt not found!")
            return [
                "This channel violates DSA guidelines by spreading harmful content",
                "This channel needs to be reviewed for policy violations",
                "Channel contains inappropriate content that violates community standards"
            ]

    def get_next_token(self) -> str:
        """Get next CSRF token using round-robin"""
        tokens = list(CSRF_TOKENS.values())
        token = tokens[self.current_token_index]
        self.current_token_index = (self.current_token_index + 1) % len(tokens)
        return token

    def prepare_headers(self, csrf_token: str) -> Dict[str, str]:
        """Prepare headers for the request"""
        headers = DEFAULT_HEADERS.copy()
        headers.update({
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'https://telegram.org',
            'Referer': REPORT_URL,
            'X-CSRF-Token': csrf_token,
            'X-CSRFToken': csrf_token,
            'Cookie': f'csrftoken={csrf_token}; sessionid={SESSION_COOKIES["sessionid"]}'
        })
        return headers

    async def report_with_number(self, channel: str, phone_number: str, user_id: int) -> bool:
        """Submit a report using a specific phone number"""
        try:
            if user_id in self.active_tasks and self.active_tasks[user_id].get('cancelled', False):
                logger.info(f"Task cancelled for user {user_id}")
                return False

            # Configure timeout
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            
            async with aiohttp.ClientSession(
                timeout=timeout,
                cookie_jar=aiohttp.CookieJar(),
                trust_env=True
            ) as session:
                # Submit report with retry mechanism
                for attempt in range(2):
                    try:
                        # Get fresh CSRF token for each attempt
                        csrf_token = self.get_next_token()
                        headers = self.prepare_headers(csrf_token)
                        
                        # DSA Report form data
                        country_code = self.phone_manager.get_country_code(phone_number)
                        form_data = {
                            'csrf_token': csrf_token,
                            'csrfmiddlewaretoken': csrf_token,
                            'phone': phone_number.lstrip('+'),
                            'channel': f"@{channel}",  # Add @ prefix
                            'description': random.choice(self.messages),
                            'email': f"{phone_number.lstrip('+').replace('+', '')}@gmail.com",
                            'name': f"User {phone_number[-4:]}",
                            'country': country_code,
                            'language': 'en',
                            'report_type': 'channel_report',
                            'platform': 'telegram',
                            'submit': 'Submit Report',
                            'agree_terms': 'on',
                            'can_contact': 'on'
                        }

                        # First make a GET request to get any dynamic tokens
                        async with session.get(
                            REPORT_URL,
                            headers=headers,
                            ssl=False,
                            timeout=aiohttp.ClientTimeout(total=15)
                        ) as get_response:
                            if get_response.status == 200:
                                # Extract any dynamic tokens if needed
                                page_content = await get_response.text()
                                soup = BeautifulSoup(page_content, 'html.parser')
                                
                                # Update form data with any dynamic fields
                                for hidden_input in soup.find_all('input', type='hidden'):
                                    if hidden_input.get('name') and hidden_input.get('value'):
                                        form_data[hidden_input['name']] = hidden_input['value']

                                # Extract form action URL if different from default
                                form = soup.find('form')
                                if form and form.get('action'):
                                    submit_url = form['action']
                                    if not submit_url.startswith('http'):
                                        submit_url = f"https://telegram.org{submit_url}"
                                else:
                                    submit_url = REPORT_URL

                        # Add delay between GET and POST
                        await asyncio.sleep(random.uniform(2.0, 4.0))

                        # Update headers for POST request
                        post_headers = headers.copy()
                        post_headers.update({
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'Origin': 'https://telegram.org',
                            'Referer': REPORT_URL,
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                            'Sec-Fetch-Site': 'same-origin',
                            'Sec-Fetch-Mode': 'navigate',
                            'Sec-Fetch-User': '?1',
                            'Sec-Fetch-Dest': 'document'
                        })

                        async with session.post(
                            submit_url,
                            headers=post_headers,
                            data=form_data,
                            allow_redirects=True,
                            ssl=False,
                            timeout=aiohttp.ClientTimeout(total=15)
                        ) as response:
                            response_text = await response.text()
                            
                            # Success indicators
                            success = (
                                response.status in [200, 201, 302] or
                                any(indicator in response_text.lower() for indicator in [
                                    'success',
                                    'thank',
                                    'received',
                                    'submitted',
                                    'report has been sent',
                                    'report received',
                                    'we will review'
                                ])
                            )
                            
                            if success:
                                logger.info(f"Successfully reported channel using number {phone_number}")
                                self.phone_manager.mark_number_status(phone_number, True)
                                # Add delay between successful reports
                                await asyncio.sleep(random.uniform(3.0, 5.0))
                                return True
                            
                            # Log the actual response for debugging
                            logger.debug(f"Response status: {response.status}")
                            logger.debug(f"Response headers: {dict(response.headers)}")
                            logger.debug(f"Response text: {response_text[:500]}...")
                            
                            if attempt < 1:
                                await asyncio.sleep(random.uniform(2.0, 4.0))
                                continue
                            
                            logger.error(f"Failed to report channel with number {phone_number}. Status: {response.status}")
                            self.phone_manager.mark_number_status(phone_number, False)
                            return False

                    except asyncio.TimeoutError:
                        if attempt < 1:
                            await asyncio.sleep(2)
                            continue
                        logger.error(f"Timeout with number {phone_number}")
                        self.phone_manager.mark_number_status(phone_number, False)
                        return False
                    
                    except Exception as e:
                        if attempt < 1:
                            await asyncio.sleep(2)
                            continue
                        logger.error(f"Error with number {phone_number}: {str(e)}")
                        self.phone_manager.mark_number_status(phone_number, False)
                        return False

        except Exception as e:
            logger.error(f"Error with number {phone_number}: {str(e)}")
            self.phone_manager.mark_number_status(phone_number, False)
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
        """Send multiple reports using different phone numbers"""
        if not self.phone_manager.phone_numbers:
            return "No phone numbers available! Please add numbers to phone_numbers.txt"

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
                    
                    phone_number = self.phone_manager.get_phone_number()
                    for _ in range(MAX_RETRIES):
                        if await self.report_with_number(channel, phone_number, user_id):
                            return True
                        if self.active_tasks[user_id].get('cancelled', False):
                            raise asyncio.CancelledError()
                        # Get a new number for retry
                        phone_number = self.phone_manager.get_phone_number()
                        # Add delay between retries
                        await asyncio.sleep(random.uniform(1.0, 2.0))
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
                f"🔄 Working Numbers: {len(self.phone_manager.working_numbers)}\n"
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
