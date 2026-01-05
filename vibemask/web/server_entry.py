import sys
import os
import uvicorn
from vibemask.web.api import app

if __name__ == "__main__":
    # Freeze support for multiprocessing in PyInstaller
    import multiprocessing
    multiprocessing.freeze_support()
    
    # Run server
    # Port 8765 is what we defaulted to in CLI, sticking to 8000 for local dev consistency
    # or better, allow dynamic port? 
    # For standalone app, fixed port is simpler for frontend to know where to connect.
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
