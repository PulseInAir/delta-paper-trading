import os
import time
import hmac
import hashlib
import json
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class DeltaClient:
    def __init__(self, api_key=None, api_secret=None, base_url=None):
        self.api_key = api_key or os.getenv("DELTA_API_KEY")
        self.api_secret = api_secret or os.getenv("DELTA_API_SECRET")
        self.base_url = base_url or os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange")
        self.session = requests.Session()
        
    def _generate_signature(self, method, timestamp, path, body=""):
        # Construct signature string: METHOD + TIMESTAMP + PATH + BODY
        signature_data = f"{method.upper()}{timestamp}{path}{body}"
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            signature_data.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    def request(self, method, path, params=None, json_data=None, auth=True):
        url = f"{self.base_url}{path}"
        method = method.upper()
        
        # Serialize body if present
        body_str = ""
        if json_data is not None:
            body_str = json.dumps(json_data, separators=(',', ':'))
            
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        if auth:
            if not self.api_key or not self.api_secret:
                raise ValueError("API Key and Secret must be configured for authenticated endpoints.")
            # Use epoch seconds for Delta Exchange India
            timestamp = str(int(time.time()))
            signature = self._generate_signature(method, timestamp, path, body_str)
            
            headers.update({
                'api-key': self.api_key,
                'timestamp': timestamp,
                'signature': signature
            })
            
        try:
            if method == "GET":
                response = self.session.get(url, headers=headers, params=params, timeout=10)
            elif method == "POST":
                response = self.session.post(url, headers=headers, data=body_str if json_data else None, timeout=10)
            elif method == "DELETE":
                response = self.session.delete(url, headers=headers, data=body_str if json_data else None, timeout=10)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
                
            return response.json()
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": {
                    "code": "network_error",
                    "message": str(e)
                }
            }
        except ValueError:
            # JSON decoding failed
            return {
                "success": False,
                "error": {
                    "code": "invalid_response",
                    "message": f"Server returned non-JSON response: {response.text[:200]}"
                }
            }

    # Public Endpoints
    def get_assets(self):
        return self.request("GET", "/v2/assets", auth=False)
        
    def get_products(self):
        return self.request("GET", "/v2/products", auth=False)
        
    def get_tickers(self):
        return self.request("GET", "/v2/tickers", auth=False)
        
    def get_ticker(self, symbol):
        res = self.request("GET", f"/v2/tickers/{symbol}", auth=False)
        if res.get("success"):
            return res
        all_tickers = self.get_tickers()
        if all_tickers.get("success"):
            for t in all_tickers.get("result", []):
                if t.get("symbol") == symbol:
                    return {"success": True, "result": t}
        return {"success": False, "error": {"message": f"Ticker not found for {symbol}"}}

    def get_order_book(self, symbol, limit=20):
        params = {"limit": limit}
        return self.request("GET", f"/v2/l2orderbook/{symbol}", params=params, auth=False)

    # Authenticated Endpoints
    def get_balances(self):
        return self.request("GET", "/v2/wallet/balances", auth=True)
        
    def get_orders(self, symbol=None, state=None):
        params = {}
        if symbol:
            params["symbol"] = symbol
        if state:
            params["state"] = state
        return self.request("GET", "/v2/orders", params=params, auth=True)

    def get_positions(self):
        return self.request("GET", "/v2/positions", auth=True)

    def place_order(self, product_id, size, side, order_type="limit_order", price=None, post_only=False, reduce_only=False):
        data = {
            "product_id": int(product_id),
            "size": int(size),
            "side": side.lower(),
            "order_type": order_type
        }
        
        if order_type == "limit_order":
            if price is None:
                raise ValueError("Price is required for limit orders")
            data["limit_price"] = str(price)
            data["time_in_force"] = "gtc"
            
        if post_only:
            data["post_only"] = True
        if reduce_only:
            data["reduce_only"] = True
            
        return self.request("POST", "/v2/orders", json_data=data, auth=True)

    def cancel_order(self, product_id, order_id):
        data = {
            "product_id": int(product_id),
            "id": int(order_id)
        }
        return self.request("DELETE", "/v2/orders", json_data=data, auth=True)

    def get_orders_history(self, limit=100):
        params = {"limit": limit}
        return self.request("GET", "/v2/orders/history", params=params, auth=True)
