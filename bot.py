import io
import discord
from discord import File, Embed
from discord.ext import commands
from google import genai
from google.genai import types

from memory import Memory
from textwrap import wrap
import logger #part of local py files





#THIS NEEDS TO BE CHANGED!!
from DONT_COMMIT_apis import YOUR_API_KEY





intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True


bot = commands.Bot(command_prefix="!", intents=intents)
memory = Memory(max_chars=6000)  # creates memory.sqlite3 by default for AI interractions




client = genai.Client(api_key = YOUR_API_KEY)
MODEL = "gemini-2.5-flash"

def summarize(text: str, limit=800):
    resp = client.models.generate_content(
        model=MODEL,
        contents=(
            "Summarize the following conversation into compact notes "
            f"(<= {limit} characters). Keep user goals/preferences and unresolved tasks.\n\n{text}"
        )
    )
    return (resp.text or "")[:limit]








def build_prompt(user_id: int, thread):
    facts = memory.get_facts(user_id)
    recent = "\n".join(f"{t['role'].capitalize()}: {t['text']}" for t in thread["turns"])
    preface = "You are a helpful Discord assistant. Be concise.\n\n"
    profile = ("Known user facts:\n- " + "\n- ".join(facts) + "\n\n") if facts else ""
    summary = (f"Conversation summary so far:\n{thread['summary']}\n\n") if thread.get("summary") else ""
    return preface + profile + summary + "Recent messages:\n" + recent + "\nAssistant:"

def get_response_from_ai(prompt: str) -> str:
    resp = client.models.generate_content(
        model=MODEL,
        config=types.GenerateContentConfig(system_instruction="Limit response 2000 chars"),
        contents=prompt
    )
    return resp.text or "(no content)"

async def safe_send(channel, text: str):
    for chunk in wrap(text, 2000, replace_whitespace=False, drop_whitespace=False):
        await channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())

# Optional: let users store long-term facts
@bot.command(name="remember")
async def remember(ctx, *, fact: str):
    memory.add_fact(ctx.author.id, fact)
    await ctx.reply("Noted. I’ll remember that.")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.event
async def on_message(message: discord.Message):
    
    #adds message to the discord_logs.db
    logger.log_message(message)
    
    if message.author == bot.user:
        return

    # Only respond when bot is tagged
    if bot.user in message.mentions:
        key = memory._key(message)
        # store user turn
        memory.add_turn(key, "user", message.content)

        # build context-aware prompt from DB
        thread = memory.get_thread(key)
        prompt = build_prompt(message.author.id, thread)

        # call Gemini
        reply = get_response_from_ai(prompt)

        # store assistant turn
        memory.add_turn(key, "assistant", reply)

        # send safely under 2000 chars
        await safe_send(message.channel, reply)

    await bot.process_commands(message)






'''

#Initializing GEMINI AI API
client = genai.Client(api_key=YOUR_API_KEY)

#Getting responses from AI
def get_response_from_ai(prompt):

    response = client.models.generate_content(
                model="gemini-2.5-flash",
                config=types.GenerateContentConfig(
        system_instruction="Limit response 2000 chars"), # limits response to 2000 chars
                  contents= prompt
            )
    
    return response.text


#sends normal messages 
async def send_to_discord(channel, text: str):
   
    await channel.send(text)
    
    

#BOT EVENTS!

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


#Whenever a message is sent
@bot.event
async def on_message(message):
    # Ignore messages from the bot itself
    if message.author == bot.user:
        return
    
    if message.content:

        if bot.user in message.mentions:

            from_AI = get_response_from_ai(message.content)
        
            await send_to_discord(message.channel, from_AI)


'''