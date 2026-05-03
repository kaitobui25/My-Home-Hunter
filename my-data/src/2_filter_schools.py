import sys
import os
import json
import yaml

# Add project root to sys.path to reuse the GeocoderService
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(PROJECT_ROOT)

from src.geocoder import GeocoderService

CONFIG_FILE = os.path.join(PROJECT_ROOT, 'config.yaml')
JSON_FILE = os.path.join(os.path.dirname(__file__), '..', 'hoikuen', 'schools_geocoded.json')

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    # Read config.yaml directly to extract coordinates
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config_data = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Error: {CONFIG_FILE} not found.")
        return
        
    loc_filter = config_data.get('filters', {}).get('location_filter', {})
    center_lat = loc_filter.get('center_lat')
    center_lng = loc_filter.get('center_lng')
    max_dist = loc_filter.get('max_distance_km', 2.0)
    
    if not center_lat or not center_lng:
        print("Error: center_lat or center_lng not found in config.yaml under filters.location_filter")
        return
        
    print(f"Center coordinates : ({center_lat}, {center_lng})")
    print(f"Search radius      : {max_dist} km")
    
    try:
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            schools = json.load(f)
    except FileNotFoundError:
        print(f"Error: {JSON_FILE} not found. Please run 1_extract_geocode.py first.")
        return
        
    os.chdir(PROJECT_ROOT)
    geocoder = GeocoderService()
    matched = []
    
    for school in schools:
        lat = school.get('lat')
        lng = school.get('lng')
        
        if lat is None or lng is None:
            continue
            
        dist = geocoder.calculate_distance(lat, lng, center_lat, center_lng)
        
        if dist <= max_dist:
            school['distance'] = dist
            matched.append(school)
            
    # Sort schools by distance (closest first)
    matched.sort(key=lambda x: x['distance'])
    
    print(f"\n=> FOUND {len(matched)} SCHOOLS WITHIN {max_dist} KM:")
    print("=" * 70)
    for s in matched:
        print(f"[{s['distance']:.2f} km] {s['name']}")
        print(f"    {s['address']}")
        print("-" * 70)

if __name__ == "__main__":
    main()
