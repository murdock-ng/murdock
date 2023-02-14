#!/usr/bin/env python3

import uvicorn
from murdock.log import stdlib_config

if __name__ == "__main__":
    uvicorn.run(
        "murdock.main:app",
        reload=True,
        reload_dirs="murdock",
        host="0.0.0.0",
        log_config=stdlib_config,
    )
