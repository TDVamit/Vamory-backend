import boto3
from botocore.exceptions import ClientError
from typing import Optional, BinaryIO, List
import uuid
import os
from fastapi import UploadFile
from app.config import settings
from app.models.file import StorageType


class S3Service:
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region
        )
        self.bucket_name = settings.s3_bucket_name

    def storage_type_to_s3_class(self, storage_type: StorageType) -> str:
        """Convert our storage type enum to S3 storage class"""
        mapping = {
            StorageType.STANDARD: "STANDARD",
            StorageType.STANDARD_IA: "STANDARD_IA",
            StorageType.GLACIER_IR: "GLACIER_IR", 
            StorageType.DEEP_ARCHIVE: "DEEP_ARCHIVE"
        }
        return mapping.get(storage_type, "GLACIER_IR")

    def generate_s3_key(self, user_id: str, folder_id: str, filename: str, file_type: str = "file") -> str:
        """Generate a unique S3 key for a file"""
        unique_id = str(uuid.uuid4())
        
        if file_type == "thumbnail":
            # Thumbnails are always saved as WebP format
            return f"thumbnails/{user_id}/{folder_id}/{unique_id}.webp"
        else:
            # Regular files keep their original extension
            file_extension = os.path.splitext(filename)[1]
            return f"files/{user_id}/{folder_id}/{unique_id}{file_extension}"

    async def upload_file(self, file_content: BinaryIO, s3_key: str, content_type: str, storage_type: StorageType = StorageType.GLACIER_IR) -> str:
        """Upload file to S3 with specified storage class and return the URL"""
        try:
            s3_storage_class = self.storage_type_to_s3_class(storage_type)
            
            # Upload file to S3 in thread pool
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.upload_fileobj(
                    file_content,
                    self.bucket_name,
                    s3_key,
                    ExtraArgs={
                        'ContentType': content_type,
                        'ACL': 'private',
                        'StorageClass': s3_storage_class
                    }
                )
            )
            
            # Generate the S3 URL
            s3_url = f"https://{self.bucket_name}.s3.{settings.aws_region}.amazonaws.com/{s3_key}"
            return s3_url
            
        except ClientError as e:
            raise Exception(f"Failed to upload file to S3: {str(e)}")

    async def upload_streaming_file(self, upload_file: UploadFile, s3_key: str, storage_type: StorageType = StorageType.GLACIER_IR) -> str:
        """Upload file directly from FastAPI UploadFile without loading into memory"""
        try:
            s3_storage_class = self.storage_type_to_s3_class(storage_type)
            
            # For large files, determine if we should use multipart upload
            file_size = upload_file.size
            
            if file_size and file_size > 100 * 1024 * 1024:  # 100MB
                return await self._upload_multipart_streaming(upload_file, s3_key, storage_type)
            else:
                # For smaller files, use regular upload
                # Reset file position to beginning
                await upload_file.seek(0)
                
                # Run S3 upload in thread pool to avoid blocking
                import asyncio
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: self.s3_client.upload_fileobj(
                        upload_file.file,
                        self.bucket_name,
                        s3_key,
                        ExtraArgs={
                            'ContentType': upload_file.content_type,
                            'ACL': 'private',
                            'StorageClass': s3_storage_class
                        }
                    )
                )
            
            # Generate the S3 URL
            s3_url = f"https://{self.bucket_name}.s3.{settings.aws_region}.amazonaws.com/{s3_key}"
            return s3_url
            
        except ClientError as e:
            raise Exception(f"Failed to upload streaming file to S3: {str(e)}")

    async def _upload_multipart_streaming(self, upload_file: UploadFile, s3_key: str, storage_type: StorageType) -> str:
        """Handle multipart upload for large files by streaming chunks"""
        try:
            s3_storage_class = self.storage_type_to_s3_class(storage_type)
            
            # Create multipart upload
            import asyncio
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.s3_client.create_multipart_upload(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    ContentType=upload_file.content_type,
                    ACL='private',
                    StorageClass=s3_storage_class
                )
            )
            
            upload_id = response['UploadId']
            parts = []
            part_number = 1
            
            try:
                # Reset file position to beginning
                await upload_file.seek(0)
                
                # Upload parts by streaming chunks
                while True:
                    # Read 100MB chunks directly from the upload file
                    chunk = await upload_file.read(100 * 1024 * 1024)  # 100MB
                    if not chunk:
                        break
                    
                    part_response = await loop.run_in_executor(
                        None,
                        lambda: self.s3_client.upload_part(
                            Bucket=self.bucket_name,
                            Key=s3_key,
                            PartNumber=part_number,
                            UploadId=upload_id,
                            Body=chunk
                        )
                    )
                    
                    parts.append({
                        'ETag': part_response['ETag'],
                        'PartNumber': part_number
                    })
                    
                    part_number += 1
                
                # Complete multipart upload
                await loop.run_in_executor(
                    None,
                    lambda: self.s3_client.complete_multipart_upload(
                        Bucket=self.bucket_name,
                        Key=s3_key,
                        UploadId=upload_id,
                        MultipartUpload={'Parts': parts}
                    )
                )
                
            except Exception as e:
                # Abort multipart upload on error
                await loop.run_in_executor(
                    None,
                    lambda: self.s3_client.abort_multipart_upload(
                        Bucket=self.bucket_name,
                        Key=s3_key,
                        UploadId=upload_id
                    )
                )
                raise e
            
            # Generate the S3 URL
            s3_url = f"https://{self.bucket_name}.s3.{settings.aws_region}.amazonaws.com/{s3_key}"
            return s3_url
            
        except ClientError as e:
            raise Exception(f"Failed to upload multipart streaming file to S3: {str(e)}")

    async def upload_large_file(self, file_content: BinaryIO, s3_key: str, content_type: str, file_size: int, storage_type: StorageType = StorageType.GLACIER_IR) -> str:
        """Upload large file using multipart upload with specified storage class"""
        try:
            s3_storage_class = self.storage_type_to_s3_class(storage_type)
            
            # For files larger than 100MB, use multipart upload
            if file_size > 100 * 1024 * 1024:  # 100MB
                # Create multipart upload
                import asyncio
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self.s3_client.create_multipart_upload(
                        Bucket=self.bucket_name,
                        Key=s3_key,
                        ContentType=content_type,
                        ACL='private',
                        StorageClass=s3_storage_class
                    )
                )
                
                upload_id = response['UploadId']
                parts = []
                part_number = 1
                
                try:
                    # Upload parts
                    while True:
                        # Read 100MB chunks
                        chunk = file_content.read(100 * 1024 * 1024)
                        if not chunk:
                            break
                        
                        part_response = await loop.run_in_executor(
                            None,
                            lambda: self.s3_client.upload_part(
                                Bucket=self.bucket_name,
                                Key=s3_key,
                                PartNumber=part_number,
                                UploadId=upload_id,
                                Body=chunk
                            )
                        )
                        
                        parts.append({
                            'ETag': part_response['ETag'],
                            'PartNumber': part_number
                        })
                        
                        part_number += 1
                    
                    # Complete multipart upload
                    await loop.run_in_executor(
                        None,
                        lambda: self.s3_client.complete_multipart_upload(
                            Bucket=self.bucket_name,
                            Key=s3_key,
                            UploadId=upload_id,
                            MultipartUpload={'Parts': parts}
                        )
                    )
                
                except Exception as e:
                    # Abort multipart upload on error
                    await loop.run_in_executor(
                        None,
                        lambda: self.s3_client.abort_multipart_upload(
                            Bucket=self.bucket_name,
                            Key=s3_key,
                            UploadId=upload_id
                        )
                    )
                    raise e
                
                # Generate the S3 URL
                s3_url = f"https://{self.bucket_name}.s3.{settings.aws_region}.amazonaws.com/{s3_key}"
                return s3_url
            else:
                # For smaller files, use regular upload
                return await self.upload_file(file_content, s3_key, content_type, storage_type)
        
        except ClientError as e:
            raise Exception(f"Failed to upload large file to S3: {str(e)}")

    async def delete_file(self, s3_key: str) -> bool:
        """Delete file from S3"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.delete_object(Bucket=self.bucket_name, Key=s3_key)
            )
            return True
        except ClientError as e:
            print(f"Failed to delete file from S3: {str(e)}")
            return False

    async def delete_files_batch(self, s3_keys: List[str]) -> dict:
        """Delete multiple files from S3 in batch"""
        try:
            if not s3_keys:
                return {"deleted": 0, "errors": []}
            
            # S3 batch delete supports up to 1000 objects per request
            batch_size = 1000
            total_deleted = 0
            errors = []
            
            for i in range(0, len(s3_keys), batch_size):
                batch = s3_keys[i:i + batch_size]
                delete_objects = [{'Key': key} for key in batch]
                
                try:
                    import asyncio
                    loop = asyncio.get_event_loop()
                    response = await loop.run_in_executor(
                        None,
                        lambda: self.s3_client.delete_objects(
                            Bucket=self.bucket_name,
                            Delete={'Objects': delete_objects}
                        )
                    )
                    
                    total_deleted += len(response.get('Deleted', []))
                    errors.extend(response.get('Errors', []))
                    
                except ClientError as e:
                    errors.append({'Key': 'batch_error', 'Message': str(e)})
            
            return {"deleted": total_deleted, "errors": errors}
            
        except Exception as e:
            return {"deleted": 0, "errors": [{'Key': 'general_error', 'Message': str(e)}]}

    async def change_storage_class(self, s3_key: str, target_storage_type: StorageType) -> bool:
        """Change storage class of an existing S3 object"""
        try:
            s3_storage_class = self.storage_type_to_s3_class(target_storage_type)
            
            # Copy the object to itself with new storage class
            import asyncio
            loop = asyncio.get_event_loop()
            copy_source = {'Bucket': self.bucket_name, 'Key': s3_key}
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.copy_object(
                    CopySource=copy_source,
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    StorageClass=s3_storage_class,
                    MetadataDirective='COPY'
                )
            )
            return True
        except ClientError as e:
            print(f"Failed to change storage class: {str(e)}")
            return False

    async def change_storage_class_batch(self, s3_keys: List[str], target_storage_type: StorageType) -> dict:
        """Change storage class for multiple files"""
        success_count = 0
        errors = []
        
        for s3_key in s3_keys:
            try:
                success = await self.change_storage_class(s3_key, target_storage_type)
                if success:
                    success_count += 1
                else:
                    errors.append({'Key': s3_key, 'Message': 'Failed to change storage class'})
            except Exception as e:
                errors.append({'Key': s3_key, 'Message': str(e)})
        
        return {"processed": success_count, "errors": errors}

    async def move_to_deep_archive(self, s3_key: str) -> bool:
        """Move file to Deep Archive storage class"""
        return await self.change_storage_class(s3_key, StorageType.DEEP_ARCHIVE)

    async def move_files_to_deep_archive_batch(self, s3_keys: List[str]) -> dict:
        """Move multiple files to Deep Archive storage class"""
        return await self.change_storage_class_batch(s3_keys, StorageType.DEEP_ARCHIVE)

    async def generate_presigned_url(
        self, s3_key: str, expiration: int = 3600, method: str = 'get_object', content_type: str = None, storage_type: StorageType = StorageType.GLACIER_IR
    ) -> Optional[str]:
        """Generate a presigned URL for file access or upload"""
        params = {'Bucket': self.bucket_name, 'Key': s3_key}
        if method == 'put_object':
            params['ACL'] = 'private'
            params['StorageClass'] = self.storage_type_to_s3_class(storage_type)
            if content_type:
                params['ContentType'] = content_type
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.s3_client.generate_presigned_url(
                    method,
                    Params=params,
                    ExpiresIn=expiration
                )
            )
            return response
        except ClientError as e:
            print(f"Failed to generate presigned URL: {str(e)}")
            return None

    async def generate_download_presigned_url(self, s3_key: str, filename: str, expiration: int = 3600) -> Optional[str]:
        """Generate a presigned URL for downloading a file with forced attachment disposition"""
        params = {
            'Bucket': self.bucket_name,
            'Key': s3_key,
            'ResponseContentDisposition': f'attachment; filename="{filename}"'
        }
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.s3_client.generate_presigned_url(
                    'get_object',
                    Params=params,
                    ExpiresIn=expiration
                )
            )
            return response
        except ClientError as e:
            print(f"Failed to generate download presigned URL: {str(e)}")
            return None

    async def copy_file(self, source_key: str, destination_key: str) -> bool:
        """Copy file within S3"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            copy_source = {'Bucket': self.bucket_name, 'Key': source_key}
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.copy_object(
                    CopySource=copy_source,
                    Bucket=self.bucket_name,
                    Key=destination_key
                )
            )
            return True
        except ClientError as e:
            print(f"Failed to copy file in S3: {str(e)}")
            return False

    async def get_file_info(self, s3_key: str) -> Optional[dict]:
        """Get file information from S3"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
            )
            return {
                'size': response['ContentLength'],
                'last_modified': response['LastModified'],
                'content_type': response['ContentType'],
                'storage_class': response.get('StorageClass', 'STANDARD')
            }
        except ClientError as e:
            print(f"Failed to get file info from S3: {str(e)}")
            return None

    async def restore_from_deep_archive(self, s3_key: str, days: int = 1) -> bool:
        """Initiate restore from Deep Archive (can take 12+ hours)"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.restore_object(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    RestoreRequest={
                        'Days': days,
                        'GlacierJobParameters': {
                            'Tier': 'Standard'  # Standard, Expedited, or Bulk
                        }
                    }
                )
            )
            return True
        except ClientError as e:
            print(f"Failed to initiate restore from Deep Archive: {str(e)}")
            return False


# Create a global instance
s3_service = S3Service() 