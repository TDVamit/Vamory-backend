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
    if db.database is None:
        print("Warning: Database not connected. Attempting to connect...")
        try:
            await connect_to_mongo()
            return db.database
        except Exception as e:
            print(f"Error connecting to database: {e}")
            return None
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


async def get_faces_collection():
    database = await get_database()
    return database.faces


async def get_notifications_collection():
    database = await get_database()
    return database.notifications


async def get_costs_collection():
    database = await get_database()
    return database.costs


async def get_email_tracking_collection():
    database = await get_database()
    if database is None:
        print("Warning: Database is None, cannot access email_tracking collection")
        return None
    
    # This will create the collection if it doesn't exist
    collection = database.email_tracking
    
    # Ensure the collection exists by trying to access it
    try:
        # This will create the collection if it doesn't exist
        await collection.find_one({})
        
        # Create indexes for better performance
        await collection.create_index([("email_type", 1), ("to_email", 1), ("user_name", 1), ("sent_at", -1)])
        await collection.create_index([("to_email", 1), ("user_name", 1), ("email_type", 1)])
        
        return collection
    except Exception as e:
        print(f"Error accessing email_tracking collection: {e}")
        return None


async def get_hls_conversion_status_collection():
    database = await get_database()
    if database is None:
        print("Warning: Database is None, cannot access hls_conversion_status collection")
        return None
    
    # This will create the collection if it doesn't exist
    collection = database.hls_conversion_status
    
    # Ensure the collection exists by trying to access it
    try:
        # This will create the collection if it doesn't exist
        await collection.find_one({})
        
        # Create indexes for better performance
        await collection.create_index([("job_id", 1)])
        await collection.create_index([("status", 1)])
        await collection.create_index([("created_at", -1)])
        
        return collection
    except Exception as e:
        print(f"Error accessing hls_conversion_status collection: {e}")
        return None


async def ensure_hls_conversion_status_collection():
    """Ensure the hls_conversion_status collection exists with proper indexes"""
    try:
        collection = await get_hls_conversion_status_collection()
        if collection is not None:
            print("✓ HLS conversion status collection ready")
            return True
        else:
            print("✗ Failed to create HLS conversion status collection")
            return False
    except Exception as e:
        print(f"Error ensuring HLS conversion status collection: {e}")
        return False


async def ensure_email_tracking_collection():
    """Ensure the email_tracking collection exists with proper indexes"""
    try:
        collection = await get_email_tracking_collection()
        if collection is not None:
            print("✓ Email tracking collection ready")
            return True
        else:
            print("✗ Failed to create email tracking collection")
            return False
    except Exception as e:
        print(f"Error ensuring email tracking collection: {e}")
        return False


async def get_cdn_upload_status_collection():
    database = await get_database()
    if database is None:
        print("Warning: Database is None, cannot access cdn_upload_status collection")
        return None
    
    # This will create the collection if it doesn't exist
    collection = database.cdn_upload_status
    
    # Ensure the collection exists by trying to access it
    try:
        # This will create the collection if it doesn't exist
        await collection.find_one({})
        
        # Create indexes for better performance
        await collection.create_index([("s3_key", 1)])
        await collection.create_index([("status", 1)])
        await collection.create_index([("created_at", -1)])
        
        return collection
    except Exception as e:
        print(f"Error accessing cdn_upload_status collection: {e}")
        return None


async def ensure_cdn_upload_status_collection():
    """Ensure the cdn_upload_status collection exists with proper indexes"""
    try:
        collection = await get_cdn_upload_status_collection()
        if collection is not None:
            print("✓ CDN upload status collection ready")
            return True
        else:
            print("✗ Failed to create CDN upload status collection")
            return False
    except Exception as e:
        print(f"Error ensuring CDN upload status collection: {e}")
        return False 