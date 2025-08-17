from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, List, Any, Set
from bson import ObjectId
import asyncio

from app.dependencies import get_current_user_id
from app.services.AI_search_util import enhance_search
from app.services.vector_db import vector_db
from app.database import get_files_collection, get_folders_collection
from app.services.face_recognition import get_faces_for_file
from app.services.s3 import s3_service
from openai import AsyncOpenAI
from app.config import settings

router = APIRouter(prefix="/ai-search", tags=["AI Search"])

# Initialize OpenAI client for embeddings
openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


async def get_folder_and_subfolder_ids(folder_id: str, user_id: str) -> set:
    """Get the specified folder ID and all its subfolder IDs recursively"""
    folder_ids = {folder_id}
    folders_collection = await get_folders_collection()
    
    # Get all subfolders recursively
    to_process = [folder_id]
    while to_process:
        current_folder_id = to_process.pop(0)
        
        # Find all subfolders of the current folder
        subfolders = await folders_collection.find({
            "parent_folder_id": current_folder_id,
            "owner_id": user_id
        }).to_list(length=None)
        
        for subfolder in subfolders:
            subfolder_id = str(subfolder["_id"])
            if subfolder_id not in folder_ids:
                folder_ids.add(subfolder_id)
                to_process.append(subfolder_id)
    
    return folder_ids


def _initialize_category_structure(names: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Create an empty category structure based on the list of names."""
    categories: Dict[str, List[Dict[str, Any]]] = {}

    if names:
        categories[" and ".join(names)] = []  # images containing all names

    # 'others' category for any other results (partial/none)
    categories["others"] = []
    return categories


def _place_in_category(categories: Dict[str, List[Dict[str, Any]]],
                        file_doc: Dict[str, Any],
                        names_in_image: Set[str],
                        query_names: List[str]):
    """Place the file document in the appropriate category based on names present."""
    query_names_set = set(query_names)

    intersection = names_in_image & query_names_set

    if intersection and intersection == query_names_set:
        # All names present
        all_key = " and ".join(query_names) if query_names else "all"
        categories.setdefault(all_key, []).append(file_doc)
    else:
        # Anything else: partial, single, or no names
        categories["others"].append(file_doc)


@router.get("/", summary="AI-powered search across user images")
async def ai_search(
    query: str, 
    user_id: str = Depends(get_current_user_id),
    folder_id: str = None
):
    """Search user images using an enhanced textual query and categorize results by detected names.

    Args:
        query: The search query to enhance and search for
        user_id: The user ID for authentication and data access
        folder_id: Optional folder ID to limit search to files in this folder and its subfolders

    Steps:
    1. Enhance the query using Gemini (`enhance_search`) to obtain an enriched description and list of mentioned names.
    2. Query the vector database with the enhanced description to retrieve the top 20 matching images.
    3. If folder_id is provided, filter results to only include files from the specified folder and its subfolders.
    4. For every returned image, fetch its faces and determine which names (if any) appear in it.
    5. Group the images into categories:
       • `<name1> and <name2>` – images containing all mentioned names.
       • `only <nameX>` – images containing only one of the mentioned names.
       • `no names` – images where none of the mentioned names are detected.
    """
    try:
        # 1. Enhance query
        enhancement_result = await enhance_search(query)
        enhanced_query: str = enhancement_result.get("enhanced_query", query)
        mentioned_names: List[str] = enhancement_result.get("mentioned_names", [])

        # Normalise names to lowercase for comparison consistency
        mentioned_names = [name.lower() for name in mentioned_names]

        # 2. Generate embedding for the enhanced query
        embedding_response = await openai_client.embeddings.create(
            model=settings.OPENAI_EMBEDDING_MODEL,
            input=enhanced_query
        )
        query_vector = embedding_response.data[0].embedding
        
        # 3. Vector DB search (top 20)
        search_results = await vector_db.query(enhanced_query, user_id, query_vector, top_k=20)
        
        image_ids = [res["payload"]["image_id"] for res in search_results]

        # Prepare category structure
        categories: Dict[str, List[Dict[str, Any]]] = _initialize_category_structure(mentioned_names)

        if not image_ids:
            return {"categories": categories}

        # Fetch all files in one query
        files_collection = await get_files_collection()
        
        # If folder_id is provided, filter files to only include those from the specified folder and subfolders
        if folder_id:
            # Get all folder IDs (including subfolders)
            allowed_folder_ids = await get_folder_and_subfolder_ids(folder_id, user_id)
            
            # Filter files by folder_id
            file_docs_cursor = files_collection.find({
                "_id": {"$in": [ObjectId(i) for i in image_ids]},
                "folder_id": {"$in": list(allowed_folder_ids)}
            })
        else:
            # No folder filter, get all files
            file_docs_cursor = files_collection.find({"_id": {"$in": [ObjectId(i) for i in image_ids]}})
        
        file_docs = await file_docs_cursor.to_list(length=len(image_ids))
        file_docs_map = {str(doc["_id"]): doc for doc in file_docs}

        # Generate presigned URLs for all files in parallel
        presigned_url_tasks = []
        for res in search_results:
            img_id = res["payload"]["image_id"]
            file_doc = file_docs_map.get(img_id)
            if not file_doc:
                continue  # Skip missing docs

            # Convert ObjectId to string for response
            file_doc["_id"] = str(file_doc["_id"])

            # Create tasks for presigned URL generation
            if file_doc.get("s3_key"):
                presigned_url_tasks.append(s3_service.generate_presigned_url(file_doc["s3_key"], 3600))
            else:
                presigned_url_tasks.append(None)
                
            if file_doc.get("thumbnail_s3_key"):
                presigned_url_tasks.append(s3_service.generate_presigned_url(file_doc["thumbnail_s3_key"], 3600))
            else:
                presigned_url_tasks.append(None)

        # Execute all presigned URL generation in parallel
        if presigned_url_tasks:
            presigned_urls = await asyncio.gather(*[task for task in presigned_url_tasks if task is not None])
            
            # Assign presigned URLs back to file documents
            url_index = 0
            for res in search_results:
                img_id = res["payload"]["image_id"]
                file_doc = file_docs_map.get(img_id)
                if not file_doc:
                    continue

                if file_doc.get("s3_key"):
                    file_doc["s3_url"] = presigned_urls[url_index]
                    url_index += 1
                if file_doc.get("thumbnail_s3_key"):
                    file_doc["thumbnail_s3_url"] = presigned_urls[url_index]
                    url_index += 1

        # Iterate over vector results preserving relevance order
        for res in search_results:
            img_id = res["payload"]["image_id"]
            file_doc = file_docs_map.get(img_id)
            if not file_doc:
                continue  # Skip missing docs

            # Check file type to handle differently
            file_type = file_doc.get("file_type", "image")
            
            if file_type == "video":
                # For videos, don't detect faces, just categorize based on content
                # Videos are categorized in "videos" category
                if "videos" not in categories:
                    categories["videos"] = []
                categories["videos"].append(file_doc)
            else:
                # For images, fetch faces and categorize by names
                faces_info = await get_faces_for_file(img_id, user_id)
                names_in_image = {f.get("name", "").lower() for f in faces_info if f.get("name")}
                _place_in_category(categories, file_doc, names_in_image, mentioned_names)

        return {"categories": categories}

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc







