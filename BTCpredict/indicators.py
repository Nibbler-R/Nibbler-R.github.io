# indicators.py
import pandas as pd
import pandas_ta as ta


def add_indicators(df):
    # EMAs
    df["ema9"] = ta.ema(df["close"], length=9)
    df["ema50"] = ta.ema(df["close"], length=50)

    # RSI
    df["rsi"] = ta.rsi(df["close"], length=14)

    # ADX
    adx = ta.adx(df["high"], df["low"], df["close"], length=14)
    if adx is not None and not adx.empty:
        df["adx"] = adx["ADX_14"]
    else:
        df["adx"] = None

    # Bollinger Bands
    bb = ta.bbands(df["close"], length=20, std=2)
    if bb is not None and not bb.empty:
        df["bb_lower"] = bb.iloc[:, 0]
        df["bb_mid"] = bb.iloc[:, 1]
        df["bb_upper"] = bb.iloc[:, 2]

    # MACD
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None and not macd.empty:
        df["macd"] = macd.iloc[:, 0]
        df["macd_signal"] = macd.iloc[:, 1]
        df["macd_hist"] = macd.iloc[:, 2]

    # ---------------------------
    # ✅ 15m Breakout + Expansion Features
    # ---------------------------
    # Candle range & rolling average range
    df["range"] = (df["high"] - df["low"]).astype(float)
    df["range_avg20"] = df["range"].rolling(20).mean()
    df["range_ratio"] = df["range"] / df["range_avg20"]

    # Prior candle levels
    df["prev_high"] = df["high"].shift(1)
    df["prev_low"] = df["low"].shift(1)

    # Breakout flags (close breaks prior candle)
    df["break_up"] = df["close"] > df["prev_high"]
    df["break_down"] = df["close"] < df["prev_low"]

    # ATR as another volatility signal
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    if atr is not None and not atr.empty:
        df["atr14"] = atr
    else:
        df["atr14"] = None

    return df

