#!/usr/bin/env python3
"""
Simple startup script for Gallery Backend API
"""
import uvicorn
from app.config import settings

if __name__ == "__main__":
    print(f"Starting Gallery Backend API on {settings.host}:{settings.port}")
    print(f"Debug mode: {settings.debug}")
    print(f"Docs available at: http://{settings.host}:{settings.port}/docs")
    
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug
    ) 