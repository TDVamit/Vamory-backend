from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.database import connect_to_mongo, close_mongo_connection
from app.routers import auth, folders, files, faces, ai_search, credit, notifications, cdn
from app.config import settings
from app.services.Hls_queue_listener import worker_loop
import tflite_runtime.interpreter as tflite
import asyncio
import os


model_path = os.path.join(os.path.dirname(__file__), 'app','face_models', 'mobilefacenet.tflite')
interpreter = tflite.Interpreter(model_path)
interpreter.allocate_tensors()



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await connect_to_mongo()
    
    # Start HLS queue listener as background task
    hls_task = asyncio.create_task(worker_loop())
    print("✓ HLS queue listener started as background task")
    
    yield
    
    # Shutdown
    # Cancel the HLS queue listener task
    hls_task.cancel()
    try:
        await hls_task
    except asyncio.CancelledError:
        print("✓ HLS queue listener stopped")
    
    await close_mongo_connection()


app = FastAPI(
    title="Gallery Backend API",
    description="A comprehensive gallery application backend with MongoDB, S3, and authentication",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware with support for large file uploads
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://vamory.vadaevri.com","http://localhost:5173","http://127.0.0.1:5500","http://localhost:8002"],  # Configure this properly for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=3600,
)

# Include routers
app.include_router(auth.router, prefix="/api/v1")
app.include_router(folders.router, prefix="/api/v1")
app.include_router(files.router, prefix="/api/v1")
app.include_router(faces.router, prefix="/api/v1")
app.include_router(ai_search.router, prefix="/api/v1")
app.include_router(credit.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(cdn.router, prefix="/api/v1")


@app.get("/")
async def root():
    return {
        "message": "Gallery Backend API",
        "version": "1.0.0",
        "status": "running",
        "max_video_size": settings.max_video_size,
        "max_file_size": settings.max_file_size
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "database": "connected",
        "api_version": "1.0.0"
    }


if __name__ == "__main__":
    import uvicorn
    
    # Configure uvicorn for large file uploads
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        # Large file upload configuration
        limit_max_requests=1000,
        timeout_keep_alive=5,
        # Allow large request bodies (10GB + overhead)
        h11_max_incomplete_event_size=12 * 1024 * 1024 * 1024,  # 12GB
    )
