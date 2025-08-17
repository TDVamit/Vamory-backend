#!/usr/bin/env python3
"""
Test script for the new Qdrant vector database implementation
"""

import asyncio
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

async def test_qdrant_vector_db():
    """Test the Qdrant vector database implementation"""
    try:
        from app.services.vector_db import vector_db
        from openai import AsyncOpenAI
        from app.config import settings
        
        print("🔍 Testing Qdrant Vector Database Implementation")
        print(f"📡 Qdrant URL: {settings.qdrant_url}")
        print(f"🔑 API Key: {'Set' if settings.qdrant_api_key else 'Not set'}")
        
        # Initialize OpenAI client
        openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        
        # Clear and reset collection to ensure correct dimensions
        print("\n1. Clearing and resetting collection...")
        await vector_db.clear_and_reset()
        print("✅ Collection cleared and reset successfully")
        
        # Test status
        print("\n2. Testing status...")
        status = await vector_db.get_status()
        print(f"✅ Status: {status}")
        
        # Test adding a document
        print("\n3. Testing document addition...")
        test_desc = "A beautiful sunset over the ocean with palm trees"
        test_image_id = "test_image_001"
        test_user_id = "test_user_001"
        
        # Generate embedding
        embedding_response = await openai_client.embeddings.create(
            model=settings.OPENAI_EMBEDDING_MODEL,
            input=test_desc
        )
        test_vector = embedding_response.data[0].embedding
        
        await vector_db.add(test_desc, test_image_id, test_user_id, test_vector)
        print("✅ Document added successfully")
        
        # Test document existence
        print("\n4. Testing document existence...")
        doc_exists = await vector_db.document_exists(f"{test_user_id}:{test_image_id}")
        print(f"✅ Document exists: {doc_exists}")
        
        # Test query
        print("\n5. Testing query...")
        query_desc = "sunset ocean"
        query_embedding_response = await openai_client.embeddings.create(
            model=settings.OPENAI_EMBEDDING_MODEL,
            input=query_desc
        )
        query_vector = query_embedding_response.data[0].embedding
        
        results = await vector_db.query(query_desc, test_user_id, query_vector, top_k=5)
        print(f"✅ Query results: {len(results)} documents found")
        for i, result in enumerate(results):
            print(f"   Result {i+1}: {result['payload']['image_id']} (score: {result['score']:.4f})")
        
        # Test deletion
        print("\n6. Testing document deletion...")
        await vector_db.delete(f"{test_user_id}:{test_image_id}")
        print("✅ Document deleted successfully")
        
        # Verify deletion
        doc_exists_after = await vector_db.document_exists(f"{test_user_id}:{test_image_id}")
        print(f"✅ Document exists after deletion: {doc_exists_after}")
        
        # Final status
        print("\n7. Final status...")
        final_status = await vector_db.get_status()
        print(f"✅ Final status: {final_status}")
        
        print("\n🎉 All tests passed! Qdrant vector database is working correctly.")
        
    except Exception as e:
        print(f"❌ Test failed: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_qdrant_vector_db())
