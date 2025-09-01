from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import JSONResponse
from typing import List, Optional
from bson import ObjectId
from datetime import datetime, timezone

from app.models.face import (
    Face, FaceUpdate, FaceNameUpdateRequest, FaceNameUpdateResponse,
    UnknownFacesResponse, PaginatedFacesResponse, FaceDetailResponse,
    FaceNameSuggestionResponse, PaginatedFaceThumbnailsResponse,
    FaceMergeRequest, FaceMergeResponse
)
from app.models.user import User
from app.dependencies import get_current_user, get_current_user_id
from app.database import get_faces_collection, get_files_collection
from app.services.face_recognition import get_unknown_faces, update_face_name, get_faces_for_file, get_face_by_id, search_faces_by_name, merge_faces
from app.services.s3 import s3_service
from app.utils import calculate_pagination_metadata, calculate_skip_from_page

router = APIRouter(prefix="/faces", tags=["Faces"])


@router.get("/name-suggestion", response_model=FaceNameSuggestionResponse)
async def face_name_suggestion_endpoint(
    name_pattern: str = Query("", description="Name pattern to search (supports regex)"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    user_id: str = Depends(get_current_user_id)
):
    """Search faces by name pattern and return suggestions with thumbnails"""
    skip = calculate_skip_from_page(page, per_page)
    
    # Search faces by name pattern
    faces, total_count = await search_faces_by_name(user_id, name_pattern, per_page, skip)
    
    # Format faces with first file reference as thumbnail
    files_collection = await get_files_collection()
    suggestions = []
    
    for face in faces:
        file_references = face.get('file_references', [])
        if not file_references:
            continue  # Skip faces without file references
        
        # Get the first file reference as thumbnail
        first_file_ref = file_references[0]
        
        # Get file info for URL generation
        file_doc = await files_collection.find_one(
            {'_id': ObjectId(first_file_ref['file_id']), 'archival_status': 'active'},
            {'s3_key': 1, 'filename': 1}
        )
        
        if file_doc:
            s3_url = await s3_service.generate_presigned_url(file_doc['s3_key'], 3600)
            suggestions.append({
                'face_id': str(face['_id']),
                'name': face.get('name'),
                'thumbnail_s3_url': s3_url,
                'thumbnail_bbox': first_file_ref['bbox'],
                'thumbnail_filename': file_doc['filename']
            })
    
    # Create pagination metadata
    meta = calculate_pagination_metadata(total_count, page, per_page)
    
    return FaceNameSuggestionResponse(
        data=suggestions,
        meta=meta
    )


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
    
    # Create pagination metadata
    meta = calculate_pagination_metadata(total_count, page, per_page)
    
    return UnknownFacesResponse(
        data=formatted_faces,
        meta=meta
    )


@router.get("/{face_id}", response_model=FaceDetailResponse)
async def get_face_endpoint(
    face_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """Get a single face by its ID with file references"""
    try:
        # Validate ObjectId format
        if not ObjectId.is_valid(face_id):
            raise HTTPException(
                status_code=400,
                detail="Invalid face ID format"
            )
        
        # Get the face from database
        face = await get_face_by_id(face_id, user_id)
        
        if not face:
            raise HTTPException(
                status_code=404,
                detail="Face not found"
            )
        
        # Format face with file information
        files_collection = await get_files_collection()
        formatted_file_references = []
        
        for file_ref in face.get('file_references', []):
            # Get file info for URL generation
            file_doc = await files_collection.find_one(
                {'_id': ObjectId(file_ref['file_id']), 'archival_status': 'active'},
                {'s3_key': 1, 'filename': 1}
            )
            
            if file_doc:
                s3_url = await s3_service.generate_presigned_url(file_doc['s3_key'], 3600)
                formatted_file_references.append({
                    'file_id': file_ref['file_id'],
                    'filename': file_doc['filename'],
                    'bbox': file_ref['bbox'],
                    's3_url': s3_url,
                    'added_at': file_ref.get('added_at', datetime.now(timezone.utc))
                })
        
        return FaceDetailResponse(
            face_id=str(face['_id']),
            name=face.get('name'),
            file_references=formatted_file_references,
            created_at=face.get('created_at', datetime.now(timezone.utc)),
            updated_at=face.get('updated_at', datetime.now(timezone.utc))
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve face: {str(e)}"
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


@router.post("/merge", response_model=FaceMergeResponse)
async def merge_faces_endpoint(
    request: FaceMergeRequest,
    user_id: str = Depends(get_current_user_id)
):
    """Merge source face into target face"""
    try:
        # Validate ObjectId formats
        if not ObjectId.is_valid(request.face_id):
            raise HTTPException(
                status_code=400,
                detail="Invalid source face ID format"
            )
        
        if not ObjectId.is_valid(request.target_face_id):
            raise HTTPException(
                status_code=400,
                detail="Invalid target face ID format"
            )
        
        # Perform the merge
        files_moved, error = await merge_faces(request.face_id, request.target_face_id, user_id)
        
        if error:
            if "not found" in error:
                raise HTTPException(
                    status_code=404,
                    detail=error
                )
            elif "Cannot merge face with itself" in error:
                raise HTTPException(
                    status_code=400,
                    detail=error
                )
            else:
                raise HTTPException(
                    status_code=500,
                    detail=error
                )
        
        # Get target face info for response
        target_face = await get_face_by_id(request.target_face_id, user_id)
        target_face_name = target_face.get('name') if target_face else None
        
        return FaceMergeResponse(
            message="Faces merged successfully",
            target_face_id=request.target_face_id,
            merged_face_id=request.face_id,
            total_file_references_moved=files_moved,
            target_face_name=target_face_name
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to merge faces: {str(e)}"
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


@router.get("/", response_model=PaginatedFaceThumbnailsResponse)
async def get_all_faces(
    user_id: str = Depends(get_current_user_id),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    named_only: bool = Query(False, description="Only return faces with names")
):
    """Get all faces for the current user with pagination and thumbnails"""
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
    
    # Format faces with thumbnail information
    files_collection = await get_files_collection()
    face_list = []
    
    for face in faces:
        file_references = face.get('file_references', [])
        total_file_references = len(file_references)
        
        thumbnail_s3_url = None
        thumbnail_bbox = None
        thumbnail_filename = None
        
        if file_references:
            # Get the first file reference as thumbnail
            first_file_ref = file_references[0]
            
            # Get file info for URL generation
            file_doc = await files_collection.find_one(
                {'_id': ObjectId(first_file_ref['file_id']), 'archival_status': 'active'},
                {'s3_key': 1, 'filename': 1}
            )
            
            if file_doc:
                thumbnail_s3_url = await s3_service.generate_presigned_url(file_doc['s3_key'], 3600)
                thumbnail_bbox = first_file_ref['bbox']
                thumbnail_filename = file_doc['filename']
        
        face_list.append({
            'face_id': str(face['_id']),
            'name': face.get('name'),
            'thumbnail_s3_url': thumbnail_s3_url,
            'thumbnail_bbox': thumbnail_bbox,
            'thumbnail_filename': thumbnail_filename,
            'total_file_references': total_file_references,
            'created_at': face.get('created_at', datetime.now(timezone.utc)),
            'updated_at': face.get('updated_at', datetime.now(timezone.utc))
        })
    
    pagination_meta = calculate_pagination_metadata(total, page, per_page)
    
    return PaginatedFaceThumbnailsResponse(
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