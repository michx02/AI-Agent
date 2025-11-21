# Multiconversational Discord AI Agent (Gemini + RAG-Style Memory)

A Discord AI agent that responds when mentioned, uses Google Gemini for natural language generation, and maintains long-term coherence using a lightweight, RAG-inspired memory system backed by SQLite. The agent keeps track of threads, summaries, and recent context to support multiconversational interactions across channels and threads.

## Features

- Gemini-generated responses trimmed to Discord’s 2,000-character limit  
- RAG-style memory pipeline with:
  - message logging  
  - context retrieval from recent history  
  - summarization when memory grows too large  
- Thread-aware and channel-aware context building  
- Ambient context injection (recent messages, mention-based context, thread history)  
- SQLite-backed storage (`memory.sqlite3` and `discord_logs.db`), auto-created on first run  

## Quickstart

### 1. Install dependencies
```bash
pip install -r requirements.txt
```
### 2. Create a .env file
```bash
YOUR_API_KEY=<google-genai-api-key>
YOUR_BOT_TOKEN=<discord-bot-token>

# Optional overrides
BOT_MEMORY_DB=memory.sqlite3
DISCORD_LOG_DB=discord_logs.db
```

### 3. Run the bot
```bash
python main.py
```

## How It Works

The agent is built around three core components: the bot logic, the memory engine, and the message logger.  
Together, they form a lightweight RAG-style system tailored for Discord’s conversational structure.

### Entry Point — `main.py`
- Loads `.env` configuration values  
- Initializes the Discord client  
- Connects the bot logic, memory engine, and logger into a single runtime  
- Starts the event loop that listens for messages and mentions  

### Bot Logic — `bot.py`
This module contains all runtime behavior when the bot is invoked.

- Responds only when the bot is mentioned in a message  
- Collects relevant context, including:
  - recent messages from the same channel or thread  
  - the relationship between replies (who replied to whom)  
  - previously stored summaries related to this thread  
- Builds a structured prompt using RAG-style retrieval:
  - fetch logged messages  
  - fetch stored summaries  
  - include ambient context (mentions, recent thread history)  
- Sends the constructed prompt to Gemini (`gemini-2.5-flash`)  
- Trims and returns a reply that fits Discord’s 2,000-character limit  

### Memory System — `memory.py`
Handles all long-term tracking of conversations.

- Maintains a SQLite database (`memory.sqlite3`) containing:
  - thread identifiers  
  - individual conversation turns  
  - rolling summaries  
  - optional per-user or global facts  
- Automatically summarizes older content when a thread exceeds the configured character budget  
- Ensures that even long-running threads remain coherent without exceeding context limits  

### Logging System — `logger.py`
Stores raw Discord messages for retrieval.

- Saves every message into `discord_logs.db` with:
  - message ID  
  - author ID  
  - timestamp  
  - content  
  - reply/thread relationships  
- Provides helper functions to fetch:
  - the last N messages in a channel  
  - thread-specific history  
  - messages from referenced replies  
- Supplies the bot with retrieval data used in the prompt-building step  

## Data Files

- `memory.sqlite3` — stores summaries, thread metadata, and long-term memory  
- `discord_logs.db` — stores raw Discord messages for RAG-style retrieval  
- Both are automatically created if missing, and paths can be customized through `.env`

## Requirements

- Python 3.10+  
- Discord bot token (with Message Content intent enabled)  
- Google GenAI API key  

## Notes

- The bot only responds when mentioned.  
- Summaries keep the memory database small while preserving coherence.  
- Designed to support multiconversational interactions across threads, channels, and servers.  
