import ffmpeg
import cv2
import numpy as np
from PIL import Image
import io
import os
import tempfile
from typing import List, Tuple, Optional
import asyncio
from app.services.gemini import gemini_image_vision, gemini_text
from app.services.transcription import transcribe_and_summarize, summarize_with_transcription_context
from app.config import settings
import logging

logger = logging.getLogger(__name__)

class VideoProcessor:
    def __init__(self):
        self.frames_per_second = 1  # Extract 1 frame per second
        self.frames_per_composite = 10  # Combine 10 frames into one composite image
        self.composite_width = 1920  # Width of composite image
        self.composite_height = 1080  # Height of composite image
    
    async def process_video(self, video_path: str) -> str:
        """
        Process a video file and return a comprehensive description with transcription.
        
        Args:
            video_path: Path to the video file
            
        Returns:
            str: Final summary description of the video including transcription context
        """
        """
        Process a video file and return a comprehensive description with transcription.
        
        Args:
            video_path: Path to the video file
            
        Returns:
            str: Final summary description of the video including transcription context
        """
        try:
            logger.info(f"Starting video processing for: {video_path}")
            
            # Check if video file exists
            if not os.path.exists(video_path):
                logger.error(f"Video file does not exist: {video_path}")
                return "Error: Video file not found"
            
            # Check file size
            file_size = os.path.getsize(video_path)
            logger.info(f"Video file size: {file_size} bytes")
            
            # Step 1: Extract frames from video
            logger.info("Step 1: Extracting frames...")
            frames = await self._extract_frames(video_path)
            
            if not frames:
                logger.error("No frames extracted, returning error message")
                return "No frames could be extracted from the video."
            
            logger.info(f"Successfully extracted {len(frames)} frames")
            
            # Step 2: Create composite images (10 frames per composite)
            logger.info("Step 2: Creating composite images...")
            composite_images = await self._create_composite_images(frames)
            
            if not composite_images:
                logger.error("No composite images created")
                return "Error: Could not create composite images from video frames."
            
            logger.info(f"Successfully created {len(composite_images)} composite images")
            
            # Step 3: Generate descriptions for each composite
            logger.info("Step 3: Generating descriptions...")
            descriptions = []
            for i, composite_img in enumerate(composite_images):
                logger.info(f"Processing composite {i+1}/{len(composite_images)}")
                description = await self._get_composite_description(composite_img, i)
                descriptions.append(description)
            
            logger.info(f"Generated {len(descriptions)} descriptions")
            
            # Step 4: Transcribe video audio
            logger.info("Step 4: Transcribing video audio...")
            transcription_result = None
            try:
                transcription_result = await transcribe_and_summarize(video_path)
                if transcription_result["success"]:
                    logger.info("Transcription completed successfully")
                else:
                    logger.warning(f"Transcription failed: {transcription_result.get('error', 'Unknown error')}")
            except Exception as e:
                logger.warning(f"Transcription error: {str(e)}")
            
            # Step 5: Generate final summary with transcription context
            logger.info("Step 5: Generating final summary with transcription context...")
            if transcription_result and transcription_result["success"]:
                final_summary = await summarize_with_transcription_context(
                    descriptions, 
                    transcription_result["transcription"]
                )
            else:
                final_summary = await self._generate_final_summary(descriptions)
            
            logger.info(f"Final summary generated: {final_summary[:100]}...")
            
            return final_summary
            
        except Exception as e:
            logger.error(f"Error processing video {video_path}: {str(e)}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return f"Video analysis completed with some errors: {str(e)}"
    
    async def _extract_frames(self, video_path: str) -> List[np.ndarray]:
        """
        Extract frames from video at 1 frame per second.
        
        Args:
            video_path: Path to the video file
            
        Returns:
            List of frame arrays
        """
        try:
            logger.info(f"Extracting frames from video: {video_path}")
            
            # Get video information
            probe = ffmpeg.probe(video_path)
            
            # Check if video stream exists
            video_streams = [s for s in probe['streams'] if s['codec_type'] == 'video']
            if not video_streams:
                logger.error("No video stream found in the file")
                return []
                
            video_info = video_streams[0]
            duration = float(probe['format']['duration'])
            fps = eval(video_info['r_frame_rate'])
            
            logger.info(f"Video info - Duration: {duration}s, FPS: {fps}, Resolution: {video_info['width']}x{video_info['height']}")
            
            # Check if duration is valid
            if duration <= 0:
                logger.error("Invalid video duration")
                return []
            
            frames = []
            frame_interval = 1.0  # 1 second intervals
            
            # For very short videos, extract at least one frame
            if duration < 1:
                logger.info("Video is very short, extracting single frame")
                frame_interval = 0
            
            for timestamp in range(0, int(duration), int(frame_interval)):
                try:
                    logger.info(f"Extracting frame at {timestamp}s...")
                    # Extract frame at specific timestamp
                    out, _ = (
                        ffmpeg
                        .input(video_path, ss=timestamp)
                        .output('pipe:', format='rawvideo', pix_fmt='rgb24', vframes=1)
                        .run(capture_stdout=True, quiet=True)
                    )
                    
                    if not out:
                        logger.warning(f"No frame data extracted at {timestamp}s")
                        continue
                    
                    # Convert to numpy array
                    frame = np.frombuffer(out, np.uint8)
                    
                    # Reshape based on video dimensions
                    height = int(video_info['height'])
                    width = int(video_info['width'])
                    
                    if len(frame) != height * width * 3:
                        logger.warning(f"Frame size mismatch at {timestamp}s: expected {height * width * 3}, got {len(frame)}")
                        continue
                    
                    frame = frame.reshape([height, width, 3])
                    
                    # Resize frame to standard size
                    frame_img = Image.fromarray(frame)
                    frame_img = frame_img.resize((640, 480), Image.Resampling.LANCZOS)
                    frame_array = np.array(frame_img)
                    
                    frames.append(frame_array)
                    logger.info(f"Successfully extracted frame at {timestamp}s")
                    
                except Exception as e:
                    logger.warning(f"Failed to extract frame at {timestamp}s: {str(e)}")
                    continue
            
            logger.info(f"Extracted {len(frames)} frames from video")
            
            if len(frames) == 0:
                logger.error("No frames were successfully extracted from the video")
                return []
                
            return frames
            
        except Exception as e:
            logger.error(f"Error extracting frames: {str(e)}")
            import traceback
            logger.error(f"Frame extraction traceback: {traceback.format_exc()}")
            return []
    
    async def _create_composite_images(self, frames: List[np.ndarray]) -> List[bytes]:
        """
        Create composite images by combining 10 frames side by side.
        
        Args:
            frames: List of frame arrays
            
        Returns:
            List of composite image bytes
        """
        composite_images = []
        
        for i in range(0, len(frames), self.frames_per_composite):
            batch = frames[i:i + self.frames_per_composite]
            
            if len(batch) < self.frames_per_composite:
                # Pad with black frames if needed
                while len(batch) < self.frames_per_composite:
                    black_frame = np.zeros((480, 640, 3), dtype=np.uint8)
                    batch.append(black_frame)
            
            # Create composite image
            composite = await self._combine_frames_side_by_side(batch)
            composite_images.append(composite)
        
        logger.info(f"Created {len(composite_images)} composite images")
        return composite_images
    
    async def _combine_frames_side_by_side(self, frames: List[np.ndarray]) -> bytes:
        """
        Combine frames side by side into a single image.
        
        Args:
            frames: List of frame arrays
            
        Returns:
            Composite image as bytes
        """
        # Resize all frames to fit in composite
        frame_width = self.composite_width // len(frames)
        frame_height = self.composite_height
        
        resized_frames = []
        for frame in frames:
            frame_img = Image.fromarray(frame)
            frame_img = frame_img.resize((frame_width, frame_height), Image.Resampling.LANCZOS)
            resized_frames.append(np.array(frame_img))
        
        # Combine frames horizontally
        composite_array = np.hstack(resized_frames)
        
        # Convert to PIL Image and then to bytes
        composite_img = Image.fromarray(composite_array)
        img_bytes = io.BytesIO()
        composite_img.save(img_bytes, format='JPEG', quality=85)
        
        return img_bytes.getvalue()
    
    async def _get_composite_description(self, composite_img_bytes: bytes, segment_index: int) -> str:
        """
        Get description for a composite image using Gemini Vision.
        
        Args:
            composite_img_bytes: Composite image as bytes
            segment_index: Index of the time segment
            
        Returns:
            Description of the composite image
        """
        try:
            start_time = segment_index * self.frames_per_composite
            end_time = (segment_index + 1) * self.frames_per_composite
            
            prompt = f"""Analyze this composite image showing {self.frames_per_composite} frames from a video (seconds {start_time}-{end_time}).
Each frame represents 1 second of video time, arranged from left to right.

Describe what you see in this video segment, including:
- What is happening in the video
- Any people, objects, or scenes visible
- Changes or movements across the frames
- Colors, lighting, and visual elements
- Any text, logos, or brands visible
- Activities or actions being performed

Focus on the overall narrative and key visual elements."""
            
            response = await gemini_image_vision(
                model=settings.GEMINI_VISION_MODEL,
                prompt=prompt,
                img_bytes=composite_img_bytes
            )
            
            # Extract description from response
            if isinstance(response, dict):
                # Try different possible keys for the description
                description = response.get("description") or response.get("text") or response.get("content")
                if description:
                    return description
                else:
                    # If no description key found, return the whole response as string
                    return str(response)
            else:
                return str(response)
            
        except Exception as e:
            logger.error(f"Error getting composite description: {str(e)}")
            return f"Error analyzing video segment {segment_index}: {str(e)}"
    
    async def _generate_final_summary(self, descriptions: List[str]) -> str:
        """
        Generate a final summary of all video segments.
        
        Args:
            descriptions: List of descriptions for each video segment
            
        Returns:
            Final summary description
        """
        try:
            if not descriptions:
                return "No video content could be analyzed."
            
            # Limit descriptions to avoid token limits
            max_descriptions = 5
            if len(descriptions) > max_descriptions:
                descriptions = descriptions[:max_descriptions]
            
            combined_descriptions = "\n\n".join([
                f"Segment {i+1}: {desc}" for i, desc in enumerate(descriptions)
            ])
            
            prompt = f"""Analyze this video content and provide a concise summary in about 50 words.

Video segments:
{combined_descriptions}

Provide a clear, descriptive summary of what this video shows, including key subjects, activities, and visual elements."""
            
            response = await gemini_text(prompt=prompt, model=settings.GEMINI_VISION_MODEL)
            
            # Extract summary from response
            if isinstance(response, dict):
                # Try different possible keys for the summary
                summary = response.get("summary") or response.get("text") or response.get("content")
                if summary:
                    return summary
                else:
                    # If no summary key found, return the whole response as string
                    return str(response)
            else:
                return str(response)
            
        except Exception as e:
            logger.error(f"Error generating final summary: {str(e)}")
            # Fallback: return a simple concatenation of descriptions
            try:
                return "Video showing: " + ". ".join(descriptions[:3]) + "."
            except:
                return "Video content analysis completed."

# Global instance
video_processor = VideoProcessor()
