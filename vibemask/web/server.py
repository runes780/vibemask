"""
Web UI server startup.
"""

import uvicorn
from pathlib import Path


def start_server(host: str = "127.0.0.1", port: int = 8765):
    """
    Start the VibeMask Web UI server.
    
    Args:
        host: Host to bind to
        port: Port to listen on
    """
    from .api import app
    
    print(f"\n🎭 VibeMask Web UI")
    print(f"   Running at: http://{host}:{port}")
    print(f"   Press Ctrl+C to stop\n")
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="warning",
    )


if __name__ == "__main__":
    start_server()
