import sys
import os
import json
import time
import re

# Add project root to sys.path to reuse the GeocoderService
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(PROJECT_ROOT)

from src.geocoder import GeocoderService

TXT_FILE = os.path.join(os.path.dirname(__file__), '..', 'hoikuen', '2026-05-02-list-ninkagai-yodo.txt')
OUTPUT_JSON = os.path.join(os.path.dirname(__file__), '..', 'hoikuen', 'schools_geocoded.json')

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    # We change working dir to project root so the cache file is saved in results/geocode_cache.json
    os.chdir(PROJECT_ROOT)
    geocoder = GeocoderService()
    schools = []
    
    print(f"Reading from: {TXT_FILE}")
    try:
        with open(TXT_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"File not found: {TXT_FILE}")
        return
        
    # Skip the first two header lines
    for line in lines[2:]:
        line = line.strip()
        if not line:
            continue
            
        parts = line.split('\t')
        if len(parts) >= 4:
            name = parts[2].strip()
            raw_address = parts[3].strip()
            
            # Clean up the address (remove building names after space)
            clean_addr = raw_address.split(' ')[0]
            
            # Add "大阪府" prefix if missing to help Nominatim understand it's in Osaka Prefecture
            if clean_addr.startswith("大阪市"):
                clean_addr = "大阪府" + clean_addr

            # Attempt 1: Full cleaned address
            lat, lng = geocoder.get_coordinates(clean_addr)
            
            # Attempt 2: Fallback loop, remove the last segment after hyphen (e.g. 2-5-24 -> 2-5)
            fallback_addr = clean_addr
            while not lat and not lng and '-' in fallback_addr:
                fallback_addr = fallback_addr.rsplit('-', 1)[0]
                lat, lng = geocoder.get_coordinates(fallback_addr)
                
            # Attempt 3: If still not found, try to strip all numbers/hyphens to just get the town name
            if not lat and not lng:
                match = re.match(r'([^0-9]+)', clean_addr)
                if match:
                    base_addr = match.group(1)
                    lat, lng = geocoder.get_coordinates(base_addr)
            
            if lat and lng:
                print(f"[OK] {name} -> ({lat:.4f}, {lng:.4f})")
            else:
                print(f"[FAILED] {name} -> Address not found: {clean_addr}")
            
            schools.append({
                "name": name,
                "address": raw_address,
                "lat": lat,
                "lng": lng
            })
            
    out_dir = os.path.dirname(OUTPUT_JSON)
    os.makedirs(out_dir, exist_ok=True)
    
    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(schools, f, ensure_ascii=False, indent=2)
        
    print(f"\nDone! Saved {len(schools)} schools to {OUTPUT_JSON}")

if __name__ == "__main__":
    main()
