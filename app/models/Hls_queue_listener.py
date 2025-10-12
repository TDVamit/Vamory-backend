import os
import asyncio
import json
import logging
from typing import Optional, Dict
from datetime import datetime

import aiohttp  
import aiobotocore.session
from app.database import get_hls_conversion_status_collection, ensure_hls_conversion_status_collection
from app.models.hls_conversion_status import HlsConversionStatusCreate, HlsConversionStatusUpdate  


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== Configuration ==========

REGION = os.getenv("REGION_NAME", "ap-south-1")
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL")
POLL_WAIT_TIME = int(os.getenv("SQS_WAIT_TIME", "20"))  # seconds for long polling
MAX_MESSAGES = int(os.getenv("SQS_MAX_MESSAGES", "5"))
VISIBILITY_TIMEOUT = int(os.getenv("SQS_VISIBILITY_TIMEOUT", "60"))

# ========== Database Operations ==========

class HlsConversionDB:
    async def update_job(self, query: Dict, update_values: Dict) -> bool:
        """
        Update HLS conversion status in database.
        Return True if a document was matched & updated, else False.
        """
        try:
            collection = await get_hls_conversion_status_collection()
            if not collection:
                logger.error("Failed to get HLS conversion status collection")
                return False

            # Add updated_at timestamp
            update_values["updated_at"] = datetime.utcnow()

            # Try to find and update existing record
            result = await collection.find_one_and_update(
                query,
                {"$set": update_values},
                return_document=True
            )

            if result:
                logger.info("Updated HLS conversion status: query=%s, update=%s", query, update_values)
                return True
            else:
                logger.warning("No HLS conversion status record found for query: %s", query)
                return False

        except Exception as e:
            logger.exception("Database update failed: %s", e)
            return False

    async def create_job(self, job_data: HlsConversionStatusCreate) -> bool:
        """
        Create a new HLS conversion status record.
        Return True if created successfully, else False.
        """
        try:
            collection = await get_hls_conversion_status_collection()
            if not collection:
                logger.error("Failed to get HLS conversion status collection")
                return False

            # Convert to dict and add timestamps
            job_dict = job_data.dict()
            job_dict["created_at"] = datetime.utcnow()
            job_dict["updated_at"] = datetime.utcnow()

            result = await collection.insert_one(job_dict)
            if result.inserted_id:
                logger.info("Created new HLS conversion status: job_id=%s, status=%s", 
                           job_data.job_id, job_data.status)
                return True
            else:
                logger.error("Failed to create HLS conversion status record")
                return False

        except Exception as e:
            logger.exception("Database create failed: %s", e)
            return False

db = HlsConversionDB()


# ========== SQS Async Wrapper ==========

async def get_sqs_client():
    session = aiobotocore.session.get_session()
    return session.create_client('sqs', region_name=REGION)


async def receive_messages(sqs_client) -> Optional[list]:
    """
    Retrieve messages from SQS queue via long polling.
    Returns list of messages (dictionaries), or [] if none.
    """
    try:
        resp = await sqs_client.receive_message(
            QueueUrl=SQS_QUEUE_URL,
            MaxNumberOfMessages=MAX_MESSAGES,
            WaitTimeSeconds=POLL_WAIT_TIME,
            VisibilityTimeout=VISIBILITY_TIMEOUT,
            MessageAttributeNames=['All'],
        )
        msgs = resp.get("Messages", [])
        return msgs
    except Exception as e:
        logger.exception("receive_messages error: %s", e)
        return []


async def delete_message(sqs_client, receipt_handle: str):
    """
    Deletes one message from SQS using its receipt handle.
    """
    try:
        await sqs_client.delete_message(
            QueueUrl=SQS_QUEUE_URL,
            ReceiptHandle=receipt_handle
        )
    except Exception as e:
        logger.exception("delete_message error: %s", e)


# ========== Processing Logic ==========

async def process_message_body(body: dict):
    """
    Process one message body (parsed JSON).
    Update HLS conversion status in database.
    Only updates if job_id is found in the database.
    """
    detail = body.get("detail", {})
    user_meta = body.get("userMetadata", {})

    job_id = detail.get("jobId")
    status = detail.get("status")
    db_id = user_meta.get("db_id")

    if not job_id and not db_id:
        logger.warning("Message missing jobId and db_id: %s", body)
        return False

    # Build query - prioritize db_id if available, otherwise use job_id
    query = {}
    if db_id:
        query = {"_id": db_id}
    else:
        query = {"job_id": job_id}

    # Build update values
    update = {"status": status}
    if job_id:
        update["job_id"] = job_id
    if detail.get("errorMessage"):
        update["error"] = detail.get("errorMessage")

    # Try to update existing record
    try:
        success = await db.update_job(query, update)
        if success:
            logger.info("Updated HLS conversion status: query=%s, status=%s", query, status)
            return True
        else:
            logger.warning("No HLS conversion status record found for query: %s", query)
            return False
    except Exception as e:
        logger.exception("Database update failed: %s", e)
        return False


# ========== Initialization ==========

async def initialize_hls_conversion_status():
    """Initialize HLS conversion status collection"""
    try:
        success = await ensure_hls_conversion_status_collection()
        if success:
            logger.info("✓ HLS conversion status collection initialized successfully")
        else:
            logger.error("✗ Failed to initialize HLS conversion status collection")
        return success
    except Exception as e:
        logger.exception("Error initializing HLS conversion status: %s", e)
        return False


# ========== Worker Loop ==========

async def worker_loop():
    logger.info("Starting SQS worker, polling queue: %s", SQS_QUEUE_URL)
    
    # Initialize HLS conversion status collection
    await initialize_hls_conversion_status()
    
    async with await get_sqs_client() as sqs_client:
        while True:
            messages = await receive_messages(sqs_client)
            if not messages:
                continue

            for msg in messages:
                receipt = msg.get("ReceiptHandle")
                body_text = msg.get("Body")

                try:
                    body = json.loads(body_text)
                except json.JSONDecodeError:
                    logger.error("Invalid JSON body: %s", body_text)
                    await delete_message(sqs_client, receipt)
                    continue

                processed = await process_message_body(body)
                if processed:
                    await delete_message(sqs_client, receipt)
                else:
                    logger.warning("Did not delete message, will reappear after visibility timeout")

            await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(worker_loop())
