"""Auto-detect installed AI agent backends (OpenClaw, Kimi, etc.)."""
import json
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("onboarding")

_AGENT_PATHS = [
    ("OpenClaw", Path.home() / ".openclaw" / "openclaw.json"),
    ("Kimi OpenClaw", Path.home() / ".kimi_openclaw" / "openclaw.json"),
]


def scan_for_agents() -> List[dict]:
    """Scan known config paths and return a list of detected agents.

    Each entry is a dict with keys:
        name, config_path, gateway_url, gateway_token
    """
    found: List[dict] = []
    for name, path in _AGENT_PATHS:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("Could not parse %s: %s", path, e)
            continue

        gw = data.get("gateway", {})
        bind = gw.get("bind", "loopback")
        host = "127.0.0.1" if bind in ("loopback", None, "localhost") else bind
        port = gw.get("port", 18789)
        url = f"ws://{host}:{port}"

        token = ""
        auth = gw.get("auth", {})
        if isinstance(auth, dict) and auth.get("mode") == "token":
            token = auth.get("token", "")

        found.append({
            "name": name,
            "config_path": str(path),
            "gateway_url": url,
            "gateway_token": token,
        })
    return found


def get_local_agent_name(fallback: str = "Ghost") -> str:
    """Return the display name of the primary local agent (id == 'main').

    Scans OpenClaw / Kimi configs and returns the 'name' field of the
    agent whose id is 'main'. If not found, returns *fallback*.
    """
    for _label, path in _AGENT_PATHS:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            agents = data.get("agents", {})
            agent_list = agents.get("list", [])
            for agent in agent_list:
                if agent.get("id") == "main":
                    name = agent.get("name", "").strip()
                    if name:
                        # If the name contains a slash (e.g. "Ghost / WarMech"),
                        # return only the first part for a cleaner display.
                        return name.split("/")[0].strip() or fallback
        except Exception as e:
            logger.debug("Could not read agent name from %s: %s", path, e)
            continue
    return fallback
