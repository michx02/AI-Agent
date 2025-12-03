# Multiconversational Discord AI Agent (Gemini + RAG-Style Memory)

A Discord AI agent that responds when mentioned, uses Google Gemini for natural language generation, and maintains long-term coherence using a lightweight, RAG-inspired memory system backed by PostgreSQL. The agent keeps track of threads, summaries, and recent context to support multi-conversation interactions across channels and threads.

## Features
- Gemini-generated responses trimmed to Discord's 2,000-character limit  
- RAG-style memory pipeline with message logging, retrieval, and summarization  
- Thread-aware and channel-aware context building  
- Ambient context injection (recent messages, mention-based context, thread history)  
- PostgreSQL-backed storage (see `DATABASE_URL`), auto-created on first run  

## Quickstart
1. Install dependencies  
```bash
pip install -r requirements.txt
```
2. Create a `.env` file  
```bash
YOUR_API_KEY=<google-genai-api-key>
YOUR_BOT_TOKEN=<discord-bot-token>
DATABASE_URL=<postgres-connection-string>
```
3. Run the bot  
```bash
python main.py
```

## How It Works
The agent is built around three core components: the bot logic, the memory engine, and the message logger. Together, they form a lightweight RAG-style system tailored for Discord's conversational structure.

### Entry Point — `main.py`
- Loads `.env` configuration values  
- Initializes the Discord client  
- Connects the bot logic, memory engine, and logger into a single runtime  
- Starts the event loop that listens for messages and mentions  

### Bot Logic — `bot.py`
- Responds only when the bot is mentioned in a message  
- Collects context (recent messages, reply relationships, stored summaries)  
- Builds a structured prompt using RAG-style retrieval  
- Sends the constructed prompt to Gemini (`gemini-2.5-flash`)  
- Returns a reply that fits Discord's 2,000-character limit  

### Memory System — `memory.py`
- Maintains PostgreSQL tables (`threads`, `turns`, `profiles`, `team_facts`)  
- Tracks thread identifiers, individual turns, rolling summaries, and per-user/team facts  
- Summarizes older content when a thread exceeds the configured character budget  
- Keeps long-running threads coherent without exceeding context limits  

### Logging System — `logger.py`
- Saves every message into the PostgreSQL `messages` table (id, author, timestamps, content, reply/thread links)  
- Provides helpers to fetch channel, thread, and user-scoped history  
- Supplies retrieval data used in the prompt-building step  

## Data Storage
All persisted data lives in PostgreSQL, configured via `DATABASE_URL`. Tables are created automatically on startup.

## Requirements
- Python 3.10+  
- Discord bot token (with Message Content intent enabled)  
- Google GenAI API key  
- PostgreSQL database URL  

## Notes
- The bot only responds when mentioned.  
- Summaries keep the memory database small while preserving coherence.  
- Designed to support multi-conversation interactions across threads, channels, and servers.  
