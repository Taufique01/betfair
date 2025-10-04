import json
from pathlib import Path
import betfairlightweight
from safe_api import safe_api_call
from logger_factory import get_logger  # singleton logger

logger = get_logger()  # singleton logger

CONFIG_FILE = Path(__file__).parent.resolve() / "config.json"

def load_config():
    """Load Betfair API configuration from JSON file."""
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

def create_client(cfg):
    """
    Create a Betfair API client and log in.
    Returns (betting interface, logout function).
    """
    client = betfairlightweight.APIClient(
        username=cfg["betfair_username"],
        password=cfg["betfair_password"],
        app_key=cfg["betfair_app_key"],
        certs=cfg["certs"]
    )
    safe_api_call(client.login)
    logger.info("Logged in to Betfair API")
    return client.betting, client.logout
