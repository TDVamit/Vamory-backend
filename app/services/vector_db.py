import asyncio
import hashlib
from typing import List, Dict, Any, Optional

from qdrant_client import models
from qdrant_client.async_qdrant_client import AsyncQdrantClient

from app.config import settings


class QdrantVectorDB:
    def __init__(self, url: str = None, api_key: Optional[str] = None, embedding_dim: int = 3072, collection_name: str = "images"):
        if url is None:
            url = settings.qdrant_url
        if api_key is None:
            api_key = settings.qdrant_api_key
            
        self.client = AsyncQdrantClient(url=url, api_key=api_key)
        self.collection = collection_name
        self.embedding_dim = embedding_dim

    async def init_collection(self):
        exists = await self.client.collection_exists(self.collection)
        if not exists:
            await self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(size=self.embedding_dim, distance=models.Distance.COSINE)
            )

    def _generate_point_id(self, owner_id: str, image_id: str) -> int:
        """Generate a consistent integer point ID from owner_id and image_id"""
        combined = f"{owner_id}:{image_id}"
        hash_object = hashlib.md5(combined.encode())
        # Convert first 8 bytes of hash to integer
        return int.from_bytes(hash_object.digest()[:8], byteorder='big')

    async def add(self, desc: str, image_id: str, owner_id: str, vector: List[float]):
        await self.init_collection()
        point_id = self._generate_point_id(owner_id, image_id)
        await self.client.upsert(
            collection_name=self.collection,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={"image_id": image_id, "owner_id": owner_id, "desc": desc}
                )
            ]
        )

    async def query(self, desc: str, owner_id: str, vector: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        await self.init_collection()
        res = await self.client.search(
            collection_name=self.collection,
            query_vector=vector,
            limit=top_k,
            with_payload=True
        )
        return [
            {"id": p.id, "score": p.score, "payload": p.payload}
            for p in res
        ]

    async def delete(self, doc_id: str):
        await self.init_collection()
        if ":" in doc_id:
            owner_id, image_id = doc_id.split(":", 1)
            point_id = self._generate_point_id(owner_id, image_id)
        else:
            point_id = int(doc_id) if doc_id.isdigit() else self._generate_point_id("", doc_id)

        points_selector = models.PointIdsList(points=[point_id])   # <-- use `points`, not `ids`
        await self.client.delete(
            collection_name=self.collection,
            points_selector=points_selector,
            wait=True
        )

    async def get_status(self) -> Dict[str, Any]:
        stats = await self.client.count(collection_name=self.collection, exact=True)
        return {"collection": self.collection, "points_count": stats.count}

    async def clear_and_reset(self):
        """Clear the collection and recreate it with correct dimensions"""
        exists = await self.client.collection_exists(self.collection)
        if exists:
            await self.client.delete_collection(self.collection)
            print(f"🗑️  Deleted existing collection '{self.collection}'")
        await self.init_collection()
        print(f"🆕 Created new collection '{self.collection}' with {self.embedding_dim} dimensions")

    async def close(self):
        await self.client.close()

    async def document_exists(self, doc_id: str) -> bool:
        """Check if a document exists in the vector database"""
        try:
            await self.init_collection()
            # Extract owner_id and image_id from doc_id format "owner_id:image_id"
            if ":" in doc_id:
                owner_id, image_id = doc_id.split(":", 1)
                point_id = self._generate_point_id(owner_id, image_id)
            else:
                # If it's already a point_id, use it directly
                point_id = int(doc_id) if doc_id.isdigit() else self._generate_point_id("", doc_id)
            
            # Try to retrieve the point
            points = await self.client.retrieve(
                collection_name=self.collection,
                ids=[point_id]
            )
            return len(points) > 0
        except Exception as e:
            print(f"⚠️  Error checking document existence: {str(e)}")
            return False
    

# Create a global instance
vector_db = QdrantVectorDB()
