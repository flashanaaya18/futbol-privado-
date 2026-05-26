import json
import httpx
from bs4 import BeautifulSoup
import re
import sys
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

# Ensure UTF-8 stdout on Windows consoles
if sys.stdout and sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

HOMEPAGE_URL = 'https://www.cablevisionhd.com/'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
}

def parse_channel_catalog() -> List[Dict[str, str]]:
    print(f"📡 Fetching homepage: {HOMEPAGE_URL}")
    try:
        r = httpx.get(HOMEPAGE_URL, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            print(f"❌ Failed to fetch homepage, status: {r.status_code}")
            return []
    except Exception as e:
        print(f"❌ Error fetching homepage: {e}")
        return []

    soup = BeautifulSoup(r.text, 'html.parser')
    home_channels_html = ""
    show_channels_html = ""

    # Find the scripts containing homeChannels and showChannels
    for s in soup.find_all('script'):
        text = s.string or ""
        if 'homeChannels' in text:
            home_match = re.search(r'const\s+homeChannels\s*=\s*`([^`]+)`', text)
            if home_match:
                home_channels_html = home_match.group(1)
            
            show_match = re.search(r'const\s+showChannels\s*=\s*`([^`]+)`', text)
            if show_match:
                show_channels_html = show_match.group(1)

    channels = []
    for html_snippet, group_name in [(home_channels_html, 'home'), (show_channels_html, 'shows')]:
        if not html_snippet:
            continue
        sub_soup = BeautifulSoup(html_snippet, 'html.parser')
        for a in sub_soup.find_all('a'):
            href = a.get('href')
            if not href or 'linktre.online' in href:
                continue

            img = a.find('img')
            p = a.find('p')

            name = p.text.strip() if p else (img.get('alt', '').strip() if img else 'Unknown')
            logo = img.get('src', '') if img else ''

            # Normalize URLs
            if logo and not logo.startswith('http'):
                if logo.startswith('/'):
                    logo = 'https://www.cablevisionhd.com' + logo
                else:
                    logo = 'https://www.cablevisionhd.com/' + logo

            if href.startswith('/'):
                href = 'https://www.cablevisionhd.com' + href
            elif not href.startswith('http'):
                href = 'https://www.cablevisionhd.com/' + href

            channels.append({
                'name': name,
                'url': href,
                'logo': logo,
                'group': group_name
            })
            
    print(f"✅ Found {len(channels)} channels in homepage catalog.")
    return channels

def resolve_m3u8_for_channel(channel: Dict[str, str]) -> Dict[str, Any]:
    name = channel['name']
    url = channel['url']
    logo = channel['logo']
    group = channel['group']
    
    print(f"🔍 Resolving stream for: {name}")
    client = httpx.Client(headers=HEADERS, timeout=12.0)
    
    try:
        # Step 1: Fetch channel page
        r = client.get(url)
        if r.status_code != 200:
            return {'name': name, 'url': url, 'logo': logo, 'group': group, 'm3u8': None, 'error': f'Channel page HTTP {r.status_code}'}
            
        soup = BeautifulSoup(r.text, 'html.parser')
        iframe_srcs = [i.get('src') for i in soup.find_all('iframe') if i.get('src')]
        
        # Look for the internal stream/ page
        stream_iframe_url = None
        for src in iframe_srcs:
            if 'stream/' in src:
                if src.startswith('/'):
                    stream_iframe_url = 'https://www.cablevisionhd.com' + src
                elif src.startswith('http'):
                    stream_iframe_url = src
                else:
                    stream_iframe_url = 'https://www.cablevisionhd.com/' + src
                break
                
        if not stream_iframe_url:
            for src in iframe_srcs:
                if 'cablevisionhd.com' in src:
                    stream_iframe_url = src
                    break
                    
        if not stream_iframe_url:
            return {'name': name, 'url': url, 'logo': logo, 'group': group, 'm3u8': None, 'error': 'No stream iframe found'}

        # Step 2: Fetch the stream iframe
        r2 = client.get(stream_iframe_url, headers={'Referer': url})
        if r2.status_code != 200:
            return {'name': name, 'url': url, 'logo': logo, 'group': group, 'm3u8': None, 'error': f'Stream iframe HTTP {r2.status_code}'}
            
        soup2 = BeautifulSoup(r2.text, 'html.parser')
        deportes_srcs = [i.get('src') for i in soup2.find_all('iframe') if i.get('src')]
        
        deportes_url = None
        for src in deportes_srcs:
            if 'deportes.ksdjugfsddeports.com' in src or 'stream.php' in src:
                deportes_url = src
                break
                
        if not deportes_url:
            return {'name': name, 'url': url, 'logo': logo, 'group': group, 'm3u8': None, 'error': 'No Deportes iframe found'}

        # Step 3: Fetch Deportes iframe and get 302 redirect location
        # Must supply Origin and Referer
        dep_headers = {
            'Referer': stream_iframe_url,
            'Origin': 'https://www.cablevisionhd.com'
        }
        r3 = client.get(deportes_url, headers=dep_headers, follow_redirects=False)
        if r3.status_code not in (301, 302, 303, 307, 308):
            return {'name': name, 'url': url, 'logo': logo, 'group': group, 'm3u8': None, 'error': f'Deportes redirect HTTP {r3.status_code}'}
            
        redirect_path = r3.headers.get('Location')
        if not redirect_path:
            return {'name': name, 'url': url, 'logo': logo, 'group': group, 'm3u8': None, 'error': 'No redirect Location header'}
            
        if redirect_path.startswith('/'):
            parsed = urlparse(deportes_url)
            redirect_url = f"{parsed.scheme}://{parsed.netloc}{redirect_path}"
        else:
            redirect_url = redirect_path

        # Step 4: Fetch final player page with referer (use stream_iframe_url or HOMEPAGE_URL)
        player_headers = {
            'Referer': stream_iframe_url,
            'Origin': 'https://www.cablevisionhd.com'
        }
        r4 = client.get(redirect_url, headers=player_headers)
        if r4.status_code != 200:
            return {'name': name, 'url': url, 'logo': logo, 'group': group, 'm3u8': None, 'error': f'Player page HTTP {r4.status_code}'}

        # Step 5: Extract the m3u8 stream from scripts
        match = re.search(r'var\s+src\s*=\s*"([^"]+)"', r4.text)
        if not match:
            match = re.search(r'src\s*:\s*"([^"]+)"', r4.text)
        if not match:
            match = re.search(r'"(https?://[^"]+\.m3u8[^"]*)"', r4.text)
            
        if match:
            m3u8 = match.group(1).replace(r'\/', '/')
            print(f"✅ Resolved {name} -> {m3u8[:70]}...")
            return {'name': name, 'url': url, 'logo': logo, 'group': group, 'm3u8': m3u8}
        else:
            return {'name': name, 'url': url, 'logo': logo, 'group': group, 'm3u8': None, 'error': 'm3u8 not found in player page HTML'}
            
    except Exception as e:
        return {'name': name, 'url': url, 'logo': logo, 'group': group, 'm3u8': None, 'error': str(e)}
    finally:
        client.close()

def main():
    channels = parse_channel_catalog()
    if not channels:
        print("❌ No channels found to resolve.")
        return

    resolved_channels = []
    
    # We resolve channels in parallel using ThreadPoolExecutor
    print(f"🚀 Starting parallel resolution using 8 threads...")
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(resolve_m3u8_for_channel, ch): ch for ch in channels}
        for future in as_completed(futures):
            res = future.result()
            resolved_channels.append(res)
            
    # Count success rate
    success_count = sum(1 for ch in resolved_channels if ch['m3u8'])
    print(f"\n📊 Resolution complete: {success_count}/{len(channels)} channels resolved successfully.")

    # Sort channels by their original index/group to keep ordering clean
    # Let's group them or keep the original order
    # To preserve the original catalog order, we can map the parsed catalog names
    name_order = {ch['name']: idx for idx, ch in enumerate(channels)}
    resolved_channels.sort(key=lambda x: name_order.get(x['name'], 999))

    # Save to JSON
    json_path = 'cablevision.json'
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(resolved_channels, f, ensure_ascii=False, indent=2)
        print(f"🎉 Saved JSON data to {json_path}")
    except Exception as e:
        print(f"❌ Error saving JSON file: {e}")

    # Save to IPTV M3U Playlist
    m3u_path = 'cablevision.m3u'
    try:
        with open(m3u_path, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            for ch in resolved_channels:
                if not ch['m3u8']:
                    continue
                name = ch['name']
                logo = ch['logo']
                m3u8_url = ch['m3u8']
                group = "Cablevision Live" if ch['group'] == 'home' else "Cablevision Events"
                
                # Format: #EXTINF:-1 tvg-logo="LOGO_URL" group-title="GROUP",CHANNEL_NAME
                f.write(f'#EXTINF:-1 tvg-logo="{logo}" group-title="{group}",{name}\n')
                f.write(f'{m3u8_url}\n')
        print(f"🎉 Saved IPTV playlist to {m3u_path}")
    except Exception as e:
        print(f"❌ Error saving M3U file: {e}")

if __name__ == "__main__":
    main()
