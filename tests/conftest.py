"""
Pytest conftest — set dummy environment variables before any app module is imported.
"""

import os

os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-dummy")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "000000:TEST-TOKEN")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test_pantry")
