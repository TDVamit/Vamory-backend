from PIL import Image, ImageOps
import io
import json
import re
from typing import Optional, BinaryIO
from app.config import settings
import ffmpeg
import tempfile
import os


def sanitize_for_json(obj):
    """Recursively sanitize an object to ensure it's JSON-safe"""
    if isinstance(obj, dict):
        return {key: sanitize_for_json(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    elif isinstance(obj, str):
        # Remove control characters except newline, tab, and carriage return
        return re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', obj)
    else:
        return obj


class ThumbnailService:
    def __init__(self):
        self.thumbnail_size = (settings.thumbnail_size, settings.thumbnail_size)
        self.thumbnail_quality = settings.thumbnail_quality

    def is_image(self, content_type: str) -> bool:
        """Check if the content type is an image"""
        return content_type.startswith('image/') and content_type != 'image/svg+xml'

    def can_generate_thumbnail(self, content_type: str) -> bool:
        """Check if we can generate a thumbnail for this content type"""
        supported_types = [
            'image/jpeg', 'image/jpg', 'image/png', 'image/gif',
            'image/bmp', 'image/tiff', 'image/webp'
        ]
        return content_type.lower() in supported_types

    def generate_thumbnail(self, image_content: BinaryIO, output_format: str = 'WEBP') -> Optional[io.BytesIO]:
        """Generate a thumbnail from image content - always outputs WebP for optimal compression"""
        try:
            # Reset file pointer
            image_content.seek(0)
            
            # Open the image
            with Image.open(image_content) as image:
                # Convert to RGB for WebP compatibility (WebP works best with RGB)
                if image.mode in ('RGBA', 'LA'):
                    # For images with transparency, keep RGBA for WebP
                    if image.mode == 'LA':
                        image = image.convert('RGBA')
                elif image.mode in ('P', 'L', '1'):
                    # Convert palette and grayscale images to RGB
                    image = image.convert('RGB')
                elif image.mode not in ('RGB', 'RGBA'):
                    # Convert any other modes to RGB
                    image = image.convert('RGB')

                # Auto-orient the image based on EXIF data
                image = ImageOps.exif_transpose(image)
                
                # Create thumbnail while maintaining aspect ratio
                image.thumbnail(self.thumbnail_size, Image.Resampling.LANCZOS)
                
                # Create output buffer
                output_buffer = io.BytesIO()
                
                # Always save as WebP with optimized settings
                image.save(
                    output_buffer,
                    format='WEBP',
                    quality=self.thumbnail_quality,
                optimize=True,
                method=6,  # Best compression method (0-6, 6 is slowest but best compression)
                lossless=False  # Use lossy compression for smaller files
                )
                
                output_buffer.seek(0)
                return output_buffer
                
        except Exception as e:
            print(f"Failed to generate thumbnail: {str(e)}")
            return None

    def can_generate_video_thumbnail(self, content_type: str) -> bool:
        """Check if we can generate a thumbnail for this video content type"""
        supported_types = [
            'video/mp4', 'video/quicktime', 'video/x-matroska', 'video/webm', 'video/avi', 'video/mpeg', 'video/ogg'
        ]
        return content_type.lower() in supported_types

    def generate_video_thumbnail(self, video_content: BinaryIO, output_format: str = 'WEBP', time_offset: float = 1.0) -> Optional[io.BytesIO]:
        """Extract a frame from a video and generate a WebP thumbnail"""
        try:
            video_content.seek(0)
            input_bytes = video_content.read()
            output_buffer = io.BytesIO()
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as temp_video:
                temp_video.write(input_bytes)
                temp_video_path = temp_video.name
            try:
                # Use ffmpeg to extract a frame at the given time offset
                probe = ffmpeg.probe(temp_video_path, v='error', select_streams='v:0', show_entries='stream=width,height')
                width = probe['streams'][0]['width']
                height = probe['streams'][0]['height']
                # Seek to the frame at time_offset seconds
                process = (
                    ffmpeg
                    .input(temp_video_path, ss=time_offset)
                    .filter('scale', self.thumbnail_size[0], -1)
                    .output('pipe:1', vframes=1, format='image2', vcodec='webp')
                    .run_async(pipe_stdout=True, pipe_stderr=True)
                )
                out, err = process.communicate()
                if process.returncode != 0:
                    print(f"Failed to extract video thumbnail: {err.decode()}")
                    return None
                output_buffer.write(out)
                output_buffer.seek(0)
                return output_buffer
            finally:
                os.unlink(temp_video_path)
        except Exception as e:
            print(f"Failed to generate video thumbnail: {str(e)}")
            return None

    def get_image_metadata(self, image_content: BinaryIO) -> dict:
        """Extract metadata from image"""
        try:
            # Reset file pointer
            image_content.seek(0)
            
            with Image.open(image_content) as image:
                metadata = {
                    'width': image.width,
                    'height': image.height,
                    'format': image.format,
                    'color_mode': image.mode,
                    'has_transparency': image.mode in ('RGBA', 'LA') or 'transparency' in image.info
                }
                
                # Get additional info from EXIF if available
                if hasattr(image, '_getexif') and image._getexif() is not None:
                    exif = image._getexif()
                    if exif:
                        # Add orientation info
                        orientation = exif.get(274)  # Orientation tag
                        if orientation:
                            metadata['orientation'] = orientation
                
                # Sanitize metadata to ensure it's JSON-safe
                return sanitize_for_json(metadata)
                
        except Exception as e:
            print(f"Failed to extract image metadata: {str(e)}")
            return {}

    def resize_image(self, image_content: BinaryIO, max_width: int, max_height: int, output_format: str = 'WEBP') -> Optional[io.BytesIO]:
        """Resize image to fit within max dimensions - always outputs WebP for optimal compression"""
        try:
            # Reset file pointer
            image_content.seek(0)
            
            with Image.open(image_content) as image:
                # Convert to RGB for WebP compatibility (WebP works best with RGB)
                if image.mode in ('RGBA', 'LA'):
                    # For images with transparency, keep RGBA for WebP
                    if image.mode == 'LA':
                        image = image.convert('RGBA')
                elif image.mode in ('P', 'L', '1'):
                    # Convert palette and grayscale images to RGB
                    image = image.convert('RGB')
                elif image.mode not in ('RGB', 'RGBA'):
                    # Convert any other modes to RGB
                    image = image.convert('RGB')
                
                # Auto-orient the image based on EXIF data
                image = ImageOps.exif_transpose(image)
                
                # Calculate new size while maintaining aspect ratio
                image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
                
                # Create output buffer
                output_buffer = io.BytesIO()
                
                # Always save as WebP with optimized settings
                image.save(
                    output_buffer,
                    format='WEBP',
                    quality=self.thumbnail_quality,
                optimize=True,
                method=6,  # Best compression method (0-6, 6 is slowest but best compression)
                lossless=False  # Use lossy compression for smaller files
                )
                
                output_buffer.seek(0)
                return output_buffer
                
        except Exception as e:
            print(f"Failed to resize image: {str(e)}")
            return None


# Create a global instance
thumbnail_service = ThumbnailService() 