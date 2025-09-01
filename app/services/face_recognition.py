import mediapipe as mp
import numpy as np
from PIL import Image
import io
from datetime import datetime, timezone
from bson import ObjectId
from app.database import get_faces_collection
from app.models.face import FaceInDB, FileReference

 
mp_face = mp.solutions.face_detection.FaceDetection(model_selection=1 ,min_detection_confidence=0.3)

def get_interpreter():
    from main import interpreter
    return interpreter

 
def preprocess_face(image: np.ndarray, box: dict, target_size=(112, 112)) -> np.ndarray:
    h, w, _ = image.shape
    xmin = int(box.xmin * w)
    ymin = int(box.ymin * h)
    box_w = int(box.width * w)
    box_h = int(box.height * h)
    crop = image[ymin:ymin+box_h, xmin:xmin+box_w]
    face = Image.fromarray(crop).resize(target_size)
    face_np = np.asarray(face).astype(np.float32)
     
    face_np = (face_np - 127.5) / 128.0
    return np.expand_dims(face_np, axis=0)

 
def detect_and_embed(image_bytes: bytes) -> list:
    interpreter = get_interpreter()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img_np = np.array(image)

    results = mp_face.process(img_np)
    embeddings = []

    if not results.detections:
        return embeddings

    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()

    for det in results.detections:
        face_tensor = preprocess_face(img_np, det.location_data.relative_bounding_box)
        interpreter.set_tensor(input_details[0]['index'], face_tensor)
        interpreter.invoke()
        emb = interpreter.get_tensor(output_details[0]['index'])[0]
        emb = emb / np.linalg.norm(emb)
        rel_box = det.location_data.relative_bounding_box

         
        bbox = {
            'xmin': float(rel_box.xmin),
            'ymin': float(rel_box.ymin),
            'width': float(rel_box.width),
            'height': float(rel_box.height),
        }
        embeddings.append({
            'bbox': bbox,
            'embedding': emb.tolist()
        })

    return embeddings

async def face_detection(file, owner_id, file_id):
    """
    Detect faces in an image and manage them in the faces collection.
    Returns list of face references for the file and count of unknown faces.
    """
    try:
        print("face detection called")
        content = file.read()
        # Run the heavy synchronous face detection in thread pool
        import asyncio
        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(None, detect_and_embed, content)
        if not embeddings:
            print("no embeddings found")
            return [], 0

        faces_coll = await get_faces_collection()
        face_references = []
        unknown_faces_count = 0
        
        # Get all existing faces for this owner
        existing_faces = await faces_coll.find(
            {'owner_id': owner_id},
            {'embedding': 1, 'name': 1}
        ).to_list(length=None)

        for detected_face in embeddings:
            emb = np.array(detected_face['embedding'], dtype=np.float32)
            bbox = detected_face['bbox']
            matched_face_id = None
            face_name = None
            
             
            for existing_face in existing_faces:
                existing_emb = np.array(existing_face['embedding'], dtype=np.float32)
                distance = np.linalg.norm(existing_emb - emb)
                
                if distance <= 0.4:  
                    matched_face_id = str(existing_face['_id'])
                    face_name = existing_face.get('name')
                    break
            
            if matched_face_id:
                 
                file_ref = FileReference(
                    file_id=file_id,
                    bbox=bbox
                )
                
                await faces_coll.update_one(
                    {'_id': ObjectId(matched_face_id)},
                    {
                        '$push': {'file_references': file_ref.dict()},
                        '$set': {'updated_at': datetime.now(timezone.utc)}
                    }
                )
            else:
                 
                new_face = FaceInDB(
                    owner_id=owner_id,
                    embedding=detected_face['embedding'],
                    name=None,  # Unknown face initially
                    file_references=[FileReference(
                        file_id=file_id,
                        bbox=bbox
                    )]
                )
                
                result = await faces_coll.insert_one(new_face.dict(by_alias=True))
                matched_face_id = str(result.inserted_id)
                unknown_faces_count += 1
            
             
            face_references.append({
                'face_id': matched_face_id,
                'bbox': bbox
            })
        
        return face_references, unknown_faces_count
        
    except Exception as e:
        print("exception during face detection:", str(e))
        return [], 0


async def get_unknown_faces(owner_id, limit=50, skip=0):
    """Get unknown faces (faces without names) for an owner"""
    try:
        faces_coll = await get_faces_collection()
        
        unknown_faces = await faces_coll.find(
            {'owner_id': owner_id, 'name': None},
            {'embedding': 0}  # Exclude embedding from response
        ).skip(skip).limit(limit).to_list(length=limit)
        
        return unknown_faces
    except Exception as e:
        print("exception getting unknown faces:", str(e))
        return []


async def update_face_name(face_id, name, owner_id):
    """Update the name of a face, affecting all files containing this face"""
    try:
        faces_coll = await get_faces_collection()
        
        result = await faces_coll.update_one(
            {'_id': ObjectId(face_id), 'owner_id': owner_id},
            {
                '$set': {
                    'name': name,
                    'updated_at': datetime.now(timezone.utc)
                }
            }
        )
        
        if result.modified_count > 0:
            # Get the updated face to return file count
            face = await faces_coll.find_one({'_id': ObjectId(face_id)})
            if face:
                return len(face.get('file_references', []))
        
        return 0
    except Exception as e:
        print("exception updating face name:", str(e))
        return 0


async def get_face_by_id(face_id, owner_id):
    """Get a single face by its ID"""
    try:
        faces_coll = await get_faces_collection()
        
        face = await faces_coll.find_one(
            {'_id': ObjectId(face_id), 'owner_id': owner_id},
            {'embedding': 0}  # Exclude embedding from response
        )
        
        return face
    except Exception as e:
        print("exception getting face by id:", str(e))
        return None


async def search_faces_by_name(owner_id, name_pattern, limit=20, skip=0):
    """Search faces by name pattern with regex support"""
    try:
        faces_coll = await get_faces_collection()
        
        # Build search query - if name_pattern is provided, use regex search
        query = {'owner_id': owner_id}
        if name_pattern:
            # Use case-insensitive regex search
            query['name'] = {'$regex': name_pattern, '$options': 'i'}
        else:
            # If no pattern provided, get all named faces
            query['name'] = {'$ne': None}
        
        # Get total count for pagination
        total_count = await faces_coll.count_documents(query)
        
        # Get faces with pagination
        faces = await faces_coll.find(
            query,
            {'embedding': 0}  # Exclude embedding from response
        ).skip(skip).limit(limit).to_list(length=limit)
        
        return faces, total_count
    except Exception as e:
        print("exception searching faces by name:", str(e))
        return [], 0


async def merge_faces(face_id, target_face_id, owner_id):
    """Merge source face into target face and delete source face"""
    try:
        faces_coll = await get_faces_collection()
        
        # Get both faces to verify ownership and existence
        source_face = await faces_coll.find_one({
            '_id': ObjectId(face_id),
            'owner_id': owner_id
        })
        
        target_face = await faces_coll.find_one({
            '_id': ObjectId(target_face_id),
            'owner_id': owner_id
        })
        
        if not source_face:
            return None, "Source face not found"
        
        if not target_face:
            return None, "Target face not found"
        
        if face_id == target_face_id:
            return None, "Cannot merge face with itself"
        
        # Get file references from source face
        source_file_refs = source_face.get('file_references', [])
        target_file_refs = target_face.get('file_references', [])
        
        # Merge file references (avoid duplicates)
        existing_file_ids = {ref['file_id'] for ref in target_file_refs}
        new_file_refs = [ref for ref in source_file_refs if ref['file_id'] not in existing_file_ids]
        
        # Update target face with merged file references
        merged_file_refs = target_file_refs + new_file_refs
        
        await faces_coll.update_one(
            {'_id': ObjectId(target_face_id)},
            {
                '$set': {
                    'file_references': merged_file_refs,
                    'updated_at': datetime.now(timezone.utc)
                }
            }
        )
        
        # Delete source face
        await faces_coll.delete_one({'_id': ObjectId(face_id)})
        
        return len(new_file_refs), None
        
    except Exception as e:
        print("exception merging faces:", str(e))
        return None, f"Failed to merge faces: {str(e)}"


async def get_faces_for_file(file_id, owner_id):
    """Get all faces that appear in a specific file"""
    try:
        faces_coll = await get_faces_collection()
        
        faces = await faces_coll.find(
            {
                'owner_id': owner_id,
                'file_references.file_id': file_id
            },
            {'embedding': 0}  # Exclude embedding from response
        ).to_list(length=None)
        
        # Extract only the relevant file references for this file
        result = []
        for face in faces:
            for file_ref in face.get('file_references', []):
                if file_ref['file_id'] == file_id:
                    result.append({
                        'face_id': str(face['_id']),
                        'name': face.get('name'),
                        'bbox': file_ref['bbox']
                    })
                    break
        
        return result
    except Exception as e:
        print("exception getting faces for file:", str(e))
        return []