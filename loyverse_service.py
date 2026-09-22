import os


from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import requests
from collections import defaultdict

app = Flask(__name__)
CORS(app)

API_TOKEN = "61da4f2b575e48a1a75d6efc239debd5"
BASE_URL = "https://api.loyverse.com/v1.0"
HEADERS = {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}

CACHED_VARIANT_MAP = {}
CATALOG_LOADED = False

def load_item_catalog():
    global CACHED_VARIANT_MAP, CATALOG_LOADED
    if CATALOG_LOADED and CACHED_VARIANT_MAP:
        return

    print("\n--- Loading Item Catalog (First Time / Refresh) ---")
    variant_map = {}
    url = f"{BASE_URL}/items"
    page = 1
    
    while url:
        try:
            print(f"Fetching items page {page}...")
            res = requests.get(url, headers=HEADERS, timeout=10)
            if res.status_code == 200:
                data = res.json()
                items = data.get("items", [])
                for item in items:
                    item_name = item.get("item_name") or item.get("name", "Unknown")
                    for variant in item.get("variants", []):
                        v_id = variant.get("id") or variant.get("variant_id")
                        if v_id:
                            variant_map[v_id] = {
                                "item_name": item_name,
                                "sku": variant.get("sku", "N/A")
                            }
                cursor = data.get("cursor")
                url = f"{BASE_URL}/items?cursor={cursor}" if cursor else None
                page += 1
            else:
                break
        except requests.exceptions.Timeout:
            print("Items page fetch timed out.")
            break
            
    CACHED_VARIANT_MAP = variant_map
    CATALOG_LOADED = True
    print(f"Catalog loaded successfully. Total mapped variants: {len(CACHED_VARIANT_MAP)}")

@app.route('/api/loyverse-inventory', methods=['GET'])
def get_loyverse_inventory():
    print("\n--- Starting Inventory Fetch ---")
    load_item_catalog()

    try:
        print("Fetching live inventory levels...")
        inv_res = requests.get(f"{BASE_URL}/inventory", headers=HEADERS, timeout=10)
        if inv_res.status_code == 200:
            inv_data = inv_res.json().get("inventory_levels", [])
            live_inventory = []
            for inv in inv_data:
                v_id = inv.get("variant_id") or inv.get("id")
                stock = inv.get("in_stock", 0)
                info = CACHED_VARIANT_MAP.get(v_id, {"item_name": "Unknown", "sku": "N/A"})
                live_inventory.append({
                    "id": v_id,
                    "name": info["item_name"],
                    "sku": info["sku"],
                    "stock": stock
                })
            print("Inventory response ready.")
            return jsonify(live_inventory)
    except requests.exceptions.Timeout:
        print("Inventory API request timed out.")

    return jsonify({"error": "Failed to fetch inventory"}), 500

@app.route('/api/demand-forecast', methods=['GET'])
def get_demand_forecast():
    print("\n--- Fetching Historical Receipts for Demand Analysis ---")
    sales_history = defaultdict(float)
    url = f"{BASE_URL}/receipts"
    
    page_count = 0
    max_pages = 5
    
    while url and page_count < max_pages:
        try:
            print(f"Fetching receipts page {page_count + 1}...")
            res = requests.get(url, headers=HEADERS, timeout=10)
            if res.status_code != 200:
                break
            data = res.json()
            for receipt in data.get("receipts", []):
                for line_item in receipt.get("line_items", []):
                    v_id = line_item.get("variant_id")
                    quantity = line_item.get("quantity", 0)
                    if v_id:
                        sales_history[v_id] += quantity
            cursor = data.get("cursor")
            url = f"{BASE_URL}/receipts?cursor={cursor}" if cursor else None
            page_count += 1
        except requests.exceptions.Timeout:
            break

    forecast_results = []
    for v_id, total_sold in sales_history.items():
        projected_next_period = round(total_sold / max(page_count, 1) * 1.1, 2)
        forecast_results.append({
            "variant_id": v_id,
            "historical_total_sold": total_sold,
            "projected_demand": projected_next_period
        })

    return jsonify({
        "status": "success",
        "receipt_pages_analyzed": page_count,
        "forecasts": forecast_results
    })

@app.route('/')
def serve_dashboard():
    return send_from_directory('.', 'index.html')


####
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    app.run(host="0.0.0.0", port=port)
