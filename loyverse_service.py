import os
from datetime import datetime, timedelta
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
            res = requests.get(url, headers=HEADERS, timeout=15)
            print(f"Items API Status Code: {res.status_code}")
            if res.status_code == 200:
                data = res.json()
                items = data.get("items", [])
                print(f"Found {len(items)} items on page {page}")
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
                print(f"Failed to fetch items: {res.text}")
                break
        except requests.exceptions.Timeout:
            print("Items page fetch timed out.")
            break
        
    CACHED_VARIANT_MAP = variant_map
    CATALOG_LOADED = True
    print(f"Catalog loaded successfully. Total pages fetched: {page - 1}, Total mapped variants: {len(CACHED_VARIANT_MAP)}")

@app.route('/api/loyverse-inventory', methods=['GET'])
def get_loyverse_inventory():
    print("\n--- Starting Inventory Fetch ---")
    load_item_catalog()
    print(f"Current CACHED_VARIANT_MAP size: {len(CACHED_VARIANT_MAP)}")

    try:
        print("Fetching live inventory levels...")
        inv_res = requests.get(f"{BASE_URL}/inventory", headers=HEADERS, timeout=15)
        if inv_res.status_code == 200:
            inv_data = inv_res.json().get("inventory_levels", [])
            live_inventory = []
            for inv in inv_data:
                v_id = inv.get("variant_id") or inv.get("id")
                stock = inv.get("in_stock", 0)
                
                found = v_id in CACHED_VARIANT_MAP
                if not found:
                    print(f"Missing Variant ID in Cache: {v_id}")
                
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
            res = requests.get(url, headers=HEADERS, timeout=15)
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

    # Generate recent 7-day trend dates and metrics for the line chart
    today = datetime.now()
    trend_dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    
    # Calculate baseline and sensed demand metrics based on aggregate volume
    base_volume = sum(sales_history.values()) / max(len(sales_history), 1)
    if base_volume == 0:
        base_volume = 100.0  # fallback default baseline

    ml_baseline = [round(base_volume * (1 + (i * 0.02)), 1) for i in range(7)]
    effective_demand = [round(val * 1.15, 1) for val in ml_baseline]

    return jsonify({
        "status": "success",
        "receipt_pages_analyzed": page_count,
        "forecasts": forecast_results,
        "trend_dates": trend_dates,
        "ml_baseline": ml_baseline,
        "effective_demand": effective_demand
    })

@app.route('/api/platform-revenue', methods=['GET'])
def calculate_platform_fee():
    print("\n--- Calculating Platform Revenue from Receipts (September 2026) ---")
    total_sales_gmv = 0.0
    url = f"{BASE_URL}/receipts"
    
    page_count = 0
    max_pages = 5
    target_year = 2026
    target_month = 9  # September
    
    while url and page_count < max_pages:
        try:
            res = requests.get(url, headers=HEADERS, timeout=15)
            if res.status_code != 200:
                break
            data = res.json()
            for receipt in data.get("receipts", []):
                created_at_str = receipt.get("created_at")
                if created_at_str:
                    try:
                        receipt_date = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                        if receipt_date.year == target_year and receipt_date.month == target_month:
                            total_sales_gmv += float(receipt.get("total_money", 0.0))
                    except ValueError:
                        pass
            
            cursor = data.get("cursor")
            url = f"{BASE_URL}/receipts?cursor={cursor}" if cursor else None
            page_count += 1
        except requests.exceptions.Timeout:
            break
    
    # Calculate 1% fee (RM 0.01 per RM 1.00)
    platform_fee_earned = total_sales_gmv * 0.01
    
    return jsonify({
        "status": "success",
        "receipt_pages_analyzed": page_count,
        "billing_cycle": "September 2026",
        "total_sales_gmv": round(total_sales_gmv, 2),
        "commission_rate": "1%",
        "platform_fee_earned": round(platform_fee_earned, 2)
    })

@app.route('/api/save-config', methods=['POST'])
def save_client_config():
    global API_TOKEN, HEADERS, CATALOG_LOADED, CACHED_VARIANT_MAP
    data = request.json or {}
    api_token = data.get('api_token')
    
    if not api_token:
        return jsonify({"error": "API Token is required"}), 400
        
    API_TOKEN = api_token.strip()
    HEADERS = {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}
    
    CATALOG_LOADED = False
    CACHED_VARIANT_MAP = {}
    
    try:
        load_item_catalog()
        return jsonify({
            "status": "success",
            "message": "Configuration saved & catalog re-loaded successfully!"
        })
    except Exception as e:
        return jsonify({"error": f"Failed to connect using provided token: {str(e)}"}), 500

@app.route('/')
def serve_dashboard():
    return send_from_directory('.', 'index.html')

print("Pre-loading item catalog on startup...")
load_item_catalog()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    app.run(host="0.0.0.0", port=port)