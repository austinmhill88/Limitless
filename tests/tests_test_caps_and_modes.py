from bot.engine.state_machine import Engine

def test_mode_switch_and_caps():
    eng = Engine()
    eng.daily_start_equity = 30000.0
    eng.daily_realized_usd = 300.0  # +1%
    soft, hard = eng.daily_caps_state()
    assert soft is True
    assert hard is False
    eng.daily_realized_usd = 450.0  # +1.5%
    soft, hard = eng.daily_caps_state()
    assert soft is True
    assert hard is True