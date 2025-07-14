# DNS resolution fix for MongoDB Atlas
try:
    import app.dns_fix  # Must be imported before motor/pymongo
except ImportError:
    print('WARNING: DNS fix module not available')

from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional
from app.config import settings


class Database:
    client: Optional[AsyncIOMotorClient] = None
    database = None


db = Database()


async def get_database():
    return db.database


async def connect_to_mongo():
    """Create database connection"""
    db.client = AsyncIOMotorClient(settings.mongodb_url)
    db.database = db.client[settings.database_name]
    print(f"Connected to MongoDB at {settings.mongodb_url}")


async def close_mongo_connection():
    """Close database connection"""
    if db.client:
        db.client.close()
        print("Disconnected from MongoDB")


# Collections
async def get_users_collection():
    database = await get_database()
    return database.users


async def get_folders_collection():
    database = await get_database()
    return database.folders


async def get_files_collection():
    database = await get_database()
    return database.files


async def get_folder_access_collection():
    database = await get_database()
    return database.folder_access


async def get_refresh_tokens_collection():
    database = await get_database()
    return database.refresh_tokens 