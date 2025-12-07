# Code Review Findings

## Issues Identified and Fixed

### 1. Critical: Asyncio Context Issues ✅ FIXED
**Location**: `src/bot/engine/state_machine.py`

**Problem**: The engine's `loop()` method runs in a separate thread (via `asyncio.to_thread()`), but was calling `asyncio.create_task()` directly, which requires being in an async context.

**Solution**: 
- Added `_event_loop` attribute to Engine class
- Created `set_event_loop()` method to receive the event loop from the async server
- Implemented `_publish()` method that uses `asyncio.run_coroutine_threadsafe()` to safely publish events from the engine thread
- Updated both `server.py` and `Nserver.py` to call `engine.set_event_loop(loop)` before starting the bot

### 2. Error Handling in External API Calls ✅ FIXED
**Location**: `src/bot/data/finnhub_earnings.py`

**Problem**: Broad `except Exception` clause was hiding all errors silently, making debugging difficult.

**Solution**: 
- Added specific exception handling for:
  - `requests.exceptions.Timeout`
  - `requests.exceptions.RequestException`
  - `ValueError` (for JSON parsing errors)
- Added logging for each error type with context
- Added check for missing API key

### 3. Input Validation Missing ✅ FIXED
**Location**: `src/bot/api/server.py`, `src/bot/api/Nserver.py`

**Problem**: Symbol parameters weren't validated, could accept malicious input.

**Solution**: Added validation using `isalnum()` to ensure symbols contain only alphanumeric characters.

### 4. Division by Zero Risks ✅ FIXED
**Location**: `src/bot/strategy/rules.py`

**Problem**: Several calculations could divide by zero:
- VWAP calculation with zero volume
- Extension calculation with zero VWAP
- Near-touch tolerance with zero VWAP

**Solution**: 
- Replaced zero values with `math.nan` before division
- Added fallback values where appropriate

### 5. Missing .gitignore ✅ FIXED
**Location**: Root directory

**Problem**: Python cache files (`__pycache__`) were being committed to git.

**Solution**: 
- Created comprehensive `.gitignore` file
- Removed all `__pycache__` directories from git

## Architectural Issues (Not Fixed - Design Decisions)

### 6. Duplicate Server Implementations
**Location**: `src/bot/api/server.py` vs `src/bot/api/Nserver.py`

**Observation**: Two nearly identical FastAPI server implementations exist:
- `server.py` - More feature-complete with event publishing to operator logs
- `Nserver.py` - Slightly simpler version without some event messages

**Recommendation**: 
- Consolidate into a single server file
- Or document the purpose of each (e.g., one for production, one for testing)
- Current state causes confusion about which to use

**Not Fixed**: This appears to be an intentional design choice or work-in-progress. Consolidating would require user decision on which features to keep.

### 7. Global State in Modules
**Location**: `src/bot/api/server.py`, `src/bot/api/Nserver.py`

**Observation**: Heavy use of module-level global variables:
- `_prices_ws`, `_prices_queue`, `_prices_symbols`
- `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY`, `ALPACA_DATA_WS`
- Modified via `globals().update()` in `/mode` endpoint

**Concerns**:
- Thread safety issues if accessed concurrently
- Difficult to test in isolation
- Harder to reason about state changes

**Recommendation**: Encapsulate in a class or use dependency injection pattern.

**Not Fixed**: This would require significant refactoring and may break existing behavior.

### 8. Hardcoded Credentials Risk
**Location**: `src/bot/broker/alpaca_data.py` (lines 6-8)

**Observation**: Module defines default environment variables:
```python
ALPACA_DATA_WS = os.getenv("ALPACA_DATA_WS", "wss://...")
ALPACA_KEY_ID = os.getenv("ALPACA_KEY_ID", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
```

**Note**: These are module-level but get default empty strings, so no actual credentials are hardcoded. However, the pattern is risky.

**Not Fixed**: Current implementation is actually safe as defaults are empty strings.

## Minor Issues and Improvements

### 9. Resource Cleanup
**Observation**: Most WebSocket handlers use `contextlib.suppress(Exception)` for cleanup, which is acceptable but could mask errors.

**Recommendation**: Consider logging suppressed exceptions at debug level.

### 10. Timezone Consistency
**Observation**: Mix of timezone handling approaches:
- Some use `ZoneInfo("America/New_York")`
- Some convert from UTC
- Some use `now_et()` helper

**Recommendation**: Standardize on the `now_et()` helper throughout.

### 11. Magic Numbers
**Observation**: Several hardcoded values:
- Lookback periods (3, 5, 14)
- Default symbols ("AAPL", "MSFT")
- Timeout values (10 seconds)

**Recommendation**: Move to configuration with sensible defaults.

## Security Considerations

### 12. CORS Configuration
**Location**: `src/bot/api/server.py`, `src/bot/api/Nserver.py`

**Observation**: CORS is set to allow all origins (`allow_origins=["*"]`)

**Risk**: Allows any website to make requests to the API.

**Recommendation**: In production, restrict to specific domains.

### 13. Token Authentication
**Observation**: Control endpoints require token, but streams don't.

**Assessment**: Reasonable design - allows public monitoring while protecting control actions.

### 14. No HTTPS
**Observation**: Server runs on HTTP (127.0.0.1:8000)

**Note**: Acceptable for local development. Should use reverse proxy with HTTPS in production.

## Code Quality

### 15. Docstrings
**Observation**: Some functions lack docstrings, making intent unclear.

**Recommendation**: Add docstrings to all public functions and classes.

### 16. Type Hints
**Observation**: Good use of type hints throughout most of the codebase.

**Praise**: This is a strength of the code - makes it easier to understand and maintain.

### 17. Error Messages
**Observation**: Error messages are generally clear and helpful.

**Praise**: Good user experience for debugging.

## Testing

### 18. Test Coverage
**Observation**: Basic tests exist for:
- Strategy rules
- Bucket settlement
- Daily caps

**Recommendation**: Add tests for:
- API endpoints
- Error handling paths
- Edge cases in calculations

## Summary

**Critical Issues Fixed**: 5
- Asyncio context problems
- Error handling
- Input validation
- Division by zero
- Git hygiene

**Architectural Concerns**: 3 (noted but not changed)
- Duplicate servers
- Global state management
- Resource cleanup patterns

**Overall Assessment**: The code is functional and well-structured with good use of type hints. The main issues were around error handling and the asyncio/threading interaction, which have been addressed. The remaining concerns are architectural choices that would require larger refactoring efforts.
