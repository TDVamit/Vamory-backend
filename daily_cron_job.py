import asyncio
import aioboto3
import motor.motor_asyncio
from datetime import datetime, timedelta, timezone
import traceback

MONGODB_URL = "mongodb+srv://vamit2damor:f75K2zKg1q93yups@cluster0.84ns0n0.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DATABASE_NAME = "gallery_db"
COLLECTION_NAME = "files"
FOLDERS_COLLECTION = "folders"
AWS_ACCESS_KEY_ID = "AKIA5KUOQP3VTZVXBHK2"
AWS_SECRET_ACCESS_KEY = "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"
AWS_REGION = "ap-south-1"
S3_BUCKET_NAME = "vamory-s3-bucket-by-vamit"
MAX_CONCURRENCY = 20
STORAGE_FROM = "STANDARD"
STORAGE_TO = "GLACIER_IR"

def parse_created_at(value):
    if value is None:
        return None
    if isinstance(value, dict):
        v = value.get("$date") or value.get("date") or value
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except Exception:
                try:
                    return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S.%fZ")
                except Exception:
                    return None
        if isinstance(v, (int, float)):
            try:
                return datetime.fromtimestamp(v / 1000, tz=timezone.utc)
            except Exception:
                return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            try:
                return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
            except Exception:
                return None
    if hasattr(value, "tzinfo"):
        return value
    return None

async def update_doc_storage(db, doc_id):
    await db[COLLECTION_NAME].update_one({"_id": doc_id}, {"$set": {"storage_type": STORAGE_TO}})

async def worker_glacier(doc, db, s3_client, sem):
    try:
        created_at_raw = doc.get("created_at")
        created_dt = parse_created_at(created_at_raw)
        if created_dt is None:
            return
        if created_dt.tzinfo is not None:
            created_dt = created_dt.astimezone(timezone.utc).replace(tzinfo=None)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if now - created_dt <= timedelta(days=1):
            return
        s3_key = doc.get("s3_key")
        if not s3_key:
            return
        copy_source = {"Bucket": S3_BUCKET_NAME, "Key": s3_key}
        await s3_client.copy_object(Bucket=S3_BUCKET_NAME, Key=s3_key, CopySource=copy_source, StorageClass=STORAGE_TO, MetadataDirective="COPY")
        await update_doc_storage(db, doc["_id"])
        print("GLACIER_UPDATED", str(doc["_id"]), s3_key)
    except Exception:
        print("Error GLACIER processing", str(doc.get("_id")), traceback.format_exc())
    finally:
        sem.release()

async def worker_delete_file(doc, db, s3_client, sem):
    try:
        deleted_at_raw = doc.get("deleted_at")
        deleted_dt = parse_created_at(deleted_at_raw)
        if deleted_dt is None:
            return
        if deleted_dt.tzinfo is not None:
            deleted_dt = deleted_dt.astimezone(timezone.utc).replace(tzinfo=None)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if now - deleted_dt <= timedelta(days=30):
            return
        s3_key = doc.get("s3_key")
        if s3_key:
            try:
                await s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
            except Exception:
                pass
        await db[COLLECTION_NAME].delete_one({"_id": doc["_id"]})
        print("DELETED_FILE", str(doc["_id"]), s3_key)
    except Exception:
        print("Error DELETE processing", str(doc.get("_id")), traceback.format_exc())
    finally:
        sem.release()

async def worker_purge_folder(folder_doc, db, sem):
    try:
        folder_id_str = str(folder_doc.get("_id"))
        files_found = await db[COLLECTION_NAME].find_one({"folder_id": folder_id_str})
        if not files_found:
            await db[FOLDERS_COLLECTION].delete_one({"_id": folder_doc["_id"]})
            print("DELETED_FOLDER", folder_id_str)
    except Exception:
        print("Error PURGE FOLDER", str(folder_doc.get("_id")), traceback.format_exc())
    finally:
        sem.release()

async def purge_deleted_files(db, s3_client, sem):
    cursor = db[COLLECTION_NAME].find({"deleted": True})
    tasks = []
    async for doc in cursor:
        await sem.acquire()
        tasks.append(asyncio.create_task(worker_delete_file(doc, db, s3_client, sem)))
    if tasks:
        await asyncio.gather(*tasks)

async def purge_deleted_folders(db, sem):
    cursor = db[FOLDERS_COLLECTION].find({"deleted": True})
    tasks = []
    async for folder in cursor:
        await sem.acquire()
        tasks.append(asyncio.create_task(worker_purge_folder(folder, db, sem)))
    if tasks:
        await asyncio.gather(*tasks)

async def main():
    client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URL)
    db = client[DATABASE_NAME]
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    session = aioboto3.Session(aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY, region_name=AWS_REGION)
    async with session.client("s3") as s3_client:
        cursor = db[COLLECTION_NAME].find({"storage_type": STORAGE_FROM})
        tasks = []
        async for doc in cursor:
            await sem.acquire()
            tasks.append(asyncio.create_task(worker_glacier(doc, db, s3_client, sem)))
        if tasks:
            await asyncio.gather(*tasks)
        await purge_deleted_files(db, s3_client, sem)
        await purge_deleted_folders(db, sem)
    client.close()

if __name__ == "__main__":
    asyncio.run(main())
