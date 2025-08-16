from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import JSONResponse
from typing import List, Optional
from bson import ObjectId
from datetime import datetime, timezone

from app.models.face import (
    Face, FaceUpdate, FaceNameUpdateRequest, FaceNameUpdateResponse,
    UnknownFacesResponse, PaginatedFacesResponse
)
from app.models.user import User
from app.dependencies import get_current_user, get_current_user_id
from app.database import get_faces_collection, get_files_collection
from app.services.face_recognition import get_unknown_faces, update_face_name, get_faces_for_file
from app.services.s3 import s3_service
from app.utils import calculate_pagination_metadata, calculate_skip_from_page

router = APIRouter(prefix="/faces", tags=["Faces"])


@router.get("/unknown", response_model=UnknownFacesResponse)
async def get_unknown_faces_endpoint(
    user_id: str = Depends(get_current_user_id),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page")
):
    """Get all unknown faces (faces without names) for the current user"""
    skip = calculate_skip_from_page(page, per_page)
    
    unknown_faces = await get_unknown_faces(user_id, per_page, skip)
    
    # Get total count for pagination
    faces_collection = await get_faces_collection()
    total_count = await faces_collection.count_documents({'owner_id': user_id, 'name': None})
    
    # Format faces with file information
    files_collection = await get_files_collection()
    formatted_faces = []
    
    for face in unknown_faces:
        face_data = {
            'face_id': str(face['_id']),
            'file_references': []
        }
        
        for file_ref in face.get('file_references', []):
            # Get file info for URL generation
            file_doc = await files_collection.find_one(
                {'_id': ObjectId(file_ref['file_id']), 'archival_status': 'active'},
                {'s3_key': 1, 'filename': 1}
            )
            
            if file_doc:
                s3_url = await s3_service.generate_presigned_url(file_doc['s3_key'], 3600)
                face_data['file_references'].append({
                    'file_id': file_ref['file_id'],
                    'filename': file_doc['filename'],
                    'bbox': file_ref['bbox'],
                    's3_url': s3_url,
                    'added_at': file_ref.get('added_at', datetime.now(timezone.utc))
                })
        
        if face_data['file_references']:  # Only include faces that have active files
            formatted_faces.append(face_data)
    
    return UnknownFacesResponse(
        faces=formatted_faces,
        total_count=total_count
    )


@router.put("/name", response_model=FaceNameUpdateResponse)
async def update_face_name_endpoint(
    request: FaceNameUpdateRequest,
    user_id: str = Depends(get_current_user_id)
):
    """Update the name of a face, affecting all files containing this face"""
    if not request.name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Face name cannot be empty"
        )
    
    files_affected = await update_face_name(request.face_id, request.name.strip(), user_id)
    
    if files_affected == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Face not found or you don't have permission to update it"
        )
    
    return FaceNameUpdateResponse(
        message="Face name updated successfully",
        face_id=request.face_id,
        name=request.name.strip(),
        files_affected=files_affected
    )


@router.get("/file/{file_id}")
async def get_faces_in_file(
    file_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """Get all faces that appear in a specific file"""
    # Verify file ownership
    files_collection = await get_files_collection()
    file_doc = await files_collection.find_one({
        '_id': ObjectId(file_id),
        'owner_id': user_id,
        'archival_status': 'active'
    })
    
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found or you don't have access to it"
        )
    
    faces = await get_faces_for_file(file_id, user_id)
    
    return {
        'file_id': file_id,
        'filename': file_doc['filename'],
        'faces': faces
    }


@router.get("/", response_model=PaginatedFacesResponse)
async def get_all_faces(
    user_id: str = Depends(get_current_user_id),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    named_only: bool = Query(False, description="Only return faces with names")
):
    """Get all faces for the current user with pagination"""
    faces_collection = await get_faces_collection()
    
    # Build query
    query = {'owner_id': user_id}
    if named_only:
        query['name'] = {'$ne': None}
    
    # Calculate pagination
    skip = calculate_skip_from_page(page, per_page)
    total = await faces_collection.count_documents(query)
    
    # Get faces
    faces_cursor = faces_collection.find(
        query,
        {'embedding': 0}  # Exclude embedding from response
    ).skip(skip).limit(per_page).sort('updated_at', -1)
    
    faces = await faces_cursor.to_list(length=per_page)
    
    # Convert to response format
    face_list = []
    for face in faces:
        face_data = Face(
            id=str(face['_id']),
            owner_id=face['owner_id'],
            embedding=[],  # Empty since we excluded it
            name=face.get('name'),
            file_references=face.get('file_references', []),
            created_at=face['created_at'],
            updated_at=face['updated_at']
        )
        face_list.append(face_data)
    
    pagination_meta = calculate_pagination_metadata(total, page, per_page)
    
    return PaginatedFacesResponse(
        data=face_list,
        meta=pagination_meta
    )


@router.delete("/{face_id}")
async def delete_face(
    face_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """Delete a face and all its references"""
    faces_collection = await get_faces_collection()
    
    # Verify ownership and delete
    result = await faces_collection.delete_one({
        '_id': ObjectId(face_id),
        'owner_id': user_id
    })
    
    if result.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Face not found or you don't have permission to delete it"
        )
    
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Face deleted successfully", "face_id": face_id}
    )


@router.get("/stats")
async def get_face_stats(user_id: str = Depends(get_current_user_id)):
    """Get face statistics for the current user"""
    faces_collection = await get_faces_collection()
    
    total_faces = await faces_collection.count_documents({'owner_id': user_id})
    named_faces = await faces_collection.count_documents({'owner_id': user_id, 'name': {'$ne': None}})
    unknown_faces = total_faces - named_faces
    
    return {
        'total_faces': total_faces,
        'named_faces': named_faces,
        'unknown_faces': unknown_faces,
        'completion_percentage': round((named_faces / total_faces * 100) if total_faces > 0 else 0, 2)
    }