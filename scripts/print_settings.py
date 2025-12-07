import sys
sys.path.insert(0, r"C:\Users\austi\OneDrive\Desktop\Limitless\src")
from bot.config.settings import settings

print("KEY=", repr(settings.alpaca_key_id))
print("SECRET=", "***" if settings.alpaca_secret_key else "")
print("BASE=", settings.alpaca_base)