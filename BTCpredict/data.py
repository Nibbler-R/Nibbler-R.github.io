import pandas as pd
import ccxt
import streamlit as st


@st.cache_resource
def _get_exchange():
    # Cache the exchange client so you don't rebuild it every refresh
    return ccxt.binance({"enableRateLimit": True})


@st.cache_data(ttl=3)
def get_btc_data(symbol: str = "BTC/USDT", include_m5: bool = True):
    """
    Returns:
      - df_15m (DataFrame) always
      - df_5m  (DataFrame) if include_m5=True else None

    Notes:
      * Cached for a few seconds to reduce rate limits + UI jitter.
      * Adds a simple 1h trend bias column (h1_trend) onto the 15m df.
    """
    try:
        exchange = _get_exchange()

        # ---------------------------
        # 15m data (main decision frame)
        # ---------------------------
        m15 = exchange.fetch_ohlcv(symbol, timeframe="15m", limit=300)
        if not m15:
            return (None, None) if include_m5 else None

        df15 = pd.DataFrame(m15, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df15["timestamp"] = pd.to_datetime(df15["timestamp"], unit="ms", utc=True)
        df15[["open", "high", "low", "close", "volume"]] = df15[["open", "high", "low", "close", "volume"]].astype(float)
        df15 = df15.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

        # ---------------------------
        # 1h data (bias)
        # ---------------------------
        h1 = exchange.fetch_ohlcv(symbol, timeframe="1h", limit=60)
        if not h1:
            df15["h1_trend"] = "NEUTRAL"
        else:
            df_h1 = pd.DataFrame(h1, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df_h1["close"] = df_h1["close"].astype(float)

            # EMA10 bias (fast)
            h1_ema = df_h1["close"].ewm(span=10, adjust=False).mean()
            current_h1_close = float(df_h1["close"].iloc[-1])
            current_h1_ema = float(h1_ema.iloc[-1])

            if current_h1_close > current_h1_ema:
                df15["h1_trend"] = "BULL"
            elif current_h1_close < current_h1_ema:
                df15["h1_trend"] = "BEAR"
            else:
                df15["h1_trend"] = "NEUTRAL"

        # ---------------------------
        # 5m data (micro confirmation)
        # ---------------------------
        if not include_m5:
            return df15

        m5 = exchange.fetch_ohlcv(symbol, timeframe="5m", limit=300)
        if not m5:
            return df15, None

        df5 = pd.DataFrame(m5, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df5["timestamp"] = pd.to_datetime(df5["timestamp"], unit="ms", utc=True)
        df5[["open", "high", "low", "close", "volume"]] = df5[["open", "high", "low", "close", "volume"]].astype(float)
        df5 = df5.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

        return df15, df5

    except Exception as e:
        print(f"[get_btc_data] Error: {e}")
        return (None, None) if include_m5 else None
