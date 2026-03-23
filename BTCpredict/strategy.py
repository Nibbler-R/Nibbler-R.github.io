import pandas as pd


def _add(evidence, impact: float, label: str, emoji: str = ""):
    evidence.append({"impact": float(impact), "label": label, "emoji": emoji})


def _m5_confirm(df_m5: pd.DataFrame):
    """
    Micro confirmation from 5m chart.
    Returns dict with:
      trend: "BULL"|"BEAR"|"NEUTRAL"
      reasons: list[str]
    """
    if df_m5 is None or len(df_m5) < 30:
        return {"trend": "NEUTRAL", "reasons": ["5m history building"]}

    last = df_m5.iloc[-1]
    prev = df_m5.iloc[-2]

    close = float(last["close"])
    open_ = float(last["open"])

    ema9 = last.get("ema9", None)
    ema9 = float(ema9) if pd.notna(ema9) else None

    # 5m impulse (current candle body)
    body = (close - open_) / open_ if open_ else 0.0

    # short momentum: last 2 closes direction
    mom = float(last["close"]) - float(prev["close"])

    bull_votes = 0
    bear_votes = 0
    reasons = []

    if ema9 is not None:
        if close > ema9:
            bull_votes += 1
            reasons.append("5m close > EMA9")
        else:
            bear_votes += 1
            reasons.append("5m close < EMA9")

    if body >= 0.0015:
        bull_votes += 1
        reasons.append(f"5m impulse +{body*100:.2f}%")
    elif body <= -0.0015:
        bear_votes += 1
        reasons.append(f"5m impulse {body*100:.2f}%")

    if mom > 0:
        bull_votes += 1
        reasons.append("5m momentum up")
    elif mom < 0:
        bear_votes += 1
        reasons.append("5m momentum down")

    if bull_votes >= 2 and bull_votes > bear_votes:
        return {"trend": "BULL", "reasons": reasons}
    if bear_votes >= 2 and bear_votes > bull_votes:
        return {"trend": "BEAR", "reasons": reasons}
    return {"trend": "NEUTRAL", "reasons": reasons}


def score_market(df_15m: pd.DataFrame, minutes_remaining: float = 15.0, df_m5: pd.DataFrame | None = None):
    """
    Returns:
      score (float)
      evidence (list[dict]) -> [{impact, label, emoji}, ...]
      signal (str) -> "BUY_UP" | "BUY_DOWN" | "NO_TRADE"
      trade_allowed (bool)

    Improvements vs prior version:
      - Slightly looser gates (more opportunities)
      - Adaptive late entries when momentum is strong (captures real late moves)
      - Optional 5m confirmation to avoid fake 15m breaks
    """

    evidence = []
    if df_15m is None or len(df_15m) < 35:
        _add(evidence, 0.0, "Building data history…", "⏳")
        return 0.0, evidence, "NO_TRADE", False

    last = df_15m.iloc[-1]
    prev = df_15m.iloc[-2]

    close = float(last["close"])
    open_ = float(last["open"])
    vol = float(last["volume"])

    # ---------------------------
    # ⏱️ Entry window
    # ---------------------------
    minutes_elapsed = 15.0 - float(minutes_remaining)

    ENTRY_OPEN_MIN = 2.0
    # Keep a preferred entry window, but allow late entries if conditions are strong.
    PREFERRED_CLOSE_MIN = 10.0

    if minutes_elapsed < ENTRY_OPEN_MIN:
        _add(evidence, 0.0, f"NO TRADE: Noise window (elapsed {minutes_elapsed:.1f}m < {ENTRY_OPEN_MIN}m)", "🧊")
        return 0.0, evidence, "NO_TRADE", False

    # ---------------------------
    # ✅ Feature pulls (15m)
    # ---------------------------
    adx = float(last["adx"]) if pd.notna(last.get("adx", None)) else None
    range_ratio = float(last["range_ratio"]) if pd.notna(last.get("range_ratio", None)) else None

    avg_vol = df_15m["volume"].rolling(20).mean().iloc[-1]
    avg_vol = float(avg_vol) if pd.notna(avg_vol) else None

    break_up = bool(last.get("break_up", False))
    break_down = bool(last.get("break_down", False))

    h1 = str(last.get("h1_trend", "NEUTRAL"))

    # Impulse (body)
    body = (close - open_) / open_ if open_ else 0.0
    body_pct = body * 100

    # Strong-momentum late-entry criteria
    strong_momentum = (
        (adx is not None and adx >= 22)
        and (range_ratio is not None and range_ratio >= 1.40)
        and (abs(body) >= 0.0040)
        and (break_up or break_down)
    )

    # Late guard (adaptive)
    if minutes_elapsed > PREFERRED_CLOSE_MIN:
        if minutes_remaining <= 2.0:
            _add(evidence, 0.0, f"NO TRADE: Too late (remaining {minutes_remaining:.1f}m)", "🛑")
            return 0.0, evidence, "NO_TRADE", False

        if strong_momentum:
            _add(evidence, 0.0, "Late entry allowed: strong momentum detected", "🟢")
        else:
            _add(evidence, 0.0, f"NO TRADE: Late candle (elapsed {minutes_elapsed:.1f}m)", "🛑")
            return 0.0, evidence, "NO_TRADE", False

    # ---------------------------
    # ✅ NO-TRADE FILTERS (quality gates) — loosened slightly
    # ---------------------------
    trade_allowed = True

    # ADX gate (was 18)
    if adx is None:
        trade_allowed = False
        _add(evidence, 0.0, "ADX not ready", "🧮")
    elif adx < 16:
        trade_allowed = False
        _add(evidence, 0.0, f"Trend too weak (ADX {adx:.1f} < 16)", "🧊")
    else:
        _add(evidence, 0.0, f"Trend OK (ADX {adx:.1f})", "💪")

    # Expansion gate (was 1.10)
    if range_ratio is None:
        trade_allowed = False
        _add(evidence, 0.0, "Range ratio not ready", "🧮")
    elif range_ratio < 1.05:
        trade_allowed = False
        _add(evidence, 0.0, f"No expansion (range x{range_ratio:.2f} < 1.05)", "🧊")
    else:
        _add(evidence, 0.0, f"Range expansion (x{range_ratio:.2f})", "📏")

    # Volume gate (was 0.95x)
    if avg_vol is None:
        trade_allowed = False
        _add(evidence, 0.0, "Volume avg not ready", "🧮")
    else:
        if vol < avg_vol * 0.90:
            trade_allowed = False
            _add(evidence, 0.0, "Volume too low (below avg)", "🔇")
        else:
            _add(evidence, 0.0, "Volume OK (>= avg)", "📈")

    _add(evidence, 0.0, f"H1 bias: {h1}", "🧭")

    # ---------------------------
    # ✅ SCORE (weighted + explainable)
    # ---------------------------
    score = 0.0

    if body >= 0.0025:
        score += 3.0
        _add(evidence, +3.0, f"Bull impulse {body_pct:.2f}%", "⚡")
    elif body <= -0.0025:
        score -= 3.0
        _add(evidence, -3.0, f"Bear impulse {body_pct:.2f}%", "🚨")
    else:
        _add(evidence, 0.0, f"Impulse neutral {body_pct:.2f}%", "🧊")

    # Breakout / breakdown
    if break_up:
        score += 2.2
        _add(evidence, +2.2, "Breakout above prior high", "📈")
    if break_down:
        score -= 2.2
        _add(evidence, -2.2, "Breakdown below prior low", "📉")

    # EMA9 position
    ema9 = last.get("ema9", None)
    if pd.notna(ema9) and float(ema9) != 0:
        ema9 = float(ema9)
        if close > ema9:
            score += 1.0
            _add(evidence, +1.0, "Price above EMA9", "🚀")
        else:
            score -= 1.2
            _add(evidence, -1.2, "Price below EMA9", "🔻")
    else:
        _add(evidence, 0.0, "EMA9 not ready", "🧮")

    # EMA structure
    ema50 = last.get("ema50", None)
    if pd.notna(last.get("ema9", None)) and pd.notna(ema50):
        if float(last["ema9"]) > float(last["ema50"]):
            score += 0.8
            _add(evidence, +0.8, "Bull structure (EMA9>EMA50)", "📈")
        else:
            score -= 0.8
            _add(evidence, -0.8, "Bear structure (EMA9<EMA50)", "📉")

    # RSI (light touch)
    rsi = last.get("rsi", None)
    if pd.notna(rsi):
        rsi = float(rsi)
        if rsi > 58:
            score += 0.6
            _add(evidence, +0.6, f"RSI bullish ({rsi:.1f})", "📶")
        elif rsi < 42:
            score -= 0.6
            _add(evidence, -0.6, f"RSI bearish ({rsi:.1f})", "📶")
        else:
            _add(evidence, 0.0, f"RSI neutral ({rsi:.1f})", "📶")

    # HTF softener
    if h1 == "BEAR" and score > 0:
        score -= 1.0
        _add(evidence, -1.0, "HTF BEAR blocks bullish aggression", "🛡️")
    elif h1 == "BULL" and score < 0:
        score += 1.0
        _add(evidence, +1.0, "HTF BULL blocks bearish aggression", "🛡️")

    # Exhaustion guard: huge candle very late (keep, but slightly smarter)
    if minutes_elapsed >= 12.5 and abs(body) >= 0.0065 and not strong_momentum:
        _add(evidence, 0.0, "NO TRADE: Very late big candle (exhaustion risk)", "🛑")
        return round(score, 2), evidence, "NO_TRADE", False

    score = round(score, 2)

    # ---------------------------
    # 🧨 SMART OVERRIDE
    # ---------------------------
    override_down = break_down and (body <= -0.0025) and (h1 != "BULL")
    override_up = break_up and (body >= 0.0025) and (h1 != "BEAR")

    if not trade_allowed and (override_down or override_up):
        _add(evidence, 0.0, "Override: Break + Impulse (allowed)", "🧨")
        trade_allowed = True

    # ---------------------------
    # ✅ 5m confirmation (optional)
    # ---------------------------
    if df_m5 is not None:
        m5 = _m5_confirm(df_m5)
        m5_trend = m5["trend"]
        _add(evidence, 0.0, f"5m confirm: {m5_trend}", "🧩")

        # If 15m score wants UP but 5m is BEAR (or vice versa), block unless it's a very strong setup.
        if score >= 3.0 and m5_trend == "BEAR" and abs(score) < 6.0:
            _add(evidence, 0.0, "NO TRADE: 5m contradicts bullish setup", "🚫")
            return score, evidence, "NO_TRADE", False
        if score <= -3.0 and m5_trend == "BULL" and abs(score) < 6.0:
            _add(evidence, 0.0, "NO TRADE: 5m contradicts bearish setup", "🚫")
            return score, evidence, "NO_TRADE", False

    # ---------------------------
    # ✅ Signal decision
    # ---------------------------
    if not trade_allowed:
        _add(evidence, 0.0, "NO TRADE (filters blocked entry)", "🧊")
        return score, evidence, "NO_TRADE", False

    if score >= 3.0:
        return score, evidence, "BUY_UP", True
    if score <= -3.0:
        return score, evidence, "BUY_DOWN", True

    _add(evidence, 0.0, "NO TRADE (score not strong enough)", "🧊")
    return score, evidence, "NO_TRADE", True


