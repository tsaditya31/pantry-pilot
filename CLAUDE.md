# Pantry Pilot — Shopping Intelligence Telegram Bot

## Architecture
Multi-user Telegram bot. Users upload receipt/pantry photos, Claude vision extracts items, shopping engine suggests what to buy based on purchase history vs current inventory.

## Tech Stack
- Python 3.12, custom Telegram long-polling via httpx
- Claude API with vision (claude-sonnet-4-6) for image analysis
- PostgreSQL via psycopg2, pydantic-settings for config
- Deployed on Railway (bot service + Postgres plugin)

## Project Structure
```
pantry-pilot/
├── main.py              # CLI entrypoint ("bot" command)
├── config.py            # pydantic-settings env config
├── db/store.py          # Schema, migrations, all CRUD
├── bot/telegram_api.py  # sendMessage, getFile, sendChatAction helpers
├── bot/telegram_bot.py  # Long-polling loop, photo download, multi-user routing
├── core/chat_handler.py # Route commands/photos, format responses
├── core/receipt_extractor.py    # Claude vision: receipt → structured items
├── core/pantry_extractor.py     # Claude vision: pantry photo → items
├── core/shopping_engine.py      # Purchase history vs pantry → suggestions
├── core/item_normalizer.py      # Canonicalize item names for matching
└── tests/
```

## Database Schema (6 tables)
1. **users** — telegram_id (unique), first_name, username, timezone, created_at
2. **receipts** — user_id FK, telegram_file_id, store_name, purchase_date, total_amount, raw_extraction (JSONB)
3. **receipt_items** — receipt_id FK, user_id FK, item_name, normalized_name, category, quantity, unit, price
4. **pantry_snapshots** — user_id FK, snapshot_type (pantry/fridge/freezer), telegram_file_id, raw_extraction (JSONB)
5. **pantry_items** — snapshot_id FK, user_id FK, item_name, normalized_name, category, estimated_qty, condition, is_current (bool)
6. **shopping_suggestions** — user_id FK, item_name, normalized_name, reason, priority, last_purchased, in_pantry, dismissed

## Key Design Decisions
- Pantry uses snapshot model: new photo replaces old items for that location type via `is_current` flag
- Receipts are always additive
- Every DB query scoped by user_id (multi-user isolation)
- Open to all Telegram users (no allowlist), auto-register on first message

## Bot Commands
| Input | Action |
|-------|--------|
| `/start` | Auto-register, welcome |
| `/help` | List commands |
| Photo + caption "receipt" | Extract receipt items via Claude vision |
| Photo + caption "pantry"/"fridge"/"freezer" | Extract pantry items |
| Photo + no caption | Ask for caption |
| `/list` | Generate shopping suggestions |
| `/history` | Recent purchases (7 days) |
| `/items` | Current pantry/fridge inventory |
| `/clear` | Reset pantry items |
| Text | Claude chat for corrections/questions |

## Shopping Engine Algorithm
1. Query receipt_items from last 90 days, group by normalized_name
2. Compute per-item: purchase_count, avg_interval_days, last_purchased
3. Query current pantry items (is_current=TRUE)
4. Suggest items: not in pantry (high), nearly empty (normal), overdue (low)
5. Return sorted by priority

## Environment Variables
- ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, DATABASE_URL
