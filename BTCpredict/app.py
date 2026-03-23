import os
import time
import math
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from polymarket_ev import gamma_market_by_slug, get_up_down_prices

from data import get_btc_data
from indicators import add_indicators
from news import get_crypto_news
from strategy import score_market

SIGNALS_CSV = "signals.csv"


# ---------------------------
# 🔔 Voice Alerts (browser TTS)
# ---------------------------
def play_voice_alert(text: str, mute: bool = False):
    if mute:
        return

    safe_text = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
    )

    nonce = str(time.time())
    tts_html = f"""
    <div id="tts-{nonce}"></div>
    <script>
        try {{
            const msg = new SpeechSynthesisUtterance("{safe_text}");
            window.speechSynthesis.cancel();
            window.speechSynthesis.speak(msg);
        }} catch (e) {{
            console.log("TTS error:", e);
        }}
    </script>
    """
    components.html(tts_html, height=0)


def trigger_signal(name: str, voice_text: str, time_str: str, cooldown_s: int = 60):
    now = time.time()
    last_ts = st.session_state.get("last_alert_ts", 0.0)
    last_name = st.session_state.get("last_signal", "None")

    if (name != last_name) or (now - last_ts > cooldown_s):
        play_voice_alert(voice_text, mute=st.session_state.mute_alerts)
        st.session_state.last_signal = name
        st.session_state.last_signal_time = time_str
        st.session_state.last_alert_ts = now


# ---------------------------
# 📒 Logging + Resolution (TRUE Polymarket PnL)
# ---------------------------
BASE_COLS = [
    "id",
    "ts_utc",
    "signal",
    "score",
    "price_entry",
    "ts_target_utc",
    "price_exit",
    "move_pct",
    "win",
    "resolved",
]

EV_COLS = [
    "pm_slug",
    "pm_up_ask",
    "pm_down_ask",
    "p_up",
    "p_down",
    "ev_edge",
    "minutes_remaining",
    "zone",
]

PM_PNL_COLS = [
    "stake_usd",          # intended bet size in $
    "pm_entry",           # ask used for chosen side
    "pm_ev_per_share",    # p - ask (model EV per share)
    "pm_pnl_per_share",   # realized PnL per share
    "pm_roi",             # realized / ask
    "pm_pnl_usd",         # dollars, from stake_usd
]

ALL_COLS = BASE_COLS + EV_COLS + PM_PNL_COLS


def _ensure_signals_file():
    if not os.path.exists(SIGNALS_CSV):
        pd.DataFrame(columns=ALL_COLS).to_csv(SIGNALS_CSV, index=False)


def _ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Keep older signals.csv compatible by adding missing columns."""
    for c in ALL_COLS:
        if c not in df.columns:
            df[c] = ""
    if "resolved" not in df.columns:
        df["resolved"] = False
    return df


def log_signal(
    ts: datetime,
    signal: str,
    score: float,
    entry_price: float,
    pm_slug: str = "",
    pm_up_ask: float | None = None,
    pm_down_ask: float | None = None,
    p_up: float | None = None,
    p_down: float | None = None,
    ev_edge: float | None = None,
    minutes_remaining: float | None = None,
    zone: str = "",
    stake_usd: float | None = None,
):
    _ensure_signals_file()
    df = pd.read_csv(SIGNALS_CSV)
    df = _ensure_columns(df)

    target_ts = ts + timedelta(minutes=15)
    row_id = f"{ts.isoformat()}_{signal}"

    # Prevent duplicates (fragment reruns)
    if "id" in df.columns and (df["id"] == row_id).any():
        return

    new_row = {
        # base
        "id": row_id,
        "ts_utc": ts.isoformat(),
        "signal": signal,
        "score": float(score),
        "price_entry": float(entry_price),
        "ts_target_utc": target_ts.isoformat(),
        "price_exit": "",
        "move_pct": "",
        "win": "",
        "resolved": False,

        # EV logging
        "pm_slug": str(pm_slug or ""),
        "pm_up_ask": "" if pm_up_ask is None else float(pm_up_ask),
        "pm_down_ask": "" if pm_down_ask is None else float(pm_down_ask),
        "p_up": "" if p_up is None else float(p_up),
        "p_down": "" if p_down is None else float(p_down),
        "ev_edge": "" if ev_edge is None else float(ev_edge),
        "minutes_remaining": "" if minutes_remaining is None else float(minutes_remaining),
        "zone": str(zone or ""),

        # PM PnL fields (filled later on resolve)
        "stake_usd": "" if stake_usd is None else float(stake_usd),
        "pm_entry": "",
        "pm_ev_per_share": "",
        "pm_pnl_per_share": "",
        "pm_roi": "",
        "pm_pnl_usd": "",
    }

    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    df.to_csv(SIGNALS_CSV, index=False)


def resolve_signals_with_df(df_price: pd.DataFrame):
    """
    Resolves older logged signals once the +15m target candle is present.
    Also computes TRUE Polymarket PnL per share using logged ask prices.
    """
    _ensure_signals_file()
    sig = pd.read_csv(SIGNALS_CSV)
    sig = _ensure_columns(sig)

    if sig.empty:
        return sig

    price = df_price.copy()
    price["timestamp"] = pd.to_datetime(price["timestamp"], utc=True)

    # safe resolved boolean
    sig["resolved"] = sig["resolved"].astype(bool)
    unresolved = sig[~sig["resolved"]].copy()
    if unresolved.empty:
        return sig

    unresolved["ts_target_utc"] = pd.to_datetime(unresolved["ts_target_utc"], utc=True)

    price_sorted = price.sort_values("timestamp")[["timestamp", "close"]].rename(columns={"close": "close_at"})
    unresolved_sorted = unresolved.sort_values("ts_target_utc")[["id", "ts_target_utc"]].rename(
        columns={"ts_target_utc": "timestamp"}
    )

    merged = pd.merge_asof(
        unresolved_sorted,
        price_sorted,
        on="timestamp",
        direction="forward",
        tolerance=pd.Timedelta(minutes=15),
    )

    for _, m in merged.iterrows():
        if pd.isna(m["close_at"]):
            continue

        row_id = m["id"]
        exit_price = float(m["close_at"])

        idxs = sig.index[sig["id"] == row_id]
        if len(idxs) == 0:
            continue
        idx = idxs[0]

        entry = float(sig.loc[idx, "price_entry"])
        signal = str(sig.loc[idx, "signal"])

        move_pct = (exit_price - entry) / entry * 100.0

        if signal == "BUY_UP":
            win = exit_price > entry
        elif signal == "BUY_DOWN":
            win = exit_price < entry
            move_pct = -move_pct  # normalize so positive means good
        else:
            win = ""

        sig.loc[idx, "price_exit"] = exit_price
        sig.loc[idx, "move_pct"] = round(float(move_pct), 4)
        sig.loc[idx, "win"] = bool(win) if win != "" else ""
        sig.loc[idx, "resolved"] = True

        # --------- TRUE Polymarket PnL ----------
        try:
            pm_up_ask = pd.to_numeric(sig.loc[idx, "pm_up_ask"], errors="coerce")
            pm_down_ask = pd.to_numeric(sig.loc[idx, "pm_down_ask"], errors="coerce")
            p_up_log = pd.to_numeric(sig.loc[idx, "p_up"], errors="coerce")
            p_down_log = pd.to_numeric(sig.loc[idx, "p_down"], errors="coerce")
            stake_usd = pd.to_numeric(sig.loc[idx, "stake_usd"], errors="coerce")

            if signal == "BUY_UP":
                pm_entry = float(pm_up_ask) if pd.notna(pm_up_ask) else None
                p_model = float(p_up_log) if pd.notna(p_up_log) else None
            elif signal == "BUY_DOWN":
                pm_entry = float(pm_down_ask) if pd.notna(pm_down_ask) else None
                p_model = float(p_down_log) if pd.notna(p_down_log) else None
            else:
                pm_entry = None
                p_model = None

            if pm_entry is not None and pm_entry > 0:
                realized = (1.0 - pm_entry) if bool(win) else (-pm_entry)
                roi = realized / pm_entry

                sig.loc[idx, "pm_entry"] = float(pm_entry)
                sig.loc[idx, "pm_pnl_per_share"] = float(realized)
                sig.loc[idx, "pm_roi"] = float(roi)

                if p_model is not None:
                    sig.loc[idx, "pm_ev_per_share"] = float(p_model - pm_entry)

                if pd.notna(stake_usd) and float(stake_usd) > 0:
                    shares = float(stake_usd) / pm_entry
                    sig.loc[idx, "pm_pnl_usd"] = float(shares * realized)
        except Exception:
            pass

    sig.to_csv(SIGNALS_CSV, index=False)
    return sig


def compute_stats(sig: pd.DataFrame):
    if sig is None or sig.empty:
        return None

    s = sig.copy()
    s = _ensure_columns(s)

    s["resolved"] = s["resolved"].astype(bool)
    s = s[s["resolved"]].copy()
    if s.empty:
        return None

    # Direction stats (BTC close vs open)
    s["move_pct"] = pd.to_numeric(s["move_pct"], errors="coerce")
    s = s.dropna(subset=["move_pct"])

    total = len(s)
    wins = int(s["win"].astype(bool).sum())
    win_rate = wins / total * 100.0 if total else 0.0
    avg_move = float(s["move_pct"].mean())
    med_move = float(s["move_pct"].median())

    s["score"] = pd.to_numeric(s["score"], errors="coerce")
    bins = [-999, -5, -3, -2, 2, 3, 5, 999]
    labels = ["<=-5", "-5..-3", "-3..-2", "-2..2", "2..3", "3..5", ">=5"]
    s["bucket"] = pd.cut(s["score"], bins=bins, labels=labels)

    by_bucket = (
        s.groupby("bucket", dropna=False)
        .agg(
            trades=("id", "count"),
            win_rate=("win", lambda x: float(x.astype(bool).mean() * 100.0) if len(x) else 0.0),
            avg_move=("move_pct", "mean"),
        )
        .reset_index()
    )

    # True Polymarket PnL stats
    s["pm_ev_per_share"] = pd.to_numeric(s["pm_ev_per_share"], errors="coerce")
    s["pm_pnl_per_share"] = pd.to_numeric(s["pm_pnl_per_share"], errors="coerce")
    s["pm_roi"] = pd.to_numeric(s["pm_roi"], errors="coerce")
    s["pm_pnl_usd"] = pd.to_numeric(s["pm_pnl_usd"], errors="coerce")

    pm_trades = int(s["pm_pnl_per_share"].notna().sum())
    avg_ev = float(s["pm_ev_per_share"].dropna().mean()) if pm_trades else None
    avg_pnl = float(s["pm_pnl_per_share"].dropna().mean()) if pm_trades else None
    avg_roi = float(s["pm_roi"].dropna().mean()) if pm_trades else None
    total_pnl_usd = float(s["pm_pnl_usd"].dropna().sum()) if s["pm_pnl_usd"].notna().any() else None

    return {
        "total": total,
        "wins": wins,
        "win_rate": win_rate,
        "avg_move": avg_move,
        "med_move": med_move,
        "by_bucket": by_bucket,

        "pm_trades": pm_trades,
        "avg_ev_per_share": avg_ev,
        "avg_pnl_per_share": avg_pnl,
        "avg_roi": avg_roi,
        "total_pnl_usd": total_pnl_usd,
    }


# ---------------------------
# 🎯 Probability helpers
# ---------------------------
def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def estimate_sigma_for_minutes(df_15m: pd.DataFrame, minutes_remaining: float, fallback_to_atr: bool = True) -> float:
    m = max(0.5, float(minutes_remaining))
    frac = m / 15.0

    if df_15m is None or len(df_15m) < 30:
        return 0.0

    last_price = float(df_15m["close"].iloc[-1])

    rets = df_15m["close"].pct_change()
    rstd = rets.rolling(30).std().iloc[-1]
    if pd.notna(rstd) and float(rstd) > 0:
        sigma_15_dollars = last_price * float(rstd)
        sigma_m = sigma_15_dollars * math.sqrt(frac)
        return max(0.0, float(sigma_m))

    if not fallback_to_atr:
        return 0.0

    sigma_15 = None
    if "atr14" in df_15m.columns and pd.notna(df_15m["atr14"].iloc[-1]):
        sigma_15 = float(df_15m["atr14"].iloc[-1])
    elif "range_avg20" in df_15m.columns and pd.notna(df_15m["range_avg20"].iloc[-1]):
        sigma_15 = float(df_15m["range_avg20"].iloc[-1])
    elif "range" in df_15m.columns:
        v = df_15m["range"].rolling(20).mean().iloc[-1]
        if pd.notna(v):
            sigma_15 = float(v)

    if not sigma_15 or sigma_15 <= 0:
        return 0.0

    sigma_15_std = sigma_15 * 0.7
    sigma_m = sigma_15_std * math.sqrt(frac)
    return max(0.0, float(sigma_m))


def polymarket_probs(current_price: float, target_price: float, sigma_m: float, base_score: float):
    if sigma_m <= 0:
        return (1.0, 0.0) if current_price >= target_price else (0.0, 1.0)

    s = max(-8.0, min(8.0, float(base_score)))
    drift = (s / 8.0) * (0.35 * sigma_m)

    z = (target_price - (current_price + drift)) / sigma_m
    p_up = 1.0 - norm_cdf(z)
    p_down = 1.0 - p_up
    return p_up, p_down


def position_multiplier(score: float, edge: float) -> float:
    s = abs(float(score))
    e = max(0.0, float(edge))

    mult = 1.0
    if s >= 6:
        mult *= 1.7
    elif s >= 4:
        mult *= 1.3

    if e >= 0.14:
        mult *= 1.25
    elif e >= 0.10:
        mult *= 1.10

    return min(2.0, mult)


def parse_price(s: str) -> float:
    if s is None:
        return 0.0
    s = str(s).strip().replace(" ", "")
    if not s:
        return 0.0

    if "," in s and "." in s:
        s2 = s.replace(",", "")
        try:
            return float(s2)
        except ValueError:
            return 0.0

    parts = s.split(",")
    if len(parts) > 1:
        if all(len(p) == 3 for p in parts[1:]):
            s2 = s.replace(",", "")
            try:
                return float(s2)
            except ValueError:
                return 0.0
        else:
            s2 = s.replace(",", ".")
            try:
                return float(s2)
            except ValueError:
                return 0.0

    try:
        return float(s)
    except ValueError:
        return 0.0


# ============================================================
# ✅ RULE ENGINE helpers (defined ONCE at module level)
# ============================================================
def _sgn(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def passes_volatility_filters(df15_: pd.DataFrame, df5_: pd.DataFrame | None) -> tuple[bool, str]:
    # ATR spike filter (15m)
    if df15_ is not None and len(df15_) >= 30 and "atr14" in df15_.columns:
        atr = pd.to_numeric(df15_["atr14"], errors="coerce")
        if atr.notna().sum() >= 20:
            atr_now = float(atr.iloc[-1])
            atr_med = float(atr.iloc[-21:-1].median())
            if atr_med > 0 and (atr_now / atr_med) >= 1.8:
                return (False, f"ATR spike ({atr_now/atr_med:.2f}x)")

    # 5m impulse filter
    if df5_ is not None and not df5_.empty:
        try:
            hi = float(df5_.iloc[-1]["high"])
            lo = float(df5_.iloc[-1]["low"])
            rng = abs(hi - lo)
            if rng >= 120:  # tune 80–150
                return (False, f"5m impulse too large (${rng:.0f})")
        except Exception:
            pass

    # Volume spike filter (optional)
    if df15_ is not None and len(df15_) >= 30 and "volume" in df15_.columns:
        v = pd.to_numeric(df15_["volume"], errors="coerce")
        if v.notna().sum() >= 20:
            v_now = float(v.iloc[-1])
            v_med = float(v.iloc[-21:-1].median())
            if v_med > 0 and (v_now / v_med) >= 2.5:
                return (False, f"Volume spike ({v_now/v_med:.2f}x)")

    return (True, "")


def apply_profit_rules(
    minutes_remaining_: float,
    dist_: float,
    base_score_: float,
    edge_up_: float,
    edge_down_: float,
    trade_allowed_: bool,
    df15_: pd.DataFrame,
    df5_: pd.DataFrame | None,
    min_edge_: float,
) -> tuple[bool, str]:
    # 1) Only mid-candle
    if not (6.0 <= float(minutes_remaining_) <= 10.0):
        return (False, "Time window block (need 6–10m left)")

    # 2) Anti-chase
    if abs(float(dist_)) > 100:
        return (False, f"Anti-chase block (|dist|={abs(dist_):.0f} > 100)")

    # 3) Strong score only
    if abs(float(base_score_)) < 5.0:
        return (False, "Weak score (<5)")

    # 4) Must pass your existing filters
    if not bool(trade_allowed_):
        return (False, "Strategy filters blocked")

    # 5) Vol filters
    ok, why = passes_volatility_filters(df15_, df5_)
    if not ok:
        return (False, f"Volatility block: {why}")

    # 6) Alignment: sign(score) == sign(dist)
    if _sgn(base_score_) == 0 or _sgn(dist_) == 0:
        return (False, "Alignment block (flat sign)")
    if _sgn(base_score_) != _sgn(dist_):
        return (False, "Alignment block (score vs dist mismatch)")

    # 7) Fat EV only (use baseline min_edge as the gate)
    HARD_MIN_EDGE = max(float(min_edge_), 0.06)
    if max(float(edge_up_), float(edge_down_)) < HARD_MIN_EDGE:
        return (False, f"Edge too small (<{HARD_MIN_EDGE:.2f})")

    # 8) Direction lock (dist chooses direction)
    if dist_ > 0 and float(edge_up_) < HARD_MIN_EDGE:
        return (False, "Direction wants UP, but UP edge insufficient")
    if dist_ < 0 and float(edge_down_) < HARD_MIN_EDGE:
        return (False, "Direction wants DOWN, but DOWN edge insufficient")

    return (True, "OK")


# ---------------------------
# ⚙️ App Configuration
# ---------------------------
st.set_page_config(page_title="Diddy x BTC Decision Tool", page_icon="📈", layout="wide")

st.session_state.setdefault("last_signal", "None")
st.session_state.setdefault("last_signal_time", "--:--")
st.session_state.setdefault("last_alert_ts", 0.0)
st.session_state.setdefault("mute_alerts", False)

st.session_state.setdefault("pending_signal", "NO_TRADE")
st.session_state.setdefault("pending_count", 0)
st.session_state.setdefault("pending_candle_ts", None)

st.session_state.setdefault("last_logged_id", "")

st.session_state.setdefault("pm_use_override", False)
st.session_state.setdefault("pm_override_target", "")
st.session_state.setdefault("pm_override_current", "")

# ✅ baseline min edge set to 0.06
st.session_state.setdefault("ev_min_edge", 0.06)
st.session_state.setdefault("pm_market_slug", "")


# ---------------------------
# 📊 Sidebar
# ---------------------------
with st.sidebar:
    st.header("🔔 Alert Settings")
    st.session_state.mute_alerts = st.checkbox("Mute Voice Alerts", value=st.session_state.mute_alerts)
    if st.button("🔊 Test Voice Alert"):
        play_voice_alert("Voice alerts are enabled.", mute=st.session_state.mute_alerts)

    st.divider()

    st.header("🎯 Polymarket Inputs (AUTO)")
    st.caption("• Price to beat = current 15m candle OPEN")
    st.caption("• Current price = current 15m candle LAST/CLOSE")

    st.session_state.ev_min_edge = st.slider(
        "Min EV edge (p - price)",
        0.00,
        0.20,
        float(st.session_state.ev_min_edge),
        0.01,
        help="Require at least this much edge vs Polymarket ask price. 0.06 = 6% edge baseline.",
    )

    st.session_state.pm_use_override = st.checkbox(
        "Override with pasted Polymarket prices (optional)",
        value=st.session_state.pm_use_override,
    )

    if st.session_state.pm_use_override:
        st.session_state.pm_override_target = st.text_input(
            "Paste Polymarket Price to beat ($)",
            value=st.session_state.pm_override_target,
        )
        st.session_state.pm_override_current = st.text_input(
            "Paste Polymarket Current price ($) (optional)",
            value=st.session_state.pm_override_current,
        )

    st.divider()

    st.header("🔗 Market slug / URL (required for EV)")
    st.session_state.pm_market_slug = st.text_input(
        "Paste Polymarket market URL or slug",
        value=st.session_state.pm_market_slug,
        placeholder="btc-updown-15m-1771318800 or https://polymarket.com/event/...",
        help="Paste the correct market each new 15m market. EV will use THIS exact market.",
    )

    st.divider()

    st.header("💰 Risk Management")
    balance = st.number_input("Polymarket Balance ($)", min_value=0.0, value=7.64, step=1.0)
    risk_pct = st.slider("Risk per Trade (%)", 1.0, 20.0, 5.0)
    st.caption(f"Base position size: ${(balance * (risk_pct / 100)):.2f}")

    st.divider()

    st.header("🕵️ Signal Log")
    st.info(f"Last Alert: **{st.session_state.last_signal}**")
    st.caption(f"Time: {st.session_state.last_signal_time}")

    st.divider()

    st.header("📰 Global Crypto News")
    try:
        news_items = get_crypto_news(limit=4)
        if news_items:
            for art in news_items:
                st.markdown(f"**{art['title']}**")
                st.caption(f"[Source]({art['url']})")
        else:
            st.write("No recent news found.")
    except Exception:
        st.write("Could not load news.")


# ---------------------------
# 🧠 Main Header
# ---------------------------
st.title("Diddy x Epstein BTC Dashboard")
st.caption("Optimized for Short-Term Polymarket Directional Trades")


# ---------------------------
# 🔄 Live Dashboard
# ---------------------------
@st.fragment(run_every=2)
def live_dashboard():
    df15, df5 = get_btc_data(include_m5=True)
    if df15 is None or df15.empty:
        st.info("🔄 Waiting for exchange data...")
        return

    df15 = add_indicators(df15)
    if df5 is not None and not df5.empty:
        df5 = add_indicators(df5)

    # Resolve + stats
    sig_df = resolve_signals_with_df(df15)
    stats = compute_stats(sig_df)

    now = datetime.now(timezone.utc)
    seconds_passed = (now.minute % 15) * 60 + now.second
    seconds_left = 900 - seconds_passed
    minutes_remaining = seconds_left / 60.0
    time_str = now.strftime("%H:%M:%S")

    base_score, evidence, base_signal, trade_allowed = score_market(
        df15, minutes_remaining=minutes_remaining, df_m5=df5
    )

    # Exchange prices
    last_price = float(df15.iloc[-1]["close"])
    candle_open = float(df15.iloc[-1]["open"])

    # AUTO mapping for Polymarket direction market
    auto_price_to_beat = candle_open
    auto_current = last_price

    # Optional override with pasted Polymarket prices
    if st.session_state.pm_use_override:
        target_override = parse_price(st.session_state.pm_override_target)
        current_override = parse_price(st.session_state.pm_override_current)
        price_to_beat = target_override if target_override > 0 else auto_price_to_beat
        pm_current = current_override if current_override > 0 else auto_current
    else:
        price_to_beat = auto_price_to_beat
        pm_current = auto_current

    # Phase zones
    if seconds_passed < 180:
        zone = "⚠️ NOISE ZONE"
    elif 420 <= seconds_passed <= 600:
        zone = "🎯 SNIPER ZONE"
    elif seconds_passed > 780:
        zone = "🛑 EXHAUSTION"
    else:
        zone = "📊 TREND DISCOVERY"

    # Model probabilities
    sigma_m = estimate_sigma_for_minutes(df15, minutes_remaining)
    p_up, p_down = polymarket_probs(pm_current, price_to_beat, sigma_m, base_score)
    dist = pm_current - price_to_beat

    # ---------------------------
    # 🛑 Distance filter (anti-chase)
    # ---------------------------
    MAX_DIST = 130  # try 120–150
    distance_block = abs(dist) >= MAX_DIST
    if distance_block:
        st.warning(f"Distance too large ({dist:+.0f}). Anti-chase filter → NO TRADE.")

    # ---------------------------
    # ✅ EV AUTOMATION (fast + cached + transparent)
    # ---------------------------
    pm_signal = "NO_TRADE"
    best_edge = 0.0
    up_px = down_px = None
    pm_dbg = {}
    prices = None

    min_edge = float(st.session_state.ev_min_edge)
    slug_in = str(st.session_state.get("pm_market_slug", "")).strip()

    # Cache Polymarket odds for 8 seconds
    cache_ttl = 8.0
    now_ts = time.time()
    cache = st.session_state.get("pm_odds_cache", {})
    cached_ok = (
        bool(cache)
        and (now_ts - float(cache.get("ts", 0))) < cache_ttl
        and cache.get("slug_in", "") == slug_in
    )

    if cached_ok:
        prices = cache.get("prices")
    else:
        if slug_in:
            try:
                pm_market = gamma_market_by_slug(slug_in)
                prices = get_up_down_prices(pm_market) if pm_market else None
            except Exception:
                prices = None
        st.session_state["pm_odds_cache"] = {"ts": now_ts, "prices": prices, "slug_in": slug_in}

    with st.expander("🧾 EV (automatic Polymarket odds)", expanded=True):
        st.write("trade_allowed:", trade_allowed)
        st.write("min_edge:", min_edge)
        st.write("slug provided:", bool(slug_in))
        st.write("distance_block:", distance_block)

    edge_up = edge_down = None
    if prices:
        up_px, down_px, pm_dbg = prices
        edge_up = float(p_up) - float(up_px)
        edge_down = float(p_down) - float(down_px)

        if (edge_up >= min_edge) or (edge_down >= min_edge):
            if edge_up > edge_down:
                pm_signal = "BUY_UP"
                best_edge = float(edge_up)
            else:
                pm_signal = "BUY_DOWN"
                best_edge = float(edge_down)
        else:
            pm_signal = "NO_TRADE"
            best_edge = 0.0

        with st.expander("🧾 EV details", expanded=False):
            st.write(f"**Market slug:** {pm_dbg.get('slug')}")
            st.write(f"**Up ask:** {up_px:.3f} | **Down ask:** {down_px:.3f}")
            st.write(f"**P(Up):** {p_up:.3f} | **P(Down):** {p_down:.3f}")
            st.write(f"**Edge Up:** {edge_up:+.3f}")
            st.write(f"**Edge Down:** {edge_down:+.3f}")
            st.write(f"**Decision (pre-rules):** {pm_signal}")
    else:
        if not slug_in:
            st.warning("EV disabled: paste the Polymarket market URL/slug in the sidebar.")
        else:
            st.warning("Could not fetch Polymarket odds. EV disabled → NO TRADE.")
        pm_signal = "NO_TRADE"
        best_edge = 0.0

    # Hard block after EV selection
    if distance_block:
        pm_signal = "NO_TRADE"
        best_edge = 0.0

    # ============================================================
    # ✅ RULE ENGINE (profit-focused)
    # Runs AFTER EV selection so it can veto weak setups
    # ============================================================
    rule_reason = "N/A"
    rule_block = False

    if prices and (pm_signal in ("BUY_UP", "BUY_DOWN")) and (edge_up is not None) and (edge_down is not None):
        ok, rule_reason = apply_profit_rules(
            minutes_remaining_=minutes_remaining,
            dist_=dist,
            base_score_=base_score,
            edge_up_=edge_up,
            edge_down_=edge_down,
            trade_allowed_=trade_allowed,
            df15_=df15,
            df5_=df5,
            min_edge_=min_edge,
        )
        if not ok:
            rule_block = True
            pm_signal = "NO_TRADE"
            best_edge = 0.0

        with st.expander("🧠 Rule Engine (why)", expanded=False):
            st.write("rule_reason:", rule_reason)
            st.write("rule_block:", rule_block)
            st.write("dist:", float(dist))
            st.write("minutes_remaining:", float(minutes_remaining))
            st.write("base_score:", float(base_score))
            st.write("edge_up:", float(edge_up))
            st.write("edge_down:", float(edge_down))

    # ---------------------------
    # 🧨 Late-window stricter rules (last 5 minutes)
    # ---------------------------
    if minutes_remaining <= 5:
        strong_edge = (best_edge >= (min_edge + 0.04))  # stricter late
        require_double_confirm = True
    else:
        strong_edge = (best_edge >= (min_edge + 0.03))
        require_double_confirm = False

    # ---------------------------
    # ✅ Adaptive confirmation (faster alerts)
    # ---------------------------
    current_candle_ts = pd.to_datetime(df15.iloc[-1]["timestamp"], utc=True)
    if st.session_state.pending_candle_ts != current_candle_ts:
        st.session_state.pending_candle_ts = current_candle_ts
        st.session_state.pending_signal = "NO_TRADE"
        st.session_state.pending_count = 0

    if pm_signal == "NO_TRADE":
        st.session_state.pending_signal = "NO_TRADE"
        st.session_state.pending_count = 0
        confirmed = False
    else:
        if pm_signal == st.session_state.pending_signal:
            st.session_state.pending_count += 1
        else:
            st.session_state.pending_signal = pm_signal
            st.session_state.pending_count = 1

        if require_double_confirm:
            confirmed = (st.session_state.pending_count >= 2)
        else:
            confirmed = True if strong_edge else (st.session_state.pending_count >= 2)

    ev_only = (pm_signal != "NO_TRADE" and not trade_allowed)

    # ---------------------------
    # Metrics + suggested bet sizing
    # ---------------------------
    base_pos = float(balance) * (float(risk_pct) / 100.0)
    mult = position_multiplier(base_score, best_edge)
    suggested_pos = base_pos * mult

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Live Price", f"${last_price:,.2f}")
    c2.metric("Strategy Score", f"{base_score:.2f}")
    c3.metric("Phase", zone)
    c4.metric("Candle Timer", f"{seconds_left//60:02d}:{seconds_left%60:02d}")
    c5.metric("Suggested Bet", f"${suggested_pos:.2f}", help="Scaled by score strength + EV edge (capped at 2x).")

    with st.expander("🎯 Polymarket Target Math (AUTO)", expanded=True):
        st.write(f"**Price to beat (AUTO = 15m open):** ${auto_price_to_beat:,.2f}")
        st.write(f"**Current price (AUTO = last):** ${auto_current:,.2f}")

        if st.session_state.pm_use_override:
            st.divider()
            st.write(f"**Override target (parsed):** ${parse_price(st.session_state.pm_override_target):,.2f}")
            v = parse_price(st.session_state.pm_override_current)
            st.write(f"**Override current (parsed):** ${v:,.2f}" if v > 0 else "**Override current:** (blank → using AUTO)")

        st.divider()
        st.write(f"**Using target:** ${price_to_beat:,.2f}")
        st.write(f"**Using current:** ${pm_current:,.2f}")
        st.write(f"**Distance to target:** `{dist:+.2f}` dollars")
        st.write(f"**Time remaining:** `{minutes_remaining:.2f}` minutes")
        st.write(f"**Estimated σ over remaining time:** `{sigma_m:.2f}` dollars")
        st.write(f"**P(UP)** ≈ `{p_up*100:.1f}%`")
        st.write(f"**P(DOWN)** ≈ `{p_down*100:.1f}%`")

    with st.expander("📒 Performance (signals.csv)", expanded=False):
        if stats is None:
            st.info("No resolved trades yet.")
        else:
            st.subheader("Direction stats (BTC close vs open)")
            st.metric("Trades", stats["total"])
            st.metric("Win rate", f"{stats['win_rate']:.1f}%")
            st.metric("Avg move (normalized)", f"{stats['avg_move']:.3f}%")
            st.metric("Median move (normalized)", f"{stats['med_move']:.3f}%")
            st.divider()
            st.dataframe(stats["by_bucket"], use_container_width=True)

            st.divider()
            st.subheader("Polymarket stats (TRUE PnL)")
            if stats.get("pm_trades", 0) == 0:
                st.info("No PM PnL computed yet (new trades will start filling pm_* columns).")
            else:
                st.metric("PM trades (with asks logged)", stats["pm_trades"])
                if stats["avg_ev_per_share"] is not None:
                    st.metric("Avg EV per share (p-ask)", f"{stats['avg_ev_per_share']:+.4f}")
                if stats["avg_pnl_per_share"] is not None:
                    st.metric("Avg realized PnL per share", f"{stats['avg_pnl_per_share']:+.4f}")
                if stats["avg_roi"] is not None:
                    st.metric("Avg realized ROI", f"{stats['avg_roi']*100:+.2f}%")
                if stats["total_pnl_usd"] is not None:
                    st.metric("Total PnL ($, using Suggested Bet)", f"{stats['total_pnl_usd']:+.2f}")

    # ---------------------------
    # Banner + Alerts + Logging
    # ---------------------------
    entry_ts = now.replace(microsecond=0)

    if pm_signal == "BUY_UP":
        msg = f"🔥 EV BUY UP — Edge {best_edge:+.3f} " + ("(EV ONLY: filters blocked)" if ev_only else "")
        st.success(msg + (" ✅ CONFIRMED" if confirmed else " …confirming"))

        if confirmed:
            trigger_signal("PM_BUY_UP", "Polymarket EV buy up.", time_str)
            row_id = f"{entry_ts.isoformat()}_PM_BUY_UP"
            if st.session_state.last_logged_id != row_id:
                log_signal(
                    entry_ts,
                    "BUY_UP",
                    base_score,
                    last_price,
                    pm_slug=(pm_dbg.get("slug") if isinstance(pm_dbg, dict) else "") or slug_in,
                    pm_up_ask=up_px,
                    pm_down_ask=down_px,
                    p_up=p_up,
                    p_down=p_down,
                    ev_edge=best_edge,
                    minutes_remaining=minutes_remaining,
                    zone=zone,
                    stake_usd=suggested_pos,
                )
                st.session_state.last_logged_id = row_id

    elif pm_signal == "BUY_DOWN":
        msg = f"💀 EV BUY DOWN — Edge {best_edge:+.3f} " + ("(EV ONLY: filters blocked)" if ev_only else "")
        st.error(msg + (" ✅ CONFIRMED" if confirmed else " …confirming"))

        if confirmed:
            trigger_signal("PM_BUY_DOWN", "Polymarket EV buy down.", time_str)
            row_id = f"{entry_ts.isoformat()}_PM_BUY_DOWN"
            if st.session_state.last_logged_id != row_id:
                log_signal(
                    entry_ts,
                    "BUY_DOWN",
                    base_score,
                    last_price,
                    pm_slug=(pm_dbg.get("slug") if isinstance(pm_dbg, dict) else "") or slug_in,
                    pm_up_ask=up_px,
                    pm_down_ask=down_px,
                    p_up=p_up,
                    p_down=p_down,
                    ev_edge=best_edge,
                    minutes_remaining=minutes_remaining,
                    zone=zone,
                    stake_usd=suggested_pos,
                )
                st.session_state.last_logged_id = row_id

    else:
        extra = []
        if distance_block:
            extra.append("anti-chase")
        if rule_block:
            extra.append("rule-engine")
        extra_txt = f" (blocked by: {', '.join(extra)})" if extra else ""
        st.info("🧊 NO TRADE — no EV edge / odds fetch failed" + extra_txt)

    # ---------------------------
    # Chart
    # ---------------------------
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=df15["timestamp"],
            open=df15["open"],
            high=df15["high"],
            low=df15["low"],
            close=df15["close"],
            name="BTC",
        )
    )
    if "ema9" in df15.columns:
        fig.add_trace(go.Scatter(x=df15["timestamp"], y=df15["ema9"], name="EMA 9", line=dict(width=1.3)))
    if "ema50" in df15.columns:
        fig.add_trace(go.Scatter(x=df15["timestamp"], y=df15["ema50"], name="EMA 50", line=dict(width=1.3)))

    fig.update_layout(template="plotly_dark", height=700, xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("🔍 Strategy Evidence & Logic", expanded=True):
        if evidence:
            total = 0.0
            for i, item in enumerate(evidence, start=1):
                impact = float(item.get("impact", 0.0))
                emoji = item.get("emoji", "")
                label = item.get("label", "")
                total += impact
                sign = "+" if impact > 0 else ""
                st.markdown(f"**{i}.** {emoji} {label} — `{sign}{impact:.2f}`")
            st.divider()
            st.markdown(f"**Evidence Total:** `{total:.2f}`")
        else:
            st.info("Gathering evidence...")


live_dashboard()





