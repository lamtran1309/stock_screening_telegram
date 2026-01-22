import os
import json
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from vnstock3 import Vnstock
import time
import schedule
from vnstock import *

# Configuration
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
STATE_FILE = 'stock_state.json'

# List of stocks to screen (Vietnam market)
STOCK_UNIVERSE = listing_companies()['ticker'].tolist()


def calculate_rsi(data, period=14):
    """Calculate RSI indicator"""
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_ema(data, period):
    """Calculate EMA indicator"""
    return data.ewm(span=period, adjust=False).mean()


def get_stock_data(symbol):
    """Fetch stock data and calculate indicators"""
    try:
        stock = Vnstock().stock(symbol=symbol, source='VCI')
        
        # Get historical data (60 days to calculate indicators properly)
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
        
        df = stock.quote.history(start=start_date, end=end_date, interval='1D')
        
        if df is None or len(df) < 50:
            return None
        
        # Calculate indicators
        df['RSI'] = calculate_rsi(df['close'])
        df['EMA20'] = calculate_ema(df['close'], 20)
        df['EMA50'] = calculate_ema(df['close'], 50)
        df['Turnover'] = df['close'] * df['volume']
        
        # Get last 20 days average turnover
        avg_turnover_20 = df['Turnover'].tail(20).mean()
        
        # Get latest values
        latest = df.iloc[-1]
        current_price = latest['close']
        rsi = latest['RSI']
        ema20 = latest['EMA20']
        ema50 = latest['EMA50']
        
        # Calculate percentages
        price_vs_ema20 = ((current_price - ema20) / ema20) * 100
        ema20_vs_ema50 = ((ema20 - ema50) / ema50) * 100
        
        return {
            'symbol': symbol,
            'price': round(current_price, 2),
            'rsi': round(rsi, 2),
            'ema20': round(ema20, 2),
            'ema50': round(ema50, 2),
            'avg_turnover': round(avg_turnover_20, 2),
            'price_vs_ema20': round(price_vs_ema20, 2),
            'ema20_vs_ema50': round(ema20_vs_ema50, 2)
        }
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        return None


def screen_stocks():
    """Screen stocks based on criteria"""
    qualified_stocks = []
    
    print(f"Starting stock screening at {datetime.now()}")
    
    for symbol in STOCK_UNIVERSE:
        print(f"Screening {symbol}...")
        data = get_stock_data(symbol)
        
        if data is None:
            continue
        
        # Apply screening criteria
        if (data['avg_turnover'] > 20_000_000_000 and  # > 20 billion VND
            data['rsi'] > 50 and
            0 <= data['price_vs_ema20'] <= 5 and
            0 <= data['ema20_vs_ema50'] <= 7):
            
            qualified_stocks.append(data)
            print(f"  ✓ {symbol} qualified")
        
        # Small delay to avoid rate limiting
        time.sleep(0.5)
    
    return qualified_stocks


def load_previous_state():
    """Load previous screening state"""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading state: {e}")
    
    return {
        'qualified_stocks': [],
        'last_update': None
    }


def save_state(qualified_stocks):
    """Save current screening state"""
    try:
        state = {
            'qualified_stocks': qualified_stocks,
            'last_update': datetime.now().isoformat()
        }
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Error saving state: {e}")


def send_telegram_message(message):
    """Send message to Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials not set. Message not sent.")
        print(message)
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'HTML'
    }
    
    try:
        response = requests.post(url, json=payload)
        return response.status_code == 200
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        return False


def format_stock_table(stocks):
    """Format stocks as a table for Telegram"""
    if not stocks:
        return "No stocks"
    
    lines = []
    for stock in stocks:
        lines.append(
            f"<b>{stock['symbol']}</b>\n"
            f"  Price: {stock['price']:,.0f} VND\n"
            f"  RSI: {stock['rsi']:.1f}\n"
            f"  P/EMA20: +{stock['price_vs_ema20']:.2f}%\n"
            f"  EMA20/50: +{stock['ema20_vs_ema50']:.2f}%\n"
            f"  Turnover: {stock['avg_turnover']/1_000_000_000:.1f}B VND"
        )
    
    return "\n\n".join(lines)


def compare_and_notify():
    """Compare current screening with previous and send Telegram notification if changed"""
    print("\n" + "="*50)
    print(f"Running screening cycle at {datetime.now()}")
    print("="*50)
    
    # Get current qualified stocks
    current_stocks = screen_stocks()
    current_symbols = set(s['symbol'] for s in current_stocks)
    
    # Load previous state
    previous_state = load_previous_state()
    previous_stocks = previous_state['qualified_stocks']
    previous_symbols = set(s['symbol'] for s in previous_stocks)
    
    # Find changes
    newcomers = [s for s in current_stocks if s['symbol'] not in previous_symbols]
    dropouts = [s for s in previous_stocks if s['symbol'] not in current_symbols]
    
    # Check if there are any changes
    has_changes = len(newcomers) > 0 or len(dropouts) > 0
    
    if has_changes:
        print("\n🔔 CHANGES DETECTED - Sending Telegram notification")
        
        # Build notification message
        message_parts = []
        
        message_parts.append("🇻🇳 <b>Vietnam Stock Screener Update</b>")
        message_parts.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        message_parts.append("")
        
        # Current qualified stocks
        message_parts.append(f"📊 <b>Current Qualified ({len(current_stocks)})</b>")
        message_parts.append(format_stock_table(current_stocks))
        message_parts.append("")
        
        # Newcomers (price went up)
        if newcomers:
            message_parts.append(f"📈 <b>Newcomers ({len(newcomers)})</b>")
            message_parts.append(format_stock_table(newcomers))
            message_parts.append("")
        
        # Dropouts (price went down)
        if dropouts:
            message_parts.append(f"📉 <b>Dropouts ({len(dropouts)})</b>")
            message_parts.append(format_stock_table(dropouts))
        
        message = "\n".join(message_parts)
        
        # Send to Telegram
        send_telegram_message(message)
    else:
        print("\n✓ No changes detected - No notification sent")
    
    # Save current state
    save_state(current_stocks)
    
    print(f"\nCurrent qualified: {len(current_stocks)} stocks")
    print(f"Newcomers: {len(newcomers)}")
    print(f"Dropouts: {len(dropouts)}")


def main():
    """Main function"""
    print("Vietnam Stock Screener - Telegram Bot")
    print("======================================")
    
    # Check environment variables
    if not TELEGRAM_BOT_TOKEN:
        print("⚠️  TELEGRAM_BOT_TOKEN not set in environment variables")
    if not TELEGRAM_CHAT_ID:
        print("⚠️  TELEGRAM_CHAT_ID not set in environment variables")
    
    print("\nTo set up:")
    print("1. Create a bot with @BotFather on Telegram")
    print("2. Get your chat ID from @userinfobot")
    print("3. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Replit Secrets")
    print("")
    
    # Run immediately on startup
    print("Running initial screening...")
    compare_and_notify()
    
    # Schedule to run every 4 hours
    schedule.every(4).hours.do(compare_and_notify)
    
    print("\n✓ Screener is now running!")
    print("📅 Scheduled to run every 4 hours")
    print("⌨️  Press Ctrl+C to stop\n")
    
    # Keep the script running
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute


if __name__ == "__main__":
    main()
