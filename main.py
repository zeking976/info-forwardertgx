import json
import os
import re
import logging
import sqlite3
import base64
import asyncio
from dotenv import load_dotenv
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import KeyboardButtonCallback, KeyboardButtonRow, ReplyInlineMarkup

load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
DEV_USER_ID = int(os.getenv('DEV_TELEGRAM_USER_ID'))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Move database to /tmp to avoid readonly root filesystem issues
db_path = '/tmp/bot.db'
db_dir = os.path.dirname(db_path) or '/tmp'
if not os.path.exists(db_path):
    with open(db_path, 'a'):  # Create file with write access
        pass
    os.chmod(db_path, 0o664)  # Read/write for owner and group
os.chmod(db_dir, 0o755)  # Ensure directory is writable
try:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users 
                   (user_id INTEGER PRIMARY KEY, encrypted_blob BLOB)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS config 
                   (key TEXT PRIMARY KEY, value TEXT)''')
    conn.commit()
except sqlite3.OperationalError as e:
    logger.error(f"Failed to initialize database at {db_path}: {e}")
    if os.path.exists(db_path):
        os.remove(db_path)
    with open(db_path, 'a'):
        pass
    os.chmod(db_path, 0o664)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users 
                   (user_id INTEGER PRIMARY KEY, encrypted_blob BLOB)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS config 
                   (key TEXT PRIMARY KEY, value TEXT)''')
    conn.commit()

def get_config(key):
    try:
        row = cur.execute('SELECT value FROM config WHERE key=?', (key,)).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError as e:
        logger.error(f"Error reading config: {e}")
        return None

def set_config(key, value):
    try:
        cur.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', (key, value))
        conn.commit()
    except sqlite3.OperationalError as e:
        logger.error(f"Error writing config: {e}")

# Default ca_filter to 'off' if not set
if get_config('ca_filter') is None:
    set_config('ca_filter', 'off')

bot_client = TelegramClient('bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

user_states = {}  # For configuration steps
user_running = {}  # For running clients: {user_id: {'client': client, 'target': target, 'user_channel': user_channel}}

def derive_key(password):
    salt = b'salt123'  # Fixed salt; in production, consider per-user random salt stored in db
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
        backend=default_backend()
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))

def extract_contract_address(msg):
    text = getattr(msg, 'text', '') or getattr(msg, 'message', '') or ''
    match = re.search(r'(?i)0x[a-f0-9]{40}', text)
    if match:
        return {'ca': match.group(0)}
    return None

async def ca_handler(event):
    """
    Optimized Telegram handler:
      - Processes only '🔥' messages
      - Extracts and logs only contract addresses (no full message)
      - Enqueues CA for processing
    """
    try:
        msg = event.message
        raw_text = getattr(event, 'raw_text', None) or getattr(msg, 'text', '') or getattr(msg, 'message', '') or ''
        cleaned = re.sub(r'^[\s\u200B\u200C\u200D\uFEFF]+', '', raw_text).strip() if raw_text else ''
        if not cleaned:
            return

        first_line = cleaned.splitlines()[0] if cleaned.splitlines() else cleaned
        first_char = first_line[0] if first_line else ''

        # Skip irrelevant messages
        if first_char in ('📈', '💰', '🏆'):
            return
        if not (first_line.startswith('🔥') or '🔥' in first_line):
            return

        # Try extracting CA
        res = extract_contract_address(msg)
        if not res or not res.get('ca'):
            return

        ca = res['ca']

        # ✅ Log only the essential info
        logger.info('🔥 CA: %s', ca)

    except Exception as e:
        logger.warning('Error in Telegram message handler: {e}')

@bot_client.on(events.NewMessage(pattern=r'^/start$'))
async def start_config(event):
    user_id = event.sender_id
    if user_id not in user_states or user_states[user_id].get('state') != 'waiting_target':
        user_states[user_id] = {'state': 'waiting_target', 'data': {}}
        await event.reply('Info Forwarder:\nPlease provide the target channel ID.')

@bot_client.on(events.NewMessage(func=lambda e: e.is_private and not e.message.message.startswith('/')))
async def handle_message(event):
    user_id = event.sender_id
    if user_id not in user_states or not isinstance(user_states[user_id], dict):
        return

    state = user_states[user_id]['state']
    data = user_states[user_id]['data']

    if state == 'waiting_target':
        if not event.text:  # Skip if no input yet
            return
        try:
            target = int(event.text)
            data['target'] = target
            user_states[user_id]['state'] = 'waiting_user_channel'
            await event.reply('Now provide your channel ID.')
        except ValueError:
            await event.reply('Invalid ID. Try again.')

    elif state == 'waiting_user_channel':
        if not event.text:  # Skip if no input yet
            return
        try:
            user_channel = int(event.text)
            data['user_channel'] = user_channel
            user_states[user_id]['state'] = 'waiting_password'
            await event.reply('Now provide a password for encryption.')
        except ValueError:
            await event.reply('Invalid ID. Try again.')

    elif state == 'waiting_password':
        password = event.text
        target = data.get('target')
        user_channel = data.get('user_channel')
        if target is None or user_channel is None:
            await event.reply('Configuration error. Please restart with /start.')
            del user_states[user_id]
            return
        data['password'] = password
        user_states[user_id]['state'] = 'waiting_session'
        await event.reply('Now upload the Telegram session file (must end with .session).')

    elif state == 'waiting_session':
        if not event.message.document:
            return
        file_name = event.message.document.attributes[0].file_name if event.message.document.attributes else ''
        if not file_name.endswith('.session'):
            await event.reply('File must end with .session.')
            return

        try:
            await event.message.download_media('temp.session')
            temp_client = TelegramClient('temp', API_ID, API_HASH)
            await temp_client.connect()
            if not await temp_client.is_user_authorized():
                await event.reply('Session not authorized.')
                os.remove('temp.session')
                del user_states[user_id]
                return

            # Save the session string directly
            session_str = temp_client.session.save()

            os.remove('temp.session')

            # Get data
            target = data.get('target')
            user_channel = data.get('user_channel')
            password = data.get('password')
            if any(x is None for x in [target, user_channel, password]):
                await event.reply('Configuration error. Please restart with /start.')
                del user_states[user_id]
                return

            # Encrypt data
            data_to_encrypt = {'target': target, 'user_channel': user_channel, 'session': session_str}
            json_bytes = json.dumps(data_to_encrypt).encode('utf-8')
            key = derive_key(password)
            f = Fernet(key)
            encrypted = f.encrypt(json_bytes)

            # Store
            cur.execute('INSERT OR REPLACE INTO users (user_id, encrypted_blob) VALUES (?, ?)', (user_id, encrypted))
            conn.commit()

            await event.reply('Configuration saved successfully. Use /start_forward <password> to start forwarding.')
            del user_states[user_id]

        except Exception as e:
            await event.reply(f'Error: {str(e)}. Please try again.')
            if os.path.exists('temp.session'):
                os.remove('temp.session')
            del user_states[user_id]

@bot_client.on(events.NewMessage(pattern=r'^/start_forward (.*)$'))
async def start_forward(event):
    user_id = event.sender_id
    password = event.pattern_match.group(1).strip()  # Extract and clean password
    logger.info(f"Attempting to start forwarding with password: {password}")  # Debug log

    row = cur.execute('SELECT encrypted_blob FROM users WHERE user_id=?', (user_id,)).fetchone()
    if not row:
        await event.reply('No configuration found. Use /start first.')
        return

    encrypted = row[0]
    key = derive_key(password)
    f = Fernet(key)
    try:
        decrypted = f.decrypt(encrypted)
        logger.info(f"Decrypted data successfully with password: {password}")  # Debug success
    except InvalidToken:
        logger.warning(f"Password mismatch for user {user_id} with input: {password}")
        await event.reply('Wrong password. Please ensure the password matches the one used during configuration and try again.')
        return

    try:
        data = json.loads(decrypted.decode('utf-8'))
        target = data['target']
        user_channel = data['user_channel']
        session_str = data['session']

        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise Exception('Session not authorized')
            await client.start()
        except Exception as e:
            logger.error(f"Client connection error for user {user_id}: {e}")
            await event.reply(f'Error connecting client: {str(e)}. Please reconfigure with /start.')
            return

        me = await client.get_me()
        try:
            perms = await client.get_permissions(user_channel, me)
            if not perms.post_messages:
                raise Exception('No post messages')
        except Exception:
            await bot_client.send_message(user_id, 'BOT IS NOT ADMIN IN YOUR CHANNEL/GROUP, ADD BOT AS ADMIN')
            await client.disconnect()
            return

        async def forward_handler(event):
            await client.forward_messages(user_channel, event.message)

        client.add_event_handler(forward_handler, events.NewMessage(chats=target))

        if user_id == DEV_USER_ID and get_config('ca_filter') == 'on':
            client.add_event_handler(ca_handler, events.NewMessage(chats=target))

        user_running[user_id] = {'client': client, 'target': target, 'user_channel': user_channel}
        await event.reply('Forwarding started.')
    except Exception as e:
        logger.error(f"Forwarding error for user {user_id}: {e}")
        await event.reply(f'Error starting forwarding: {str(e)}. Please try again.')
        if 'client' in locals():
            try:
                await client.disconnect()
            except Exception as disconnect_err:
                logger.error(f"Error during disconnect: {disconnect_err}")

@bot_client.on(events.NewMessage(pattern=r'^/stop_forward$'))
async def stop_forward(event):
    user_id = event.sender_id
    if user_id not in user_running:
        await event.reply('Forwarding not running.')
        return

    client = user_running[user_id]['client']
    try:
        await client.disconnect()
    except Exception as e:
        logger.error(f"Error stopping forward for user {user_id}: {e}")
    del user_running[user_id]
    await event.reply('Forwarding stopped.')

@bot_client.on(events.NewMessage(pattern=r'^/settings$'))
async def settings(event):
    user_id = event.sender_id
    if user_id != DEV_USER_ID:
        await event.reply('This command is restricted to the developer.')
        return

    current = get_config('ca_filter') or 'off'
    markup = ReplyInlineMarkup(
        rows=[
            KeyboardButtonRow(
                buttons=[
                    KeyboardButtonCallback(text=f'CA📃 filter: {current.upper()}', data=b'toggle_ca')
                ]
            )
        ]
    )
    await event.reply('Settings', reply_markup=markup)

@bot_client.on(events.NewMessage(pattern=r'^/delete_session$'))
async def delete_session(event):
    user_id = event.sender_id
    try:
        cur.execute('DELETE FROM users WHERE user_id=?', (user_id,))
        conn.commit()
        await event.reply('Session deleted successfully. Use /start to reconfigure.')
    except sqlite3.OperationalError as e:
        logger.error(f"Error deleting session for user {user_id}: {e}")
        await event.reply('Error deleting session. Please try again or check logs.')

@bot_client.on(events.CallbackQuery)
async def handle_callback(event):
    user_id = event.sender_id
    if user_id != DEV_USER_ID:
        return

    if event.data == b'toggle_ca':
        current = get_config('ca_filter') or 'off'
        new = 'off' if current == 'on' else 'on'
        set_config('ca_filter', new)

        markup = ReplyInlineMarkup(
            rows=[
                KeyboardButtonRow(
                    buttons=[
                        KeyboardButtonCallback(text=f'CA📃 filter: {new.upper()}', data=b'toggle_ca')
                    ]
                )
            ]
        )
        await event.edit('Settings', reply_markup=markup)

        # Handle running client
        if DEV_USER_ID in user_running:
            client = user_running[DEV_USER_ID]['client']
            if new == 'on':
                target = user_running[DEV_USER_ID]['target']
                client.add_event_handler(ca_handler, events.NewMessage(chats=target))
            else:
                client.remove_event_handler(ca_handler)

async def main():
    try:
        await bot_client.start(bot_token=BOT_TOKEN)
        print('Bot is running.')
        await bot_client.run_until_disconnected()
    except Exception as e:
        logger.error(f"Bot failed to start or run: {e}")

if __name__ == '__main__':
    asyncio.run(main())