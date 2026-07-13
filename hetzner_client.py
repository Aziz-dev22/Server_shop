import requests
from config import logger

class HetznerClient:
    def __init__(self, api_token):
        self.api_token = api_token
        self.base_url = "https://api.hetzner.cloud/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }

    def _request(self, method, endpoint, data=None):
        url = f"{self.base_url}{endpoint}"
        try:
            response = requests.request(method, url, headers=self.headers, json=data, timeout=15)
            if response.status_code in [200, 201]:
                return response.json()
            logger.error(f"Hetzner API Error [{response.status_code}]: {response.text}")
            return None
        except Exception as e:
            logger.error(f"HTTP Request failed: {e}")
            return None

    def create_server(self, name, server_type, image="ubuntu-24.04", location="nbg1"):
        payload = {
            "name": name,
            "server_type": server_type,
            "image": image,
            "location": location,
            "start_after_create": True
        }
        res = self._request("POST", "/servers", data=payload)
        if res and "server" in res:
            return {
                "id": res["server"]["id"],
                "ip": res["server"]["public_net"]["ipv4"]["ip"],
                "root_password": res["root_password"]
            }
        return None

    def action_server(self, server_id, action):
        return self._request("POST", f"/servers/{server_id}/actions/{action}")

    def get_server_metrics(self, server_id):
        res = self._request("GET", f"/servers/{server_id}")
        if res and "server" in res:
            outgoing = res["server"].get("outgoing_traffic", 0) or 0
            incoming = res["server"].get("incoming_traffic", 0) or 0
            return (outgoing + incoming) / (1024 ** 3)
        return 0.0

    def delete_server(self, server_id):
        return self._request("DELETE", f"/servers/{server_id}")

