#!/usr/bin/env python3
"""
Gallery Backend API Startup Script
"""
import sys
import os
import uvicorn
from app.config import settings

def main():
    """Run the FastAPI application"""
    try:
        # Check if environment file exists
        if not os.path.exists('.env'):
            print("⚠️  Warning: .env file not found. Please copy env.example to .env and configure your settings.")
            print("   Run: cp env.example .env")
            return 1
        
        print("🚀 Starting Gallery Backend API...")
        print(f"   Host: {settings.host}")
        print(f"   Port: {settings.port}")
        print(f"   Debug: {settings.debug}")
        print(f"   MongoDB: {settings.mongodb_url}")
        print(f"   Database: {settings.database_name}")
        print()
        print("📚 API Documentation available at:")
        print(f"   Swagger UI: http://{settings.host}:{settings.port}/docs")
        print(f"   ReDoc: http://{settings.host}:{settings.port}/redoc")
        print()
        
        # Run the application
        uvicorn.run(
            "main:app",
            host=settings.host,
            port=settings.port,
            reload=settings.debug,
            # Large file upload configuration
            limit_max_requests=1000,
            timeout_keep_alive=30,  # Keep connections alive longer for large uploads
            # Allow large request bodies (12GB to handle 10GB files with overhead)
            h11_max_incomplete_event_size=12 * 1024 * 1024 * 1024,  # 12GB
            # Increase timeouts for large file uploads
            timeout_graceful_shutdown=30,
        )
        
    except KeyboardInterrupt:
        print("\n👋 Shutting down Gallery Backend API...")
        return 0
    except Exception as e:
        print(f"❌ Error starting application: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main()) 