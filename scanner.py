import os
import requests
import pandas as pd
import pandas_ta as ta
import numpy as np
import time
from datetime import datetime, time
import pytz
import os
from statsmodels.tsa.stattools import coint
from sklearn.linear_model import LinearRegression

# ===== Configuration =====
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")
GOLDAPI_KEY = os.getenv("GOLDAPI_KEY")

API_CALL_DELAY = 8  # Seconds between API calls
REALTIME_API_URL = "https://api.twelvedata.com/price"
OHLC_API_URL = "https://api.twelvedata.com/time_series"
GOLDAPI_URL = "https://www.goldapi.io/api/XAUUSD"

class EnhancedScanner:
    def __init__(self):
        self.uk_tz = pytz.timezone('Europe/London')
        self.scan_time = datetime.now(self.uk_tz).strftime("%Y-%m-%d %H:%M")
        self.signals = []
        
        # Define strategies with their parameters
        self.strategies = [
            {
                'name': 'Asian Range Breakout',
                'pairs': ['AUD/USD', 'NZD/USD', 'USD/JPY'],
                'timeframe': '15min',
                'active_window': (time(0, 30), time(6, 0)), 
                'active_days': [0, 1, 2, 3, 4],  # Monday(0) to Friday(4)
                'function': self.asian_range_breakout_strategy,
                'data_source': 'twelvedata'
            },
            {
                'name': 'Gold CMAR Strategy',
                'pairs': ['XAU/USD'],
                'timeframe': '1h',
                'active_window': (time(9, 0), time(17, 0)),  # 9am-5pm UK time
                'active_days': [1, 2, 3],  # Tue(1), Wed(2), Thu(3)
                'function': self.gold_strategy,
                'data_source': 'goldapi'
            }
        ]
        
    def get_uk_time(self):
        return datetime.now(self.uk_tz)
    
    def is_strategy_active(self, strategy):
        now = self.get_uk_time()
        current_time = now.time()
        current_day = now.weekday()  # Monday=0, Sunday=6
        
        # Check trading days
        if strategy['active_days'] is not None and current_day not in strategy['active_days']:
            return False
            
        # Check trading hours
        start, end = strategy['active_window']
        return start <= current_time < end
    
    def get_current_price(self, pair, data_source):
        if data_source == 'twelvedata':
            params = {"symbol": pair, "apikey": TWELVEDATA_API_KEY}
            try:
                response = requests.get(REALTIME_API_URL, params=params, timeout=15)
                data = response.json()
                return float(data['price'])
            except:
                return None
                
        elif data_source == 'goldapi':
            headers = {"x-access-token": GOLDAPI_KEY}
            try:
                response = requests.get(GOLDAPI_URL, headers=headers, timeout=15)
                data = response.json()
                return float(data['price'])
            except:
                return None
    
    def fetch_ohlc_data(self, pair, timeframe, data_source):
        if data_source == 'twelvedata':
            params = {
                "symbol": pair,
                "interval": timeframe,
                "outputsize": 100,
                "apikey": TWELVEDATA_API_KEY,
                "timezone": "Europe/London"
            }
            try:
                response = requests.get(OHLC_API_URL, params=params, timeout=20)
                data = response.json()
                
                if 'values' not in data:
                    return None
                    
                df = pd.DataFrame(data['values'])
                df = df.iloc[::-1].reset_index(drop=True)
                
                for col in ['open', 'high', 'low', 'close']:
                    if col in df: 
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                
                if 'datetime' in df:
                    df['datetime'] = pd.to_datetime(df['datetime'])
                    df['datetime'] = df['datetime'].dt.tz_localize('UTC').dt.tz_convert('Europe/London')
                    df['hour'] = df['datetime'].dt.hour
                    df['time'] = df['datetime'].dt.time
                else:
                    now = datetime.now(self.uk_tz)
                    df['datetime'] = pd.date_range(end=now, periods=len(df), freq=timeframe)
                    df['hour'] = df['datetime'].dt.hour
                    df['time'] = df['datetime'].dt.time
                
                df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
                return df.dropna()
            except:
                return None
                
        elif data_source == 'goldapi':
            headers = {"x-access-token": GOLDAPI_KEY}
            end_date = datetime.now(self.uk_tz)
            start_date = end_date - pd.Timedelta(days=90)
            
            params = {
                "start_date": start_date.strftime("%Y-%m-%d"),
                "end_date": end_date.strftime("%Y-%m-%d"),
                "timeframe": timeframe
            }
            
            try:
                response = requests.get(GOLDAPI_URL, headers=headers, params=params, timeout=20)
                data = response.json()
                df = pd.DataFrame(data["data"])
                df["time"] = pd.to_datetime(df["time"])
                df.set_index("time", inplace=True)
                df = df.tz_convert('Europe/London')
                
                # Calculate required indicators
                df["S_3"] = df["close"].rolling(3).mean()
                df["S_9"] = df["close"].rolling(9).mean()
                df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
                
                # Cointegration check
                _, pvalue_3, _ = coint(df["S_3"], df["close"])
                _, pvalue_9, _ = coint(df["S_9"], df["close"])
                df["cointegrated"] = (pvalue_3 < 0.05) & (pvalue_9 < 0.05)
                
                df.reset_index(inplace=True)
                return df.dropna()
            except:
                return None
                
        return None
    
    # ===== Strategy Implementations =====
    def asian_range_breakout_strategy(self, df, current_price):
        """Asian Range Breakout for Forex pairs (00:30-06:00 UK)"""
        if df is None or len(df) < 20: 
            return None
        
        try:
            # Filter Asian session (00:30-06:00 UK)
            start_time = time(0, 30)
            end_time = time(6, 0)
            asian_session = df[(df['time'] >= start_time) & (df['time'] < end_time)]
            
            if len(asian_session) < 4: 
                return None
            
            session_high = asian_session['high'].max()
            session_low = asian_session['low'].min()
            session_open = asian_session.iloc[0]['open']
            latest = df.iloc[-1]
            
            # Calculate buffer (30% of ATR)
            buffer = latest['atr'] * 0.3 if 'atr' in df and pd.notnull(latest['atr']) else 0.001
            
            # Bullish Breakout
            if (current_price > session_high + buffer and
                current_price > session_open and
                latest['close'] > latest['open']):
                return {
                    'direction': 'BUY', 
                    'session_high': session_high, 
                    'session_low': session_low,
                    'pip_value': 10000  # For Forex pairs
                }
            
            # Bearish Breakout
            elif (current_price < session_low - buffer and
                  current_price < session_open and
                  latest['close'] < latest['open']):
                return {
                    'direction': 'SELL', 
                    'session_high': session_high, 
                    'session_low': session_low,
                    'pip_value': 10000
                }
            
            return None
        except Exception as e:
            print(f"Asian breakout error: {str(e)}")
            return None
    
    def gold_strategy(self, df, current_price):
        """Gold CMAR Strategy for XAU/USD"""
        if df is None or len(df) < 50: 
            return None
        
        try:
            # Only use cointegrated periods
            if not df.iloc[-1]['cointegrated']:
                return None
                
            # Prepare features
            features = pd.DataFrame({
                "S_3": [df.iloc[-1]['S_3']],
                "S_9": [df.iloc[-1]['S_9']]
            })
            
            # Train model
            train_df = df[df["cointegrated"]]
            if len(train_df) < 50:
                return None
                
            model = LinearRegression()
            model.fit(train_df[["S_3", "S_9"]], train_df["close"].shift(-1).dropna())
            
            # Predict next close
            pred = model.predict(features)[0]
            
            # Generate signal
            if pred > current_price + (20/10):  # 20 pip target (1 pip = $0.10)
                return {
                    'direction': 'BUY', 
                    'predicted_price': pred,
                    'pip_value': 10  # For Gold (XAU/USD)
                }
            elif pred < current_price - (20/10):
                return {
                    'direction': 'SELL', 
                    'predicted_price': pred,
                    'pip_value': 10
                }
            
            return None
        except Exception as e:
            print(f"Gold strategy error: {str(e)}")
            return None
    
    # ===== Target Calculation & Telegram =====
    def calculate_targets(self, signal, current_price):
        pip_value = signal.get('pip_value', 10000)  # Default to Forex pip value
        direction = signal['direction']
        
        # Base targets
        base_tp = 20 / pip_value
        base_sl = 15 / pip_value
        
        # Volatility adjustment
        if 'atr' in signal:
            volatility = signal['atr'] * pip_value
            if volatility > 15:  # High volatility
                base_tp = 25 / pip_value
            elif volatility < 8:  # Low volatility
                base_tp = 15 / pip_value
        
        if direction == 'BUY':
            tp = current_price + base_tp
            sl = current_price - base_sl
        else:  # SELL
            tp = current_price - base_tp
            sl = current_price + base_sl
            
        return tp, sl, base_tp * pip_value
    
    def send_telegram_message(self, message):
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    'chat_id': TELEGRAM_CHAT_ID,
                    'text': message,
                    'parse_mode': 'Markdown'
                },
                timeout=10
            )
            return True
        except:
            return False
    
    def scan(self):
        # ===== GLOBAL TIME CHECK =====
        now = self.get_uk_time()
        current_day = now.weekday()  # Monday=0, Sunday=6
        current_hour = now.hour
        
        # Skip weekends (Saturday=5, Sunday=6)
        if current_day >= 5:
            print("Skipping scan: Weekend")
            return
            
        # Skip Friday after 18:00 UK time
        if current_day == 4 and current_hour > 18:  # Friday=4
            print("Skipping scan: Friday after 18:00")
            return
        # =============================
            
        print(f"\n🔍 Starting Enhanced Scanner at {self.scan_time}")
        scan_results = []
        signal_count = 0
        
        for strategy in self.strategies:
            strategy_name = strategy['name']
            print(f"\n=== Scanning {strategy_name} ===")
            
            if not self.is_strategy_active(strategy):
                print(f"  Strategy not active at this time")
                continue
                
            for pair in strategy['pairs']:
                time.sleep(API_CALL_DELAY)
                print(f"\nScanning {pair}...")
                pair_status = f"{strategy_name} - {pair}: "
                
                # Get current price
                current_price = self.get_current_price(pair, strategy['data_source'])
                if current_price is None:
                    pair_status += "❌ Price fetch failed"
                    scan_results.append(pair_status)
                    print(pair_status)
                    continue
                else:
                    print(f"  Current price: {current_price:.5f}")
                
                # Get OHLC data
                df = self.fetch_ohlc_data(pair, strategy['timeframe'], strategy['data_source'])
                if df is None or len(df) < 20:
                    pair_status += "❌ Data fetch failed"
                    scan_results.append(pair_status)
                    print(pair_status)
                    continue
                
                # Run strategy
                signal = strategy['function'](df, current_price)
                
                if signal:
                    # Calculate targets
                    tp, sl, pips = self.calculate_targets(signal, current_price)
                    
                    # Prepare signal details
                    signal_details = {
                        'entry': current_price,
                        'tp': tp,
                        'sl': sl,
                        'pips': pips,
                        'strategy': strategy_name,
                        'pair': pair
                    }
                    
                    # Merge signal details
                    full_signal = {**signal, **signal_details}
                    self.signals.append(full_signal)
                    
                    # Format signal message
                    direction_icon = "🟢 BUY" if signal['direction'] == 'BUY' else "🔴 SELL"
                    
                    if strategy_name == 'Gold CMAR Strategy':
                        details = (
                            f"📈 Predicted Price: `{full_signal['predicted_price']:.2f}`\n"
                            f"📊 Current Price: `{current_price:.2f}`"
                        )
                    else:
                        details = (
                            f"📈 Session High: `{full_signal['session_high']:.5f}`\n"
                            f"📉 Session Low: `{full_signal['session_low']:.5f}`"
                        )
                    
                    detailed_msg = (
                        f"🚀 *{strategy_name} SIGNAL* {direction_icon}\n"
                        f"⏰ {self.scan_time} UK\n"
                        f"📊 *{pair}* | {direction_icon}\n"
                        f"📍 Entry: `{current_price:.5f}`\n"
                        f"🎯 TP: `{tp:.5f}` | *{pips:.0f} pips*\n"
                        f"🛑 SL: `{sl:.5f}`\n"
                        f"{details}"
                    )
                    
                    if self.send_telegram_message(detailed_msg):
                        pair_status += f"✅ Signal sent"
                    else:
                        pair_status += f"⚠️ Signal (Telegram failed)"
                    
                    signal_count += 1
                    print(f"  Signal found! {signal['direction']}")
                else:
                    pair_status += "⚪ No signal"
                    print(f"  No signal")
                
                scan_results.append(pair_status)
        
        # Prepare and send summary message
        summary_title = f"✅ {signal_count} SIGNAL{'S' if signal_count != 1 else ''} FOUND" if signal_count else "⚠️ NO SIGNALS"
        
        summary_msg = (
            f"📊 *SCAN SUMMARY*\n"
            f"{summary_title} | {self.scan_time} UK\n"
            f"Total Strategies: {len(self.strategies)}\n\n"
            f"Scan Results:\n" + "\n".join(scan_results)
        )
        
        self.send_telegram_message(summary_msg)
        print(f"\nScan complete! {summary_title}")

# ===== Run Scanner =====
if __name__ == "__main__":
    print("Starting Enhanced Scanner")
    print("=" * 40)
    print("Strategies:")
    print("- Asian Range Breakout (00:30-06:00 UK, Mon-Fri, Forex pairs)")
    print("- Gold CMAR Strategy (9am-5pm UK Tue-Thu, XAU/USD)\n")
    
    scanner = EnhancedScanner()
    scanner.scan()
    
    print("\nScanner finished")
