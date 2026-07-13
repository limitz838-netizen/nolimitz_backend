import os
import httpx
from urllib.parse import urlencode


class DerivService:
    def __init__(self):
        self.app_id = os.getenv("DERIV_APP_ID")
        self.client_id = os.getenv("DERIV_CLIENT_ID")
        self.client_secret = os.getenv("DERIV_CLIENT_SECRET")
        self.redirect_uri = os.getenv("DERIV_REDIRECT_URI")
        self.ws_url = os.getenv(
            "DERIV_WS_URL",
            "wss://ws.derivws.com/websockets/v3",
        )

    def check_configuration(self):
        return {
            "app_id": self.app_id,
            "redirect_uri": self.redirect_uri,
            "ws_url": self.ws_url,
            "configured": bool(self.app_id and self.redirect_uri),
        }
    
    def get_login_url(self):

        params = {

            "app_id": self.app_id,

            "redirect_uri": self.redirect_uri,

            "l": "EN",

        }

        return (

            "https://oauth.deriv.com/oauth2/authorize?"

            + urlencode(params)

        )