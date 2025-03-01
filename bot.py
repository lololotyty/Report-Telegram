import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import aiohttp
import re

# Load environment variables
load_dotenv()

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Regular expression for Telegram channel links
CHANNEL_LINK_PATTERN = r'^(?:https?://)?(?:t\.me|telegram\.me)/(?:joinchat/)?([a-zA-Z0-9_-]+)$'

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(
        f"Hi {user.mention_html()}! 👋\n\n"
        "I'm your friendly Telegram bot. Here are the commands you can use:\n"
        "/start - Show this welcome message\n"
        "/help - Show available commands\n"
        "/echo [message] - Echo back your message\n"
        "/time - Get current time\n"
        "/report [channel_link] [reason] - Report a Telegram channel"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    help_text = (
        "Here are the available commands:\n\n"
        "/start - Start the bot\n"
        "/help - Show this help message\n"
        "/echo [message] - Echo back your message\n"
        "/time - Get current time\n"
        "/report [channel_link] [reason] - Report a Telegram channel\n\n"
        "For reporting, use format:\n"
        "/report https://t.me/channelname your_reason_here"
    )
    await update.message.reply_text(help_text)

async def report_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle channel reporting."""
    if len(context.args) < 2:
        await update.message.reply_text(
            "Please provide both channel link and reason.\n"
            "Usage: /report [channel_link] [reason]\n"
            "Example: /report https://t.me/channelname spam content"
        )
        return

    channel_link = context.args[0]
    reason = ' '.join(context.args[1:])

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
        
        # Prepare report data
        report_data = {
            'channel': channel_username,
            'reason': reason,
            'reporter': update.effective_user.id
        }

        async with aiohttp.ClientSession() as session:
            # Send report to Telegram's report API
            api_url = f"https://api.telegram.org/bot{os.getenv('TELEGRAM_BOT_TOKEN')}/sendMessage"
            params = {
                'chat_id': update.effective_chat.id,
                'text': f"🚨 Report submitted:\nChannel: {channel_username}\nReason: {reason}\n\nThank you for reporting. Our team will review this content."
            }
            
            async with session.get(api_url, params=params) as response:
                if response.status == 200:
                    logger.info(f"Report submitted for channel: {channel_username}")
                else:
                    raise Exception("Failed to submit report")

    except Exception as e:
        logger.error(f"Error reporting channel: {str(e)}")
        await update.message.reply_text(
            "Sorry, there was an error processing your report. Please try again later."
        )
        return

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message."""
    if not context.args:
        await update.message.reply_text("Please provide a message to echo!\nUsage: /echo [message]")
        return
    
    message = ' '.join(context.args)
    await update.message.reply_text(f"You said: {message}")

async def time_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send current time."""
    from datetime import datetime
    current_time = datetime.now().strftime("%H:%M:%S")
    await update.message.reply_text(f"Current time is: {current_time}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all non-command messages."""
    await update.message.reply_text(
        "I can respond to commands! Try /help to see what I can do."
    )

def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("No bot token found! Make sure to set TELEGRAM_BOT_TOKEN in .env file")
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

    # Start the Bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
