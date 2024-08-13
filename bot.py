from pyrogram import Client, filters
import requests
import base64
import json
import time
from config import API_ID, API_HASH, BOT_TOKEN, OWNER, REPO, PATH, MESSAGE, GIT_TOKEN, PLATFORMS

# Initialize the bot with your API keys
app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

def fetch_with_retry(url, headers=None, max_retries=3, delay=1):
    retries = 0
    while retries < max_retries:
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Error fetching {url}: {e}")
            retries += 1
            if retries >= max_retries:
                raise
            print(f"Retrying in {delay} seconds...")
            time.sleep(delay)
            delay *= 2  # Exponential backoff

def fetch_folders(api_key, platform):
    url = PLATFORMS.get(platform)
    if not url:
        raise ValueError(f"Unsupported platform: {platform}")
    
    data = fetch_with_retry(url)
    
    if platform == 'MixDrop':
        return data.get('result', {}).get('folders', [])
    
    # Handling Filemoon and other platforms
    result = data.get('result', {})
    if platform in ['Filemoon', 'VidHide', 'StreamWish', 'DoodStream']:
        if 'folders' in result:
            return result['folders']
        elif isinstance(result, list):
            return result
    return []

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
    try:
        await fetch_anime_data(message)
    except Exception as e:
        await message.reply_text(f"Update failed: {str(e)}")


@app.on_message(filters.command("check"))
async def check_data(client, message):
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
    await message.reply_text("Welcome to the Anime Data Bot! Use the following commands:\n"
                             "/update - Update the anime data\n"
                             "/check - Check the current anime data\n"
                             "/combiner - Placeholder for future functionality")

@app.on_message(filters.command("combiner"))
async def combiner(client, message):
    await message.reply_text("Combiner command is a placeholder. Implement your logic here.")

def split_message(message, chunk_size=4096):
    """Splits a message into chunks of a specified size."""
    return [message[i:i+chunk_size] for i in range(0, len(message), chunk_size)]

if __name__ == "__main__":
    app.run()

