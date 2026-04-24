import io
import discord
from discord import File, Embed
from discord.ext import commands
from google import genai
from google.genai import types
from openai import OpenAI
from dotenv import load_dotenv  # pip install python-dotenv
import os
from typing import Any

from memory import Memory
from textwrap import wrap
from logger import log_message, fetch_recent_history_for_scope, log_message,fetch_user_recent_in_channel,fetch_user_recent_in_guild
import logger #part of local py files




load_dotenv()  # reads .env in project root

GEMINI_API_KEY = os.getenv("YOUR_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not GEMINI_API_KEY and not OPENAI_API_KEY:
    raise RuntimeError("Missing AI API keys. Set YOUR_API_KEY, OPENAI_API_KEY, or both.")





intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True


bot = commands.Bot(command_prefix="!", intents=intents)
memory = Memory(max_chars=6000)  # uses PostgreSQL for AI interactions




gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")
OPENAI_MCP_POSTGRES_SERVER_URL = os.getenv("OPENAI_MCP_POSTGRES_SERVER_URL")
OPENAI_MCP_POSTGRES_LABEL = os.getenv("OPENAI_MCP_POSTGRES_LABEL", "postgres")
OPENAI_MCP_POSTGRES_DESCRIPTION = os.getenv(
    "OPENAI_MCP_POSTGRES_DESCRIPTION",
    "PostgreSQL database tools for querying structured application data.",
)
OPENAI_MCP_POSTGRES_AUTH = os.getenv("OPENAI_MCP_POSTGRES_AUTH")
OPENAI_MCP_POSTGRES_REQUIRE_APPROVAL = os.getenv(
    "OPENAI_MCP_POSTGRES_REQUIRE_APPROVAL",
    "never",
)
OPENAI_MCP_POSTGRES_ALLOWED_TOOLS = [
    tool.strip()
    for tool in os.getenv("OPENAI_MCP_POSTGRES_ALLOWED_TOOLS", "").split(",")
    if tool.strip()
]


def build_openai_tools() -> list[dict[str, Any]]:
    if not OPENAI_MCP_POSTGRES_SERVER_URL:
        return []

    tool: dict[str, Any] = {
        "type": "mcp",
        "server_label": OPENAI_MCP_POSTGRES_LABEL,
        "server_description": OPENAI_MCP_POSTGRES_DESCRIPTION,
        "server_url": OPENAI_MCP_POSTGRES_SERVER_URL,
        "require_approval": OPENAI_MCP_POSTGRES_REQUIRE_APPROVAL,
    }
    if OPENAI_MCP_POSTGRES_AUTH:
        tool["authorization"] = OPENAI_MCP_POSTGRES_AUTH
    if OPENAI_MCP_POSTGRES_ALLOWED_TOOLS:
        tool["allowed_tools"] = OPENAI_MCP_POSTGRES_ALLOWED_TOOLS
    return [tool]


def should_prefer_openai() -> bool:
    return bool(openai_client and build_openai_tools())


def generate_text(prompt: str, system_instruction: str | None = None) -> str:
    if gemini_client and not should_prefer_openai():
        try:
            config = None
            if system_instruction:
                config = types.GenerateContentConfig(system_instruction=system_instruction)
            resp = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                config=config,
                contents=prompt,
            )
            text = resp.text or ""
            if text:
                return text
        except Exception as gemini_error:
            print(f"Gemini request failed, falling back to OpenAI: {gemini_error}")
            if not openai_client:
                raise

    if not openai_client:
        raise RuntimeError("No working AI provider is configured.")

    response = openai_client.responses.create(
        model=OPENAI_MODEL,
        instructions=system_instruction,
        input=prompt,
        tools=build_openai_tools() or None,
    )
    return response.output_text or "(no content)"

def summarize(text: str, limit=800):
    summary = generate_text(
        prompt=(
            "Summarize the following conversation into compact notes "
            f"(<= {limit} characters). Keep user goals/preferences and unresolved tasks.\n\n{text}"
        )
    )
    return summary[:limit]

# --- Conversation scoping helpers ---

async def get_root_message(msg: discord.Message) -> discord.Message:
    """Follow reply chain to the first message; fallback to channel starter in Threads."""
    cur = msg
    while cur.reference and cur.reference.resolved:
        cur = cur.reference.resolved
    # If in a Discord Thread, use the starter message if available
    if isinstance(msg.channel, discord.Thread):
        try:
            starter = await msg.channel.fetch_message(msg.channel.id)
            # Above sometimes returns the thread object; safer to use .message_id
        except Exception:
            starter = None
        # Discord exposes the starter as thread.message_id (int) on parent channel
        if getattr(msg.channel, "message_id", None):
            try:
                return await msg.channel.parent.fetch_message(msg.channel.message_id)
            except Exception:
                pass
    return cur

async def conversation_key(message: discord.Message) -> str:
    """Use a stable key so anyone can continue the same convo."""
    # If this is a reply chain, key on the root message id
    if message.reference:
        root = await get_root_message(message)
        return f"conv:reply:{root.id}"

    # If we’re inside a Discord Thread, key on the thread id
    if isinstance(message.channel, discord.Thread):
        return f"conv:thread:{message.channel.id}"

    # Otherwise, key on the channel with a rolling window (so the whole channel can ask “about above”)
    return f"conv:channel:{message.channel.id}"




def build_prompt(author_id: int, thread, guild, ambient_lines: list[str], targets: dict[int, list[str]]):
    user_facts = memory.get_facts(author_id)
    team_facts = memory.get_team_facts(guild.id) if guild and hasattr(memory, "get_team_facts") else []

    recent_turns = "\n".join(f"{t['role'].capitalize()}: {t['text']}" for t in thread["turns"])
    ambient = "\n".join(ambient_lines)

    preface = (
        "You are a helpful Discord assistant. Be concise. "
        "Ask follow-up questions only when necessary.\n"
        "If database MCP tools are available, use them when the user asks for factual data "
        "that should come from PostgreSQL instead of guessing.\n\n"
    )
    blocks = []
    if team_facts:
        blocks.append("Team knowledge (shared):\n- " + "\n- ".join(team_facts))
    if user_facts:
        blocks.append("About current user:\n- " + "\n- ".join(user_facts))
    if thread.get("summary"):
        blocks.append(f"Conversation summary so far:\n{thread['summary']}")
    if ambient:
        blocks.append("Context from recent untagged discussion in this thread/channel:\n" + ambient)

    # NEW: include per-mentioned-user context
    if targets:
        for uid, lines in targets.items():
            if lines:
                blocks.append("Recent messages from the referenced user:\n" + "\n".join(lines))

    header = ("\n\n".join(blocks) + "\n\n") if blocks else ""
    return preface + header + "Recent tagged exchange (if any):\n" + recent_turns + "\nAssistant:"


def get_response_from_ai(prompt: str) -> str:
    return generate_text(prompt, system_instruction="Limit response 2000 chars") or "(no content)"

async def safe_send(channel, text: str):
    for chunk in wrap(text, 2000, replace_whitespace=False, drop_whitespace=False):
        await channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())

# Optional: let users store long-term facts
@bot.command(name="remember")
async def remember(ctx, *, fact: str):
    cleaned = fact.strip()
    if not cleaned:
        await ctx.reply("Please provide a fact to remember.")
        return
    memory.add_fact(ctx.author.id, cleaned)
    await ctx.reply("Noted. I'll remember that.")

@bot.command(name="remember_team")
async def remember_team(ctx, *, fact: str):
    if not ctx.guild:
        await ctx.reply("Team facts can only be saved in a server.")
        return
    cleaned = fact.strip()
    if not cleaned:
        await ctx.reply("Please provide a team fact to remember.")
        return
    memory.add_team_fact(ctx.guild.id, cleaned)
    await ctx.reply("Got it. I'll remember this for the team.")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.event
async def on_message(message: discord.Message):
    log_message(message)
    if message.author == bot.user:
        return
    try:
        if not message.author.bot:
            memory.record_message_for_facts(
                message.author.id,
                message.guild.id if message.guild else None,
                message.content,
                batch_size=30,
            )
    except Exception:
        pass

    if bot.user in message.mentions:
        key = await conversation_key(message)
        memory.add_turn(key, "user", message.content)

        # summarize if large
        thread = memory.get_thread(key)
        joined = "\n".join(f"{t['role']}: {t['text']}" for t in thread["turns"])
        if len(joined) > memory.max_chars:
            s = summarize(joined, limit=800)
            memory.save_thread(key, {"summary": s})
            thread = memory.get_thread(key)

        # Ambient channel/thread context (untagged)
        ambient = fetch_recent_history_for_scope(message, limit=60, minutes=240)

        # NEW: pull context for any other @mentions (besides the bot)
        targets: dict[int, list[str]] = {}
        other_mentions = [u for u in message.mentions if u.id != bot.user.id]
        for u in other_mentions:
            # first try same channel/thread
            lines = fetch_user_recent_in_channel(message.channel.id, u.id, minutes=720, limit=60)
            # if none found and we’re in a guild, search server-wide
            if not lines and message.guild:
                lines = fetch_user_recent_in_guild(message.guild.id, u.id, minutes=720, limit=100)
            targets[u.id] = lines

        prompt = build_prompt(message.author.id, thread, message.guild, ambient, targets)

        try:
            reply = get_response_from_ai(prompt)
        except Exception:
            reply = ("I'm having trouble reaching the model right now. "
                     "Please try again in a moment.")

        if reply:
            memory.add_turn(key, "assistant", reply)
            await safe_send(message.channel, reply)

    await bot.process_commands(message)
    
