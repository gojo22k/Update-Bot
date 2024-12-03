from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
import re
import html
import requests
import base64
import json
from config import API_ID, API_HASH, BOT_TOKEN, OWNER, REPO, PATH, MESSAGE, GIT_TOKEN, PLATFORMS

OWNER_ID = 1740287480  # Directly use the owner ID

# Initialize the bot with your API keys
app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_modes = {}
user_states = {}

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b'OK')

def run_health_check_server():
    server_address = ('', 8000)
    httpd = HTTPServer(server_address, HealthCheckHandler)
    httpd.serve_forever()

import time

def fetch_with_retry(url, headers=None, max_retries=3, delay=1, max_rate_limit_retries=10):
    retries = 0
    rate_limit_retries = 0
    
    while retries < max_retries:
        try:
            response = requests.get(url, headers=headers)
            
            # Check if the response status code indicates rate limiting
            if response.status_code == 429:
                if rate_limit_retries < max_rate_limit_retries:
                    rate_limit_retries += 1
                    wait_time = response.headers.get('Retry-After', delay)
                    print(f"Rate limit exceeded. Retrying in {wait_time} seconds...")
                    time.sleep(int(wait_time))  # Wait according to Retry-After header or default delay
                    continue
                else:
                    raise Exception(f"Rate limit exceeded and max retries reached for {url}")
            
            # Check for other response errors
            response.raise_for_status()
            
            return response.json()
        
        except requests.RequestException as e:
            print(f"Error fetching {url}: {e}")
            retries += 1
            if retries >= max_retries:
                raise
            print(f"Retrying in {delay} seconds...")
            time.sleep(delay)  # Wait before retrying
            delay *= 2  # Exponential backoff


def fetch_folders(api_key, platform):
    url = PLATFORMS.get(platform)
    if not url:
        raise ValueError(f"Unsupported platform: {platform}")
    
    data = fetch_with_retry(url)
    
    if platform == 'MixDrop':
        folders = data.get('result', {}).get('folders', [])
    else:
        # Handling Filemoon and other platforms
        result = data.get('result', {})
        if platform in ['Filemoon', 'VidHide', 'StreamWish', 'DoodStream']:
            if 'folders' in result:
                folders = result['folders']
            elif isinstance(result, list):
                folders = result
            else:
                folders = []

    # Decode folder names to correct HTML entities
    for folder in folders:
        if 'name' in folder:
            folder['name'] = html.unescape(folder['name'])
        if 'title' in folder:  # For platforms that use 'title' instead of 'name'
            folder['title'] = html.unescape(folder['title'])
    
    return folders

def fetch_anime_details_from_jikan(anime_name):
    url = f'https://api.jikan.moe/v4/anime?q={requests.utils.quote(anime_name)}&limit=1'
    data = fetch_with_retry(url)
    if data.get('data'):
        anime_data = data['data'][0]
        return {
            'genres': ', '.join(genre['name'] for genre in anime_data.get('genres', [])),
            'type': anime_data.get('type', ''),
            'total_episodes': anime_data.get('episodes', 0),
            'score': anime_data.get('score', 0),
            'status': anime_data.get('status', ''),
            'pg_rating': anime_data.get('rating', '')
        }
    return None

def update_github_file(token, owner, repo, path, message, content, sha):
    url = f'https://api.github.com/repos/{owner}/{repo}/contents/{path}'
    headers = {
        'Authorization': f'token {token}',
        'Content-Type': 'application/json'
    }
    data = {
        'message': message,
        'content': base64.b64encode(content.encode('utf-8')).decode('utf-8'),
        'sha': sha
    }
    response = requests.put(url, headers=headers, data=json.dumps(data))
    response.raise_for_status()
    return response.json()

def check_initial_conditions():
    """Check if the bot has all required configurations before starting the update."""
    errors = []

    # Check GitHub token
    if not GIT_TOKEN:
        errors.append("GitHub token is missing.")

    # Check repository details
    if not OWNER or not REPO or not PATH:
        errors.append("GitHub repository details are incomplete.")

    # Check platform URLs
    for platform, url in PLATFORMS.items():
        if not url:
            errors.append(f"API URL for {platform} is missing or invalid.")
    
    return errors

async def fetch_anime_data(message):
    platforms = [
        {'name': 'MixDrop', 'api_key': PLATFORMS['MixDrop'].split('key=')[1].split('&')[0]},
        {'name': 'Filemoon', 'api_key': PLATFORMS['Filemoon'].split('key=')[1].split('&')[0]},
        {'name': 'VidHide', 'api_key': PLATFORMS['VidHide'].split('key=')[1].split('&')[0]},
        {'name': 'StreamWish', 'api_key': PLATFORMS['StreamWish'].split('key=')[1].split('&')[0]},
        {'name': 'DoodStream', 'api_key': PLATFORMS['DoodStream'].split('key=')[1].split('&')[0]}
    ]

    anime_data = []

    for platform in platforms:
        try:
            folders = fetch_folders(platform['api_key'], platform['name'])
            for folder in folders:
                folder_id = folder.get('fld_id') or folder.get('id')
                folder_name = folder.get('name') or folder.get('title')
                if folder_name and folder_id:
                    anime_details = fetch_anime_details_from_jikan(folder_name)
                    if anime_details:
                        anime_entry = {
                            'id': folder_id,
                            'name': folder_name,
                            'genres': anime_details['genres'],
                            'type': anime_details['type'],
                            'starting_letter': folder_name[0].upper(),
                            'cloud': platform['name'],
                            'pg_rating': anime_details['pg_rating'],
                            'score': anime_details['score'],
                            'status': anime_details['status'],
                            'total_episodes': anime_details['total_episodes']
                        }
                        # Send a real-time success message to Telegram
                        await message.reply_text(f"Successfully updated {folder_name} from {platform['name']}")
                        anime_data.append(anime_entry)
        except Exception as e:
            print(f"Error processing platform {platform['name']}: {e}")
            await message.reply_text(f"Failed to update {platform['name']}: {str(e)}")

    if anime_data:
        content = json.dumps(anime_data, indent=4)
        file_response = requests.get(f'https://api.github.com/repos/{OWNER}/{REPO}/contents/{PATH}', headers={'Authorization': f'token {GIT_TOKEN}'})
        file_response.raise_for_status()
        file_sha = file_response.json()['sha']
        
        update_response = update_github_file(GIT_TOKEN, OWNER, REPO, PATH, MESSAGE, content, file_sha)
        await message.reply_text(f"Successfully updated {PATH} on GitHub.")
    else:
        await message.reply_text("No anime data to update.")

@app.on_message(filters.command("update"))
async def update_file(client, message):
    if message.from_user.id != OWNER_ID:
        await message.reply_text("Unauthorized access.")
        return

    # Run initial checks
    errors = check_initial_conditions()
    if errors:
        error_message = "The following errors were detected:\n" + "\n".join(f"- {error}" for error in errors)
        await message.reply_text(error_message)
        return

    try:
        await fetch_anime_data(message)
    except Exception as e:
        await message.reply_text(f"Update failed: {str(e)}")

@app.on_message(filters.command("check"))
async def check_data(client, message):
    if message.from_user.id != OWNER_ID:
        await message.reply_text("Unauthorized access.")
        return
    chat_id = message.chat.id
    try:
        get_file_response = requests.get(f'https://raw.githubusercontent.com/{OWNER}/{REPO}/main/{PATH}')
        get_file_response.raise_for_status()
        file_data = get_file_response.json()

        if not file_data:
            await message.reply_text("No data found.")
            return

        response_message = "Current Anime Data:\n"
        for index, anime in enumerate(file_data, start=1):
            response_message += f"{index}. {anime['name']} (ID: {anime['id']})\n"

        # Send response message in chunks
        for chunk in split_message(response_message):
            await message.reply_text(chunk)
        
    except Exception as e:
        await message.reply_text(f"Fetch failed: {e}")

@app.on_message(filters.command("start"))
async def start(client, message):
    if message.from_user.id != OWNER_ID:
        await message.reply_text("Unauthorized access.")
        return
    await message.reply_text("Welcome to the Anime Data Bot! I am up and running. Use the following commands:\n"
                             "/update - Update the anime data\n"
                             "/check - Check the current anime data\n"
                             "/combiner - Placeholder for future functionality")

@app.on_message(filters.command("git"))
async def git_test(client, message):
    if message.from_user.id != OWNER_ID:
        await message.reply_text("Unauthorized access.")
        return

    test_url = "https://api.github.com/user"
    headers = {
        'Authorization': f'token {GIT_TOKEN}',
        'Content-Type': 'application/json'
    }

    try:
        response = requests.get(test_url, headers=headers)
        response.raise_for_status()
        user_info = response.json()
        response_message = f"Token is working! 🎉\n\nUser Info:\n\n" \
                           f"Username: {user_info.get('login')}\n" \
                           f"ID: {user_info.get('id')}\n" \
                           f"Public Repos: {user_info.get('public_repos')}\n" \
                           f"Followers: {user_info.get('followers')}\n" \
                           f"Following: {user_info.get('following')}\n"
        await message.reply_text(response_message)
    except requests.exceptions.HTTPError as e:
        error_message = f"GitHub API Error: {e.response.status_code} - {e.response.text}\n\n" \
                        f"Token Used: {GIT_TOKEN}"
        await message.reply_text(error_message)
    except Exception as e:
        await message.reply_text(f"An error occurred: {str(e)}\n\nToken Used: {GIT_TOKEN}")


@app.on_message(filters.command("combiner"))
async def combiner_command(client, message):
    user_id = message.from_user.id
    user_modes[user_id] = 1  # Set default mode to 1
    user_states[user_id] = "waiting_for_mode"  # Set state to wait for mode selection

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Mode 1", callback_data="mode_1")],
        [InlineKeyboardButton("Mode 2", callback_data="mode_2")]
    ])
    await message.reply_text("Choose a mode:", reply_markup=keyboard)

@app.on_callback_query(filters.regex(r"mode_(1|2)"))
async def handle_mode_change(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    mode = int(callback_query.data.split('_')[1])
    user_modes[user_id] = mode
    user_states[user_id] = "waiting_for_links"  # Update state to wait for links

    # Update the button text to reflect the new mode
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Mode {mode}", callback_data=f"mode_{mode}")],
        [InlineKeyboardButton(f"Switch to Mode {2 if mode == 1 else 1}", callback_data=f"mode_{2 if mode == 1 else 1}")]
    ])
    await callback_query.message.edit_text("Mode changed! Please send links in the format:\n\n`<your_message>`", reply_markup=keyboard)
    await callback_query.answer()

@app.on_message(filters.text)
async def handle_text_input(client, message):
    user_id = message.from_user.id
    
    if user_id in user_states and user_states[user_id] == "waiting_for_links":
        mode = user_modes[user_id]
        input_message = message.text
        links = []

        if mode == 1:
            regex = r"https?:\/\/[^\s]+"
            links = re.findall(regex, input_message)
        elif mode == 2:
            regex = r"📥\s*(?:Dᴏᴡɴʟᴏᴀᴅ|Download)\s*:\s*(https?:\/\/[^\s]+)"
            links = re.findall(regex, input_message)

        extracted_links = "\n".join(links)
        total_links = len(links)
        
        # Send extracted links to the user
        await message.reply_text(f"{extracted_links}")
        
        # Send the total link count in a separate message
        await message.reply_text(f"Total links: {total_links}")

        # Reset state after processing
        user_states[user_id] = "waiting_for_mode"
        
def split_message(message, chunk_size=4096):
    """Splits a message into chunks of a specified size."""
    return [message[i:i+chunk_size] for i in range(0, len(message), chunk_size)]

if __name__ == "__main__":
    # Start health check server in a separate thread
    health_check_thread = threading.Thread(target=run_health_check_server)
    health_check_thread.daemon = True
    health_check_thread.start()
    
    # Start the bot
    app.run()
