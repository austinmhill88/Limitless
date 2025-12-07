# Limitless Trading Bot

An automated trading system built with Python that implements a systematic trading strategy using Alpaca's brokerage API.

## Features

- **Automated Trading**: Executes trades based on technical indicators and predefined rules
- **Multiple Trading Modes**: Supports both cash and margin account modes
- **Risk Management**: Implements daily profit/loss caps, position sizing, and cooldown periods
- **Real-time Monitoring**: Web-based dashboard with live position tracking and market data streaming
- **Earnings Calendar Integration**: Automatically avoids trading around earnings announcements
- **REST API**: Full control through REST endpoints for starting/stopping and monitoring

## Architecture

### Core Components

- **Engine (`state_machine.py`)**: Main trading loop that scans markets, enters positions, and manages exits
- **Strategy (`rules.py`)**: Technical analysis rules including EMA crossovers, VWAP tests, and opening range breakouts
- **Broker Adapter (`alpaca_adapter.py`)**: Interface to Alpaca's REST API for order execution and account data
- **API Server (`server.py`)**: FastAPI application providing REST and WebSocket endpoints
- **Storage (`buckets_ledger.py`)**: Cash management system for T+1 settlement in cash accounts

### Trading Strategy

The bot implements a momentum-based strategy with the following entry criteria:
- Price above 20 and 50-period EMAs (uptrend)
- Price above VWAP after a retest/pullback
- Price above opening range high (9:30-9:45 ET)
- Confirmation signals (higher lows, VWAP reclaim)
- Volume and spread filters

Exit conditions:
- Target profit (configurable percentage or ATR-based)
- ATR-based trailing stops
- Maximum adverse excursion (MAE) stops
- Friday position flattening
- Daily profit caps

## Installation

### Prerequisites

- Python 3.9 or higher
- Alpaca brokerage account (paper or live trading)
- Finnhub API key (for earnings calendar)

### Setup

1. Clone the repository:
```bash
git clone https://github.com/austinmhill88/Limitless.git
cd Limitless
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create a `.env` file in the project root with your API credentials:
```env
# Alpaca API credentials (paper trading)
ALPACA_PAPER_KEY_ID=your_paper_key_id
ALPACA_PAPER_SECRET_KEY=your_paper_secret_key
ALPACA_PAPER_BASE=https://paper-api.alpaca.markets

# Alpaca API credentials (live trading - optional)
ALPACA_LIVE_KEY_ID=your_live_key_id
ALPACA_LIVE_SECRET_KEY=your_live_secret_key
ALPACA_LIVE_BASE=https://api.alpaca.markets

# Finnhub API key
FINNHUB_API_KEY=your_finnhub_api_key

# Control token for API authentication
CONTROL_TOKEN=your_secure_token

# Trading mode
DRY_RUN=true
```

4. Configure trading parameters (optional - defaults are provided):
```env
# Watchlist
WATCHLIST=TSLA,NVDA,AAPL,MSFT,QQQ,SPY

# Trading windows (Eastern Time)
WINDOW_MORNING_START=09:45
WINDOW_MORNING_END=11:15
WINDOW_POWER_START=15:00
WINDOW_POWER_END=15:55

# Risk parameters
CONCURRENCY_CAP=3
DAILY_SOFT_CAP_PCT=0.01
DAILY_HARD_CAP_PCT=0.015
```

## Usage

### Starting the Server

```bash
python main.py
```

The server will start on `http://127.0.0.1:8000`

### Web Dashboard

Access the web UI at: `http://127.0.0.1:8000/ui`

### API Endpoints

#### Control Endpoints (require authentication token)

- **Start Bot**: `POST /control?action=start_bot&token=YOUR_TOKEN`
- **Stop Bot**: `POST /control?action=stop_bot&token=YOUR_TOKEN`
- **Set Mode**: `POST /mode?mode=paper&token=YOUR_TOKEN` (paper or live)

#### Status Endpoints (no auth required)

- **Status**: `GET /status` - Get current bot status, equity, positions, and caps
- **Positions**: `GET /positions` - List all open positions

#### WebSocket Streams

- **Events**: `ws://127.0.0.1:8000/events` - Real-time trading events and logs
- **Prices**: `ws://127.0.0.1:8000/prices` - Market data stream
- **Heartbeat**: `ws://127.0.0.1:8000/stream?token=YOUR_TOKEN` - System heartbeat

## Configuration

### Environment Variables

Key configuration options:

| Variable | Default | Description |
|----------|---------|-------------|
| `DRY_RUN` | `true` | Enable/disable paper trading mode |
| `CONCURRENCY_CAP` | `3` | Maximum number of concurrent positions |
| `TARGET_PCT` | `0.005` | Target profit percentage (0.5%) |
| `SOFT_CAP_PCT` | `0.01` | Soft daily profit cap (1%) |
| `HARD_CAP_PCT` | `0.015` | Hard daily profit cap (1.5%) |
| `ATR_LEN` | `14` | ATR calculation period |
| `RVOL_MIN` | `1.1` | Minimum relative volume filter |
| `SPREAD_MAX_PCT` | `0.0015` | Maximum bid-ask spread (0.15%) |

See `src/bot/config/settings.py` for all available options.

## Testing

Run tests using Python's unittest:

```bash
python -m unittest discover tests
```

## Project Structure

```
Limitless/
├── src/
│   └── bot/
│       ├── api/          # FastAPI servers and endpoints
│       ├── broker/       # Alpaca broker integration
│       ├── config/       # Configuration and settings
│       ├── data/         # External data (earnings calendar)
│       ├── engine/       # Trading engine and state machine
│       ├── logging/      # Event logging and audit trails
│       ├── storage/      # Cash bucket management
│       └── strategy/     # Trading rules and indicators
├── tests/                # Unit tests
├── webui/               # Web dashboard files
├── main.py              # Application entry point
└── requirements.txt     # Python dependencies
```

## Safety Features

- **Daily Profit Caps**: Automatic trading halt when daily limits are reached
- **Cooldown Periods**: Prevents over-trading with time-based restrictions
- **Earnings Lockout**: Skips trading symbols around earnings announcements
- **Spread Filters**: Avoids wide bid-ask spreads
- **Volume Filters**: Requires minimum relative volume
- **Slippage Protection**: Limits entry if price has moved too far from signal

## Warnings

⚠️ **IMPORTANT**: This is trading software that involves real financial risk.

- Always test thoroughly in paper trading mode before using live funds
- Understand the strategy and risks before deploying
- Monitor the bot actively during market hours
- Have a plan for handling technical failures or market disruptions
- Only trade with capital you can afford to lose

## License

See repository license file for details.

## Support

For issues or questions, please open an issue on GitHub.
