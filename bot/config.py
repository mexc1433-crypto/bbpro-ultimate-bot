import os
from dataclasses import dataclass, field
from typing import List
from enum import Enum

class BreakoutMode(str, Enum):
    TOUCH_BAND       = "touch"
    CLOSE_OUTSIDE    = "close"
    PENETRATION_PIPS = "penetration"

class TradeDirection(str, Enum):
    BUY  = "buy"
    SELL = "sell"

class SizingMode(str, Enum):
    RISK_PERCENT = "risk_percent"
    FIXED_LOTS   = "fixed_lots"

@dataclass
class BotConfig:
    # cTrader Open API — loaded from env vars
    client_id:     str = ""
    client_secret: str = ""
    access_token:  str = ""
    api_token:     str = ""   # fallback token for REST
    account_id:    int = 47838646
    host:          str = "demo.ctraderapi.com"
    port:          int = 5035
    symbol:        str = "XAUUSD"
    timeframe:     str = "m30"

    # Multiple symbols support
    active_symbol:     str = 'XAUUSD'

    # Bollinger Bands
    bb_period:             int   = 20
    bb_deviations:         float = 2.0
    bb_mode:               BreakoutMode = BreakoutMode.CLOSE_OUTSIDE
    require_close_confirm: bool  = True

    # RSI
    rsi_period:        int   = 14
    rsi_overbought:    float = 70.0
    rsi_oversold:      float = 30.0
    rsi_exit_long:     float = 75.0
    rsi_exit_short:    float = 25.0
    enable_rsi_filter: bool  = True

    # Trend EMA
    fast_ema_period:     int  = 50
    slow_ema_period:     int  = 200
    enable_trend_filter: bool = True
    require_both_emas:   bool = True

    # ADX
    enable_adx_filter: bool  = True
    min_adx:           float = 25.0
    adx_period:        int   = 14

    # ATR
    atr_period:           int   = 14
    sl_atr_multiplier:    float = 1.5
    tp_atr_multiplier:    float = 2.0
    min_sl_pips:          float = 10.0
    sl_pad_pips:          float = 1.0
    min_volatility_ratio: float = 1.2
    std_dev_period:       int   = 14
    bot_label:            str   = "BBProV2"
    show_debug:           bool  = False

    # Sizing
    risk_per_trade:    float = 0.5
    sizing_mode:       SizingMode = SizingMode.RISK_PERCENT
    fixed_volume_lots: float = 0.01
    max_volume_lots:   float = 10.0

    # Trade Management
    enable_trailing:         bool  = True
    trail_start_atr_mult:    float = 1.0
    trail_distance_atr_mult: float = 1.0
    enable_break_even:       bool  = True
    be_trigger_atr_mult:     float = 1.0
    be_lock_in_pips:         float = 0.0
    enable_partial_tp:       bool  = True
    ptp_sl_multiplier:       float = 1.5
    ptp_percent:             int   = 40
    max_bars_in_trade:       int   = 0

    # Daily DD
    enable_daily_dd:    bool  = True
    max_daily_loss_pct: float = 3.0
    max_daily_trades:   int   = 0
    max_concurrent_pos: int   = 1

    # Sessions
    enable_session_filter: bool = True
    allow_asian:           bool = False
    allow_london:          bool = True
    allow_new_york:        bool = True
    only_overlap:          bool = False

    # Safety
    max_spread_pips:      float = 3.0
    kill_switch_on_error: bool  = True
    conflict_gate:        bool  = True

    # Friday / News
    trade_on_friday:       bool      = False
    friday_close_hour_utc: int       = 20
    enable_news_filter:    bool      = True
    news_blackout_minutes: int       = 30
    manual_news_times:     List[str] = field(default_factory=list)

    # Telegram
    telegram_enabled:   bool = False
    telegram_bot_token: str  = ""
    telegram_chat_id:   str  = ""

    # Database
    db_enabled: bool = True
    db_path:    str  = "/tmp/bbpro_trades.db"

    # Web Monitor
    web_monitor_enabled: bool = True
    web_monitor_host:    str  = "0.0.0.0"
    web_monitor_port:    int  = int(os.environ.get('PORT', 5100))

    # Bot Loop
    poll_interval_sec:   int   = 30
    warmup_bars:         int   = 300
    magic_label:         str   = "BBProV2"

    # Volume filter
    enable_volume_filter: bool  = True
    volume_ma_period:     int   = 20
    volume_threshold:     float = 1.2

    # Ichimoku Cloud
    enable_ichimoku: bool = False
    ichimoku_tenkan: int  = 9
    ichimoku_kijun:  int  = 26
    ichimoku_senkou: int  = 52

    # VWAP
    enable_vwap: bool = True

    # Smart Money Concepts (SMC)
    smc_fvg_min_pips:       float = 5.0
    smc_liquidity_lookback: int   = 50

    # Multi-symbol support
    symbols: List[str] = field(default_factory=lambda: ['EURUSD', 'XAUUSD', 'GBPUSD', 'USDJPY', 'EURJPY', 'USDCAD'])
    multi_symbol_mode: bool = True

    # Stochastic
    stoch_k_period: int = 5
    stoch_d_period: int = 3
    stoch_slowing: int = 3
    stoch_overbought: float = 80.0
    stoch_oversold: float = 20.0
    enable_stoch_filter: bool = True

    # MACD
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    enable_macd_filter: bool = False

    # Williams %R
    williams_r_period: int = 14
    williams_r_overbought: float = -20.0
    williams_r_oversold: float = -80.0
    enable_williams_r: bool = False

    # CCI
    cci_period: int = 20
    cci_overbought: float = 100.0
    cci_oversold: float = -100.0
    enable_cci: bool = False

    # Fibonacci
    enable_fib_levels: bool = True
    fib_lookback: int = 100

    # Pivot Points
    enable_pivot_points: bool = True

    # Heikin Ashi
    enable_heikin_ashi: bool = False

    # SMC
    smc_enabled: bool = False
    smc_ob_lookback: int = 20

    @property
    def hostname(self):
        return self.host

    def validate(self):
        errors = []
        if not self.client_id:
            errors.append("client_id not set")
        if not self.client_secret:
            errors.append("client_secret not set")
        if not self.access_token:
            errors.append("access_token not set")
        return errors


def load_config() -> BotConfig:
    """Load config from environment variables."""
    cfg = BotConfig()
    cfg.client_id = os.environ.get('CTRADER_CLIENT_ID_3', os.environ.get('CTRADER_CLIENT_ID', '')).strip()
    cfg.client_secret = os.environ.get('CTRADER_SECRET_4', os.environ.get('CTRADER_SECRET', '')).strip()
    cfg.access_token  = os.environ.get('CTRADER_ACCESS_TOKEN_4', os.environ.get('CTRADER_API_TOKEN', '')).strip()
    cfg.api_token     = os.environ.get('CTRADER_API_TOKEN', os.environ.get('CTRADER_ACCESS_TOKEN_4', '')).strip()
    _account_id = os.environ.get('CTRADER_ACCOUNT_ID', '').strip()
    if _account_id:
        try:
            cfg.account_id = int(_account_id)
        except ValueError:
            pass
    cfg.telegram_bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    cfg.telegram_chat_id = os.environ.get('TELEGRAM_CHAT_ID', '7005859703').strip()
    if cfg.telegram_bot_token:
        cfg.telegram_enabled = True

    # Database logic: enabled unless explicitly false
    db_env = os.environ.get('DB_ENABLED', '').strip().lower()
    if db_env == 'false':
        cfg.db_enabled = False
    else:
        cfg.db_enabled = True

    cfg.db_path = os.environ.get('DB_PATH', '/tmp/bbpro_trades.db').strip() or '/tmp/bbpro_trades.db'
    return cfg

load_from_env = load_config

DEFAULT_CONFIG = load_config()

# Auto-patch to enable telegram from environment
import os as _os
_token = _os.environ.get('TELEGRAM_BOT_TOKEN', '')
_chat = _os.environ.get('TELEGRAM_CHAT_ID', '7005859703')
if _token:
    DEFAULT_CONFIG.telegram_enabled = True
    DEFAULT_CONFIG.telegram_bot_token = _token
    DEFAULT_CONFIG.telegram_chat_id = _chat
