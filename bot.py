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
from aiohttp_socks import ProxyConnector
import requests
from bs4 import BeautifulSoup

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

# Command handler functions
[... keep existing command handler functions unchanged ...]

# Utility functions
def load_proxies():
    """Load proxies from http_proxies.txt"""
    try:
        with open('http_proxies.txt', 'r') as f:
            proxies = []
            for line in f:
                line = line.strip()
                if line:
                    # Check if proxy includes protocol
                    if not any(line.startswith(p) for p in ['http://', 'socks4://', 'socks5://']):
                        line = 'http://' + line
                    proxies.append(line)
            return proxies
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

def get_csrf_token(session, proxy_url):
    """Get CSRF token from the report page"""
    try:
        response = session.get(
            'https://telegram.org/dsa-report',
            proxy=proxy_url,
            timeout=10,
            verify=False
        )
        soup = BeautifulSoup(response.text, 'html.parser')
        csrf_token = soup.find('input', {'name': 'csrf_token'})
        return csrf_token['value'] if csrf_token else None
    except Exception as e:
        logger.error(f"Error getting CSRF token: {str(e)}")
        return None

# Reporting functions
async def report_with_proxy(session, channel, reason, proxy):
    """Report channel using a specific proxy"""
    try:
        # Create connector based on proxy type
        if proxy.startswith('socks4://'):
            connector = ProxyConnector.from_url(proxy, verify_ssl=False)
        elif proxy.startswith('socks5://'):
            connector = ProxyConnector.from_url(proxy, verify_ssl=False)
        else:
            connector = ProxyConnector.from_url(proxy, verify_ssl=False)

        async with aiohttp.ClientSession(connector=connector) as proxy_session:
            # First get the report page to obtain CSRF token
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Origin': 'https://telegram.org',
                'Referer': 'https://telegram.org/dsa-report'
            }

            try:
                async with proxy_session.get('https://telegram.org/dsa-report', headers=headers, timeout=30) as response:
                    if response.status != 200:
                        return False
                    
                    page_content = await response.text()
                    soup = BeautifulSoup(page_content, 'html.parser')
                    csrf_token = soup.find('input', {'name': 'csrf_token'})
                    if not csrf_token:
                        return False
                    
                    # Prepare form data with CSRF token
                    form_data = {
                        'csrf_token': csrf_token['value'],
                        'channel': channel,
                        'reason': reason,
                        'submit': 'Report Channel'
                    }

                    # Submit report
                    headers['Content-Type'] = 'application/x-www-form-urlencoded'
                    async with proxy_session.post(
                        'https://telegram.org/dsa-report',
                        headers=headers,
                        data=form_data,
                        timeout=30
                    ) as report_response:
                        return report_response.status == 200 and 'report has been sent' in (await report_response.text()).lower()

            except asyncio.TimeoutError:
                logger.error(f"Timeout with proxy {proxy}")
                return False
            except Exception as e:
                logger.error(f"Error with proxy {proxy}: {str(e)}")
                return False

    except Exception as e:
        logger.error(f"Error setting up proxy {proxy}: {str(e)}")
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
    failed_reports = 0
    
    # Create a semaphore to limit concurrent connections
    semaphore = asyncio.Semaphore(10)  # Limit to 10 concurrent connections
    
    async def report_with_semaphore(channel, reason, proxy):
        async with semaphore:
            return await report_with_proxy(None, channel, reason, proxy)
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for _ in range(num_reports):
            proxy = random.choice(proxies)
            reason = random.choice(messages)
            tasks.append(report_with_semaphore(channel_username, reason, proxy))
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, bool) and result:
                successful_reports += 1
            else:
                failed_reports += 1
    
    return (f"Completed reporting process:\n"
            f"✅ Successful reports: {successful_reports}\n"
            f"❌ Failed reports: {failed_reports}\n"
            f"📊 Success rate: {(successful_reports/num_reports)*100:.1f}%")

[... keep existing report_channel and main functions unchanged ...]
