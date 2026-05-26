import json
import httpx
from bs4 import BeautifulSoup
import re
import sys
import base64
import urllib.parse
from typing import List, Dict, Any

# Asegurar encoding UTF-8 para stdout en consolas de Windows
if sys.stdout and sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Configuración del scraper
TARGET_DOMAIN = "https://futbollibretv.mx"
AGENDA_JSON_URL = "https://fubolazo.com/agenda.json?v=1.07"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Referer': f'{TARGET_DOMAIN}/',
    'Origin': TARGET_DOMAIN
}

def clean_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return f"https:{url}"
    return url

def clean_title(desc: str) -> str:
    # Remove prefix like "NBA: " or "LaLiga: " if present
    if ":" in desc:
        parts = desc.split(":", 1)
        # If the second part has "vs" or "-", use it
        if " vs " in parts[1] or " - " in parts[1]:
            desc = parts[1]
    
    desc = desc.strip()
    if " - " in desc and " vs " not in desc:
        desc = desc.replace(" - ", " vs ")
    return desc

def decode_iframe_url(iframe: str) -> str:
    if not iframe:
        return ""
    # Extract parameter 'r' (base64 URL)
    match = re.search(r'[?&]r=([^&]+)', iframe)
    if match:
        b64_str = match.group(1)
        try:
            # Pad base64 if needed
            b64_str += '=' * ((4 - len(b64_str) % 4) % 4)
            decoded = base64.b64decode(b64_str).decode('utf-8', errors='ignore')
            return decoded
        except Exception:
            pass
    if iframe.startswith('http'):
        return iframe
    return f"{TARGET_DOMAIN}{iframe}"

def fetch_from_agenda_json() -> List[Dict[str, Any]]:
    print("📡 Intentando descargar agenda JSON...")
    response = httpx.get(AGENDA_JSON_URL, headers=HEADERS, timeout=15.0)
    if response.status_code != 200:
        raise Exception(f"Error HTTP {response.status_code} al descargar JSON")
    
    data = response.json()
    raw_matches = data.get('data', []) if isinstance(data, dict) else []
    
    matches = []
    for item in raw_matches:
        attributes = item.get('attributes', {})
        if not attributes:
            continue
            
        desc = attributes.get('diary_description', '').strip()
        if not desc:
            continue
            
        title = clean_title(desc)
        status = attributes.get('diary_hour', 'LIVE')
        if status and len(status) > 5:
            # Format HH:MM:SS to HH:MM
            status = status[:5]
            
        # Extract tournament name
        country_data = attributes.get('country', {}).get('data', {})
        tournament = "Fútbol en Vivo"
        if country_data:
            country_attr = country_data.get('attributes', {})
            if country_attr:
                tournament = country_attr.get('name', 'Fútbol en Vivo')
                
        # Extract embeds
        embeds_data = attributes.get('embeds', {}).get('data', [])
        links = []
        for idx, emb in enumerate(embeds_data):
            emb_attr = emb.get('attributes', {})
            if not emb_attr:
                continue
            name = emb_attr.get('embed_name') or f"Opción {idx + 1}"
            iframe = emb_attr.get('embed_iframe', '')
            stream_url = decode_iframe_url(iframe)
            if stream_url:
                links.append({
                    "name": name,
                    "url": clean_url(stream_url)
                })
                
        if links:
            matches.append({
                "title": title,
                "tournament": tournament,
                "status": status,
                "homeLogo": None,
                "awayLogo": None,
                "links": links
            })
            
    return matches

def fetch_from_html_scraping() -> List[Dict[str, Any]]:
    print("📡 Fallback: Raspando HTML de futbol-libre...")
    response = httpx.get(TARGET_DOMAIN, headers=HEADERS, timeout=15.0, follow_redirects=True)
    if response.status_code != 200:
        raise Exception(f"Error HTTP {response.status_code} al raspar HTML")
        
    soup = BeautifulSoup(response.text, 'html.parser')
    matches = []
    
    match_links = soup.find_all('a', href=re.compile(r'/partido/|/stream/|/watch/'))
    if not match_links:
        match_links = soup.find_all(class_=re.compile(r'partido|match|agenda-item', re.IGNORECASE))
        
    for el in match_links:
        href = el.get('href', '') if el.name == 'a' else ''
        if href and not href.startswith('http'):
            href = f"{TARGET_DOMAIN}{href}"
            
        text = el.get_text(separator=" ").strip()
        title = text
        tournament = "Fútbol en Vivo"
        status = "LIVE"
        
        tournament_el = el.find(class_=re.compile(r'tournament|liga|league', re.IGNORECASE))
        if tournament_el:
            tournament = tournament_el.text.strip()
            
        status_el = el.find(class_=re.compile(r'status|time|hora', re.IGNORECASE))
        if status_el:
            status = status_el.text.strip()
            
        title_lines = [line.strip() for line in text.split('\n') if line.strip()]
        if len(title_lines) >= 2:
            title = " vs ".join(title_lines[:2])
        
        if href:
            matches.append({
                "title": title,
                "tournament": tournament,
                "status": status,
                "homeLogo": None,
                "awayLogo": None,
                "links": [
                    {"name": "Ver Transmisión (Señal Principal)", "url": href}
                ]
            })
    return matches

def main():
    matches = []
    try:
        matches = fetch_from_agenda_json()
        print(f"✅ Se cargaron {len(matches)} partidos desde agenda JSON.")
    except Exception as e:
        print(f"⚠️ Error cargando JSON: {e}")
        try:
            matches = fetch_from_html_scraping()
            print(f"✅ Se cargaron {len(matches)} partidos desde raspado HTML.")
        except Exception as html_err:
            print(f"❌ Error crítico al raspar HTML: {html_err}")
            return

    # Guardar localmente como matches.json
    try:
        with open('matches.json', 'w', encoding='utf-8') as f:
            json.dump(matches, f, ensure_ascii=False, indent=2)
        print("🎉 Archivo matches.json creado y guardado con éxito localmente.")
    except Exception as e:
        print(f"❌ Error al escribir archivo JSON: {e}")

if __name__ == "__main__":
    main()
