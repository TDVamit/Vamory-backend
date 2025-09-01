import asyncio
import aioboto3
import motor.motor_asyncio
from datetime import datetime, timedelta, timezone
from app.config import settings
from app.services.mail_util import send_retrieval_success_message
import traceback
import re

MONGODB_URL = settings.mongodb_url
DATABASE_NAME = settings.database_name
COLLECTION_NAME = "files"
FOLDERS_COLLECTION = "folders"
USERS_COLLECTION = "users"
AWS_ACCESS_KEY_ID = settings.aws_access_key_id
AWS_SECRET_ACCESS_KEY = settings.aws_secret_access_key
AWS_REGION = settings.aws_region
S3_BUCKET_NAME = settings.s3_bucket_name
MAX_CONCURRENCY = 20
STORAGE_FROM = "STANDARD"
STORAGE_TO = "GLACIER_IR"


async def check_file_status(file_doc, s3_client, deep_sem):
    try:
        s3_key = file_doc["s3_key"]
        response = s3_client.head_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
        restore_header = response.get("Restore")

        if not restore_header:
            # No restore in progress or completed
            return datetime.now(timezone.utc)

        # Regex to find expiry-date
        m = re.search(r'expiry-date="([^"]+)"', restore_header)
        expiry = None
        if m:
            expiry_str = m.group(1)
            # Parse date string as RFC 1123 format, e.g. 'Fri, 21 Dec 2012 00:00:00 GMT'
            expiry = datetime.strptime(expiry_str, "%a, %d %b %Y %H:%M:%S %Z")
            return  expiry  # Restore done
        else:
            return True  # Restore still in progress
    except Exception as e:
        print(f"Error checking file status: {e}")
        return datetime.now(timezone.utc)
    finally:
        await deep_sem.release()

async def check_folder_file_status(folder_doc, db, s3_client, sem):
    try:
        file_cursor = db[COLLECTION_NAME].find({"folder_id": folder_doc["_id"]})
        deep_sem = asyncio.Semaphore(MAX_CONCURRENCY)
        tasks = []
        min_expiry = datetime.now(timezone.utc)
        async for file in file_cursor:
            await deep_sem.acquire()
            tasks.append(asyncio.create_task(check_file_status(file, s3_client, deep_sem)))
        if tasks:
            results = await asyncio.gather(*tasks)
            if results and False in results:
                return False
            else:
                min_expiry = max(results)

        folder_cursor = db[FOLDERS_COLLECTION].find_one({"parent_folder_id": folder_doc["_id"]})
        async for folder in folder_cursor:
            await deep_sem.acquire()
            tasks.append(asyncio.create_task(check_folder_file_status(folder,db, s3_client, deep_sem)))
        if tasks:
            results = await asyncio.gather(*tasks)
            if results and False in results:
                return False
            else:
                min_expiry = max(results)
        if min_expiry:
            return min_expiry
        else:
            return datetime.now(timezone.utc)
    except Exception as e:
        print(f"Error checking folder file status: {e}")
        return datetime.now(timezone.utc)
    finally:
        await sem.release()
    
async def update_folder_status(db,folder, min_expiry):
    await db[FOLDERS_COLLECTION].update_one({"_id": folder["_id"]}, {"$set": {"expiry": min_expiry , "status": "active"}})
    user = await db[USERS_COLLECTION].find_one({"_id": folder["owner_id"]})
    await send_retrieval_success_message(folder["name"], user["email"], folder["retrieval_days"], min_expiry)  

async def check_expiry(db,doc,sem):
    try:
        expiry = doc["expiry"]
        now = datetime.now(timezone.utc)
        if now > expiry:
            await db[FOLDERS_COLLECTION].update_one({"_id": doc["_id"]}, {"$set": {"status": "inactive" , "retrieval_days" : None , "retrieval_expiry_date" : None}})
        else:
            pass
    except Exception as e:
        print(f"Error checking expiry: {e}")
    finally:
        await sem.release()

async def main():
    client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URL)
    db = client[DATABASE_NAME]
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    session = aioboto3.Session(aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY, region_name=AWS_REGION)
    async with session.client("s3") as s3_client:
        cursor = db[FOLDERS_COLLECTION].find({"status": "converting"})
        tasks = []
        async for doc in cursor:
            await sem.acquire()
            tasks.append(asyncio.create_task(check_folder_file_status(doc, db, s3_client, sem)))
        if tasks:
            results = await asyncio.gather(*tasks)
            update_tasks = []
            for i,result in enumerate(results):
                if result :
                    min_expiry = result
                    folder = cursor[i]
                    update_tasks.append(asyncio.create_task(update_folder_status(db,folder, min_expiry)))
            if update_tasks:
                await asyncio.gather(*update_tasks)

        cursor = db[FOLDERS_COLLECTION].find({"expiry": {"$ne": None}})
        tasks = []
        async for doc in cursor:
            await sem.acquire()
            tasks.append(asyncio.create_task(check_expiry(db,doc,sem)))
        if tasks:
            await asyncio.gather(*tasks)

    client.close()

if __name__ == "__main__":
    asyncio.run(main())