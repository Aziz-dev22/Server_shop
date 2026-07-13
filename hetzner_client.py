import requests
from config import logger

class HetznerClient:
    def __init__(self, api_token):
        self.api_token = api_token
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }
        self.base_url = "https://api.hetzner.cloud/v1"

    def _request(self, method, endpoint, data=None):
        try:
            url = f"{self.base_url}{endpoint}"
            response = requests.request(method, url, headers=self.headers, json=data, timeout=15)
            if response.status_code < 300:
                return response.json()
            else:
                logger.error(f"Hetzner API Error: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"Connection Error: {e}")
            return None

    def create_server(self, name, server_type, location="nbg1"):
        payload = {
            "name": name,
            "server_type": server_type,
            "image": "ubuntu-24.04",
            "location": location,
            "start_after_create": True
        }
        res = self._request("POST", "/servers", payload)
        if res and "server" in res:
            return {
                "id": res["server"]["id"], 
                "ip": res["server"]["public_net"]["ipv4"]["ip"], 
                "root_password": res["root_password"]
            }
        return None

    def action_server(self, server_id, action):
        # اکشن‌های مجاز: poweron, poweroff, reboot, rebuild, reset_password
        return self._request("POST", f"/servers/{server_id}/actions/{action}") is not None

    def change_ip(self, server_id):
        # درخواست جایگزینی آی‌پی (Replace IP)
        res = self._request("POST", f"/servers/{server_id}/actions/replace_ip", {"type": "ipv4"})
        return res is not None

    def fetch_plans(self):
        # دریافت لیست پلن‌های موجود از هتزنر
        res = self._request("GET", "/server_types")
        if res and "server_types" in res:
            return res["server_types"]
        return []

    def get_server_status(self, server_id):
        res = self._request("GET", f"/servers/{server_id}")
        return res["server"]["status"] if res and "server" in res else "unknown"

    def delete_server(self, server_id):
        return self._request("DELETE", f"/servers/{server_id}") is not None
