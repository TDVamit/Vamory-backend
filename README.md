# Gallery Backend API

A comprehensive FastAPI-based gallery application backend with MongoDB, AWS S3 integration, JWT authentication, advanced file management features, and intelligent storage class management.

## Features

### 🔐 Authentication & Authorization
- User registration and login
- JWT access tokens with refresh token support
- Secure password hashing with bcrypt
- Role-based access control for folders and files

### 📁 Folder Management
- Create, read, update, delete folders with storage type specification
- Nested folder support (subfolders) with storage type inheritance
- Folder sharing with different access levels (read, write, admin)
- Access control per folder
- **Storage Type Management**: Choose from three S3 storage classes for folders
- **Cascading Storage**: Storage type automatically applies to all children (folders & files)
- Bulk storage class transitions for entire folder hierarchies
- **📏 Smart Size Calculation**: Recursive folder size calculation including all files and subfolders
- **📊 Detailed Statistics**: File type breakdowns, size analysis, and comprehensive folder stats

### 📄 File Management
- Upload files to S3 with configurable storage classes
- Support for images AND videos with different size limits (up to 10GB for videos)
- **Intelligent Thumbnail Management**: Automatically generate/delete thumbnails based on storage type
- File metadata extraction
- Presigned URLs for secure file downloads
- File type validation and size limits
- **Storage Class Transitions**: Move files between storage classes with automatic S3 operations
- **📐 Size Information**: All file responses include detailed size information in bytes

### 🖼️ Image Processing
- Automatic WebP thumbnail generation (only for non-Deep Archive storage)
- Image metadata extraction (dimensions, format, etc.)
- Support for various image formats (JPEG, PNG, GIF, etc.)
- EXIF data handling and auto-orientation
- **Smart Thumbnail Lifecycle**: Thumbnails deleted when moving to Deep Archive, created when moving back

### 🎥 Video Support
- Support for multiple video formats (MP4, AVI, MOV, WebM, etc.)
- **Massive file support**: Up to 10GB video files with multipart uploads
- Video metadata extraction capabilities
- Future support for video thumbnails (respects storage class rules)

### ☁️ Smart Storage Management
**Three S3 Storage Classes:**
- **S3 Standard – Infrequent Access (IA)**: Cost-effective for infrequently accessed data
- **S3 Glacier Instant Retrieval**: Ultra-low cost with millisecond retrieval
- **S3 Glacier Deep Archive**: Lowest cost for long-term archival (12+ hour retrieval)

**Intelligent Features:**
- **Default Storage**: New folders default to Glacier Instant Retrieval
- **Status Tracking**: Real-time folder status (active/inactive/converting)
- **Smart Conversions**: Automatic job tracking with estimated completion times
- **Deep Archive Bulk Mode**: 48-hour conversions with significant cost savings
- **Time-Limited Retrievals**: Retrieve for specific periods (1-365 days) or permanently
- **Auto-Return System**: Permanent retrievals automatically move to Standard storage
- **No Thumbnails in Deep Archive**: Saves additional storage costs
- **Automatic Thumbnail Management**: Smart creation/deletion during transitions
- **Bulk Operations**: Archive entire folder hierarchies efficiently
- **Storage Inheritance**: Children inherit parent folder storage type
- **Conversion Monitoring**: Track job progress and get status updates

**Conversion Modes for Deep Archive Retrieval:**
- **Standard**: 1-12 hours retrieval, higher cost (for faster access)
- **Bulk**: 5-48 hours retrieval, lowest cost (recommended for large archives)

**Deep Archive Conversion Rules:**
- Converting TO Deep Archive: Always uses Bulk mode, includes all children
- Converting FROM Deep Archive: Choose retrieval mode and duration
- Status changes: active → converting → inactive/active
- Cost optimization: Bulk mode provides significant savings for large datasets

## Tech Stack

- **FastAPI** - Modern, fast web framework for building APIs
- **MongoDB** - Document database with Motor (async driver)
- **AWS S3** - File storage with multiple storage classes
- **JWT** - Secure authentication
- **Pillow** - Image processing and thumbnail generation
- **Pydantic** - Data validation and settings management

## Setup Instructions

### 1. Clone the Repository

```bash
git clone <repository-url>
cd backend
```

### 2. Install Dependencies

```bash
# Using pip
pip install -r requirements.txt

# Or using the pyproject.toml
pip install -e .
```

### 3. Environment Configuration

Copy the example environment file and configure your credentials:

```bash
cp env.example .env
```

Edit the `.env` file with your actual credentials:

```env
# Database
MONGODB_URL=mongodb://localhost:27017
DATABASE_NAME=gallery_db

# JWT Authentication
SECRET_KEY=your-super-secret-key-change-this-in-production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# AWS S3 Configuration
AWS_ACCESS_KEY_ID=your-aws-access-key
AWS_SECRET_ACCESS_KEY=your-aws-secret-key
AWS_REGION=us-east-1
S3_BUCKET_NAME=your-s3-bucket-name

# Application Settings
DEBUG=True
HOST=0.0.0.0
PORT=8000

# Upload Settings
MAX_FILE_SIZE=100MB
MAX_VIDEO_SIZE=10GB
ALLOWED_IMAGE_EXTENSIONS=jpg,jpeg,png,gif,webp,bmp,tiff
ALLOWED_VIDEO_EXTENSIONS=mp4,avi,mov,wmv,flv,webm,mkv,m4v
THUMBNAIL_SIZE=300
THUMBNAIL_QUALITY=85
```

### 4. MongoDB Setup

Ensure MongoDB is running locally or provide a remote MongoDB connection string.

```bash
# Local MongoDB (default port 27017)
mongod

# Or use MongoDB Atlas cloud service
```

### 5. AWS S3 Setup

1. Create an AWS S3 bucket
2. Configure bucket permissions for your use case
3. Create IAM user with S3 access permissions
4. Add the credentials to your `.env` file

### 6. Run the Application

```bash
# Development mode with auto-reload
python main.py

# Or using the simple startup script
python start.py

# Or using uvicorn directly
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The API will be available at `http://localhost:8000`

## API Documentation

Once the application is running, you can access:

- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **OpenAPI JSON**: `http://localhost:8000/openapi.json`

## API Endpoints

### Authentication
- `POST /api/v1/auth/register` - Register a new user
- `POST /api/v1/auth/login` - Login user
- `POST /api/v1/auth/refresh` - Refresh access token
- `POST /api/v1/auth/logout` - Logout user
- `GET /api/v1/auth/me` - Get current user info
- `GET /api/v1/auth/users` - Get all users with search, pagination, and sorting
- `GET /api/v1/auth/users/search` - Search users (optimized for autocomplete)

### Folders
- `POST /api/v1/folders/` - Create folder (with storage type and status)
- `GET /api/v1/folders/` - List folders with search, pagination, and sorting
- `GET /api/v1/folders/root` - Get root folders with advanced filtering
- `GET /api/v1/folders/{folder_id}` - Get folder details (includes total size and status)
- `GET /api/v1/folders/{folder_id}/stats` - Get detailed folder statistics with size breakdowns
- `PUT /api/v1/folders/{folder_id}` - Update folder name only
- `DELETE /api/v1/folders/{folder_id}` - Delete folder and all contents
- `POST /api/v1/folders/{folder_id}/share` - Share folder with user (owner only)
- `DELETE /api/v1/folders/{folder_id}/share/{user_email}` - Revoke folder access (owner only)
- `POST /api/v1/folders/{folder_id}/change-storage-type` - **Smart storage conversion with status tracking**
- `GET /api/v1/folders/{folder_id}/conversion-status` - Get conversion job status

### Files
- `POST /api/v1/files/upload?folder_id={folder_id}` - Upload file (respects folder storage type)
- `GET /api/v1/files/folder/{folder_id}` - List files with comprehensive filtering and search
- `GET /api/v1/files/{file_id}` - Get file details
- `GET /api/v1/files/{file_id}/download` - Get download URL (checks storage class)
- `GET /api/v1/files/{file_id}/thumbnail` - Get thumbnail URL
- `PUT /api/v1/files/{file_id}` - Update file (handles storage type inheritance)
- `DELETE /api/v1/files/{file_id}` - Delete file

### 🔍 **Search, Pagination & Sorting Features**

**All "get all" endpoints now support:**
- ✅ **Text Search**: Search by relevant fields (name, email, filename, etc.)
- ✅ **Pagination**: `skip` and `limit` parameters with comprehensive metadata
- ✅ **Sorting**: `sort_by` and `sort_order` (asc/desc) parameters
- ✅ **Filtering**: Type-specific filters (storage type, file type, size ranges, etc.)

#### **📋 Paginated Response Format**

All paginated endpoints now return responses in this format:

```json
{
  "data": [...],  // Array of items
  "meta": {
    "total_count": 150,      // Total number of items available
    "page_count": 15,        // Total number of pages
    "current_page": 3,       // Current page number (1-based)
    "per_page": 10,          // Number of items per page
    "has_next": true,        // Whether there is a next page
    "has_prev": true,        // Whether there is a previous page
    "next_page": 4,          // Next page number (if available)
    "prev_page": 2           // Previous page number (if available)
  }
}
```

#### **📋 Complete Parameter Reference**

| Endpoint | Search Fields | Sort Fields | Filters | Special Features |
|----------|---------------|-------------|---------|-----------------|
| **`/auth/users`** | `search` (name, email) | `full_name`, `email`, `created_at`, `updated_at` | Active users only | Smart relevance sorting |
| **`/auth/users/search`** | `q` (min 2 chars) | Auto-sorted by relevance | Active users only | Optimized for autocomplete |
| **`/folders/`** | `search` (name) | `name`, `created_at`, `updated_at`, `file_count`, `subfolder_count`, `total_size` | `storage_type`, `parent_folder_id`, `include_size` | Access control aware, optional size calculation |
| **`/folders/root`** | `search` (name) | `name`, `created_at`, `updated_at`, `file_count`, `subfolder_count`, `total_size` | `storage_type`, `only_true_roots`, `include_size` | Root folders only, optional size calculation |
| **`/files/folder/{id}`** | `search` (filename) | `filename`, `file_size`, `created_at`, `updated_at`, `file_type` | `file_type`, `storage_type`, `min_size`, `max_size` | Comprehensive filtering, size-based filtering |

#### **🎯 Common Parameters**
- **Pagination**: `skip=0&limit=50` (all endpoints)
- **Sorting**: `sort_by=field&sort_order=asc|desc` (all endpoints)
- **Search**: Case-insensitive regex matching
- **Limits**: Max 100 items per request (adjustable)

### 🗄️ Storage Management
All storage management is handled at the folder level:
- `POST /api/v1/folders/{folder_id}/change-storage-type` - Change storage type with cascading
- `PUT /api/v1/folders/{folder_id}` - Update folder including storage type

## Usage Examples

### 1. Create Folder with Storage Type

```bash
# Create folder with Glacier Instant Retrieval (default)
curl -X POST "http://localhost:8000/api/v1/folders/" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "My Photos",
       "storage_type": "GLACIER_IR"
     }'

# Create folder with Deep Archive (no thumbnails)
curl -X POST "http://localhost:8000/api/v1/folders/" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "Archive Vault",
       "storage_type": "DEEP_ARCHIVE"
     }'
```

### 2. Update Folder Name

```bash
# Update folder name only
curl -X PUT "http://localhost:8000/api/v1/folders/FOLDER_ID" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "New Folder Name"
     }'
```

### 3. Upload Large Video Files

```bash
# Upload a video file up to 10GB
curl -X POST "http://localhost:8000/api/v1/files/upload?folder_id=FOLDER_ID" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
     -F "file=@/path/to/your/large_video.mp4"
```

### 4. Smart Storage Type Conversions

```bash
# Convert to Deep Archive (bulk mode, 48 hours, lower cost)
curl -X POST "http://localhost:8000/api/v1/folders/FOLDER_ID/change-storage-type" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "folder_id": "FOLDER_ID",
       "new_storage_type": "DEEP_ARCHIVE",
       "apply_to_children": true
     }'

# Retrieve from Deep Archive for 30 days (bulk mode)
curl -X POST "http://localhost:8000/api/v1/folders/FOLDER_ID/change-storage-type" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "folder_id": "FOLDER_ID",
       "new_storage_type": "STANDARD_IA",
       "retrieval_days": 30,
       "retrieval_mode": "Bulk"
     }'

# Permanent retrieval from Deep Archive (standard mode)
curl -X POST \
  'http://localhost:8000/api/v1/folders/6862ec2d08bb90c56191a62b/change-storage-type' \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -F 'new_storage_type=GLACIER_IR' \
  -F 'retrieval_mode=Standard'

# Check conversion status
curl -X GET "http://localhost:8000/api/v1/folders/FOLDER_ID/conversion-status" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

**Response Example:**
```json
{
  "message": "Started bulk conversion to Deep Archive (48 hours, lower cost)",
  "folder_id": "FOLDER_ID",
  "folders_updated": 5,
  "files_updated": 150,
  "new_storage_type": "DEEP_ARCHIVE",
  "new_status": "converting",
  "conversion_job_id": "conv_abc123def456",
  "estimated_completion_time": "2024-01-15T14:30:00Z",
  "is_immediate": false,
  "retrieval_mode": "Bulk",
  "bulk_mode_savings": "Bulk mode saves approximately $3.00 compared to Standard mode"
}
```

### 5. Folder Sharing (Owner Only)

```bash
# Share folder with another user (only folder owner can do this)
curl -X POST "http://localhost:8000/api/v1/folders/FOLDER_ID/share" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "user_email": "colleague@example.com",
       "access_level": "write"
     }'

# Revoke access (only folder owner can do this)
curl -X DELETE "http://localhost:8000/api/v1/folders/FOLDER_ID/share/colleague@example.com" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### 6. User Search and Management

```bash
# Get all users with pagination
curl -X GET "http://localhost:8000/api/v1/auth/users?skip=0&limit=20" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# Search users by name or email
curl -X GET "http://localhost:8000/api/v1/auth/users?search=john" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# Quick user search for autocomplete (optimized)
curl -X GET "http://localhost:8000/api/v1/auth/users/search?q=john.doe" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# Find users for folder sharing
curl -X GET "http://localhost:8000/api/v1/auth/users/search?q=colleague&limit=5" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### 7. Advanced Search, Pagination & Sorting

```bash
# FOLDERS: Search, sort, and paginate
# Get folders sorted by creation date (newest first) with pagination metadata
curl -X GET "http://localhost:8000/api/v1/folders/?sort_by=created_at&sort_order=desc&limit=10" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# Response format:
# {
#   "data": [
#     {
#       "id": "folder_id",
#       "name": "My Folder",
#       "description": "Folder description",
#       "storage_type": "GLACIER_IR",
#       "file_count": 25,
#       "subfolder_count": 3,
#       "thumbnail_url": "https://presigned-url...",
#       "access_level": "ADMIN",
#       ...
#     }
#   ],
#   "meta": {
#     "total_count": 50,
#     "page_count": 5,
#     "current_page": 1,
#     "per_page": 10,
#     "has_next": true,
#     "has_prev": false,
#     "next_page": 2,
#     "prev_page": null
#   }
# }

# Search folders by name with pagination
curl -X GET "http://localhost:8000/api/v1/folders/?search=photos&skip=0&limit=20" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# Filter folders by storage type
curl -X GET "http://localhost:8000/api/v1/folders/?storage_type=DEEP_ARCHIVE" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# Get root folders with multiple filters
curl -X GET "http://localhost:8000/api/v1/folders/root?search=project&storage_type=GLACIER_IR&sort_by=file_count&sort_order=desc" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# FILES: Comprehensive file filtering and search with pagination
# Search files by filename with pagination metadata
curl -X GET "http://localhost:8000/api/v1/files/folder/FOLDER_ID?search=vacation&limit=10" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# Response format:
# {
#   "data": [
#     {
#       "id": "file_id",
#       "filename": "vacation_photo.jpg",
#       "file_type": "IMAGE",
#       "file_size": 2048576,
#       "s3_url": "https://presigned-url...",
#       "thumbnail_s3_url": "https://presigned-thumbnail-url...",
#       ...
#     }
#   ],
#   "meta": {
#     "total_count": 100,
#     "page_count": 10,
#     "current_page": 1,
#     "per_page": 10,
#     "has_next": true,
#     "has_prev": false,
#     "next_page": 2,
#     "prev_page": null
#   }
# }

# Filter files by type and size
curl -X GET "http://localhost:8000/api/v1/files/folder/FOLDER_ID?file_type=IMAGE&min_size=1048576&max_size=10485760" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# Sort files by size (largest first)
curl -X GET "http://localhost:8000/api/v1/files/folder/FOLDER_ID?sort_by=file_size&sort_order=desc" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# USERS: Search and paginate users with metadata
# Get users with pagination metadata
curl -X GET "http://localhost:8000/api/v1/auth/users?skip=0&limit=20" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# Response format:
# {
#   "data": [
#     {
#       "id": "user_id",
#       "email": "user@example.com",
#       "full_name": "John Doe",
#       "is_active": true,
#       ...
#     }
#   ],
#   "meta": {
#     "total_count": 500,
#     "page_count": 25,
#     "current_page": 1,
#     "per_page": 20,
#     "has_next": true,
#     "has_prev": false,
#     "next_page": 2,
#     "prev_page": null
#   }
# }

# Search users by name or email
curl -X GET "http://localhost:8000/api/v1/auth/users?search=john" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### 8. Advanced Query Examples

```bash
# Find large video files for archival
curl -X GET "http://localhost:8000/api/v1/files/folder/FOLDER_ID?file_type=VIDEO&min_size=1073741824&sort_by=file_size&sort_order=desc" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# Get recently uploaded images
curl -X GET "http://localhost:8000/api/v1/files/folder/FOLDER_ID?file_type=IMAGE&sort_by=created_at&sort_order=desc&limit=20" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# Find folders with many files for optimization
curl -X GET "http://localhost:8000/api/v1/folders/?sort_by=file_count&sort_order=desc&limit=10" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# Search for specific file types with size limits
curl -X GET "http://localhost:8000/api/v1/files/folder/FOLDER_ID?search=report&file_type=DOCUMENT&max_size=52428800" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### 8. Size Information and Folder Statistics

```bash
# Get individual folder with total size included
curl -X GET "http://localhost:8000/api/v1/folders/FOLDER_ID" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# Get detailed folder statistics with file type breakdowns
curl -X GET "http://localhost:8000/api/v1/folders/FOLDER_ID/stats" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# List folders with size calculation (may be slower for large folders)
curl -X GET "http://localhost:8000/api/v1/folders/?include_size=true&sort_by=total_size&sort_order=desc" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# Get root folders sorted by size (largest first)
curl -X GET "http://localhost:8000/api/v1/folders/root?include_size=true&sort_by=total_size&sort_order=desc&limit=10" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# Find folders taking up the most space
curl -X GET "http://localhost:8000/api/v1/folders/?include_size=true&sort_by=total_size&sort_order=desc&limit=5" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"

# Filter files by size range (between 10MB and 1GB)
curl -X GET "http://localhost:8000/api/v1/files/folder/FOLDER_ID?min_size=10485760&max_size=1073741824&sort_by=file_size&sort_order=desc" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### Size Information Features

**📏 Folder Size Calculation:**
- Individual folders: Always includes `total_size` in bytes (recursive calculation)
- Folder lists: Optional via `include_size=true` parameter  
- Statistics endpoint: Detailed breakdown with human-readable formatting
- Sorting: Sort by `total_size` in folder list endpoints

**📐 File Size Information:**
- All file responses include `file_size` in bytes
- File filtering: `min_size` and `max_size` parameters for range filtering
- Sort by file size: Use `sort_by=file_size` parameter
- Upload responses: Include file size information

**📊 Folder Statistics Example Response:**
```json
{
  "folder_id": "507f1f77bcf86cd799439011",
  "folder_name": "My Photos",
  "total_size_bytes": 2147483648,
  "total_size_formatted": "2.0 GB",
  "file_count": 150,
  "subfolder_count": 5,
  "file_types": {
    "image": {
      "count": 120,
      "total_size_bytes": 1610612736,
      "total_size_formatted": "1.5 GB",
      "average_size_bytes": 13421772,
      "average_size_formatted": "12.8 MB"
    },
    "video": {
      "count": 30,
      "total_size_bytes": 536870912,
      "total_size_formatted": "512 MB",
      "average_size_bytes": 17895697,
      "average_size_formatted": "17.1 MB"
    }
  }
}
```

## Key Features Explained

### 🚀 **TRUE 10GB File Support**
**FIXED MEMORY ISSUE**: The system now properly handles files up to 10GB without memory issues:

- **❌ Old Implementation**: Loaded entire file into memory (would crash on large files)
- **✅ New Implementation**: Streams files directly to S3 in 100MB chunks
- **Memory Usage**: Constant ~100MB regardless of file size
- **S3 Multipart Upload**: Automatic for files >100MB
- **Smart Thumbnail Handling**: Limited to 50MB images to prevent memory issues

**Technical Implementation:**
- `upload_streaming_file()`: Direct streaming from FastAPI UploadFile to S3
- `_upload_multipart_streaming()`: Chunked upload for large files
- Uvicorn configuration: 12GB request body limit with extended timeouts
- No intermediate file storage on server

### Storage Type Intelligence
- **Deep Archive**: No thumbnails generated or stored (maximum cost savings)
- **Standard IA & Glacier IR**: Full thumbnail support
- **Automatic Transitions**: Moving to Deep Archive deletes thumbnails, moving back creates them
- **Inheritance**: New files inherit parent folder's storage type
- **Bulk Operations**: Change entire folder hierarchies efficiently

### Access Control System
- **Read**: View folders and files, download files (except Deep Archive)
- **Write**: Read permissions + upload, edit, move files
- **Admin**: Write permissions + change storage types and manage folder settings
- **Owner**: Full control + share/revoke folder access (only folder creators can share)

### File Upload Intelligence
- **Storage Class Aware**: Files uploaded with folder's storage class
- **Size Validation**: Different limits for images (100MB) vs videos (10GB)
- **Smart Thumbnails**: Only generated for non-Deep Archive storage
- **Multipart Uploads**: Automatic for large files

### Cost Optimization Features
- **Storage Class Selection**: Choose appropriate cost/access balance
- **Thumbnail Management**: Automatic cleanup saves storage costs
- **Bulk Operations**: Efficient S3 API usage
- **Smart Defaults**: Glacier IR provides good balance of cost and access

## Testing Large File Uploads

### Automated Testing
Run the included test script to validate 10GB file support:

```bash
# Install testing dependencies
pip install aiohttp

# Run the test suite (creates files up to 500MB by default)
python test_large_file.py

# For extreme testing, edit the script to include:
# - 1000,   # 1GB files
# - 5000,   # 5GB files  
# - 10000,  # 10GB files (requires significant time and bandwidth)
```

### Manual Testing with curl
```bash
# Test large file upload (replace with actual large file)
curl -X POST "http://localhost:8000/api/v1/files/upload?folder_id=FOLDER_ID" \
     -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
     -F "file=@/path/to/large_video.mp4" \
     --max-time 3600  # 1 hour timeout

# Monitor upload progress
curl -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
     "http://localhost:8000/api/v1/files/FILE_ID"
```

### Production Considerations for 10GB Files

**Server Configuration:**
```bash
# Uvicorn with large file support
uvicorn main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --timeout-keep-alive 30 \
  --limit-max-requests 1000 \
  --h11-max-incomplete-event-size 12884901888  # 12GB
```

**Nginx Configuration** (if using reverse proxy):
```nginx
client_max_body_size 12G;
client_body_timeout 3600s;
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;
```

**AWS S3 Considerations:**
- Multipart uploads are automatically used for files >100MB
- Each part is 100MB, so a 10GB file = 100 parts
- S3 charges per request, but multipart is still cost-effective
- Upload can be resumed if interrupted

## Production Deployment

### Environment Variables
Update your `.env` file for production:
- Set `DEBUG=False`
- Use a strong `SECRET_KEY`

# Pagination Parameters

All paginated endpoints now use the following parameters:

- `page`: Page number (1-based, default: 1)
- `per_page`: Number of items per page (default: 20, max: 100)

## Example API Calls

### Get Folders (Page 3, 20 items per page)
```bash
curl -X GET "http://localhost:8000/folders/?page=3&per_page=20" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### Get Files in Folder (Page 2, 10 items per page)
```bash
curl -X GET "http://localhost:8000/files/folder/FOLDER_ID?page=2&per_page=10" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### Get Users (Page 1, 5 items per page)
```bash
curl -X GET "http://localhost:8000/auth/users?page=1&per_page=5" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

## Paginated Response Format

All paginated endpoints return responses in the following format:

```json
{
  "data": [
    // ... array of items
  ],
  "meta": {
    "total_count": 150,
    "page_count": 8,
    "current_page": 3,
    "per_page": 20,
    "has_next": true,
    "has_prev": true,
    "next_page": 4,
    "prev_page": 2
  }
}
```

### Pagination Metadata Fields

- `total_count`: Total number of items available
- `page_count`: Total number of pages
- `current_page`: Current page number (1-based)
- `per_page`: Number of items per page
- `has_next`: Whether there is a next page
- `has_prev`: Whether there is a previous page
- `next_page`: Next page number (null if no next page)
- `prev_page`: Previous page number (null if no previous page)

## Frontend Implementation Example

```javascript
// Example pagination handling in frontend
const handlePageChange = (newPage) => {
  fetch(`/api/folders/?page=${newPage}&per_page=20`)
    .then(response => response.json())
    .then(data => {
      // Update UI with data.data
      // Update pagination controls with data.meta
      updatePaginationControls({
        currentPage: data.meta.current_page,
        totalPages: data.meta.page_count,
        hasNext: data.meta.has_next,
        hasPrev: data.meta.has_prev
      });
    });
};
```

## Benefits of Page-Based Pagination

1. **User-Friendly**: Page numbers are more intuitive than skip/limit
2. **Consistent**: All endpoints use the same parameter names
3. **Flexible**: Easy to implement page size controls
4. **Efficient**: Skip calculation handled internally
5. **Safe**: Automatic bounds checking prevents invalid pages

### 🔄 **Smart Storage Conversion System**

**Folder Status Tracking:**
- **`active`**: Folder is accessible (Standard IA, Glacier IR)
- **`inactive`**: Folder is in Deep Archive (not immediately accessible)
- **`converting`**: Folder is being converted between storage types

**Time-Based Deep Archive Retrievals:**
The system now uses a revolutionary time-based approach for Deep Archive conversions that eliminates the need for background workers (Celery/Redis). Instead, it calculates status dynamically on each request:

**Three Conversion Phases:**
1. **Converting Phase** (start → ready): Status shows "converting", files not yet accessible
2. **Active Phase** (ready → expires): Status shows "active", files accessible in target storage
3. **Expired Phase** (after expiration): Automatic cleanup, return to Deep Archive

**Key Features:**
- **Real-time Status**: Calculated on each API request based on timestamps
- **No Background Workers**: No Celery, Redis, or job queues needed
- **Automatic Cleanup**: Expired retrievals automatically return to Deep Archive
- **Time-Limited Retrievals**: 1-365 days or permanent retrieval options
- **Cost Optimization**: Bulk mode provides significant savings

**Deep Archive Features:**
- **Bulk Mode**: 48-hour conversion with lower costs
- **Standard Mode**: 12-hour conversion with higher costs  
- **Time-Limited Retrievals**: 1-365 days or permanent
- **Auto-Return**: Permanent retrievals automatically move to Standard after completion
- **Forced Children**: Deep Archive conversions always include all subfolders and files

**Conversion Job Tracking:**
- Unique job IDs for each conversion
- Real-time status monitoring via timestamps
- Estimated completion times
- Cost savings calculations
- Dynamic status updates without background processes

**Time-Based Calculation Logic:**
```
Current Time vs Retrieval Timeline:
├── Before Ready Time → Status: converting, Storage: DEEP_ARCHIVE
├── Ready to Expiry → Status: active, Storage: target_storage  
└── After Expiry → Status: inactive, Storage: DEEP_ARCHIVE (auto-cleanup)
```

## Usage Examples