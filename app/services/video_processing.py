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
        self.target_frames = 60  # Target number of frames to extract
        self.frame_width = 640  # Standard frame width
        self.frame_height = 480  # Standard frame height
    
    async def process_video(self, video_path: str) -> str:

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
            
            # Step 2: Generate descriptions for each frame (parallel processing)
            logger.info("Step 2: Generating descriptions for individual frames...")
            descriptions = await self._process_frames_parallel(frames)
            
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
        Extract frames from video with equal intervals, targeting 60 frames total.
        If video is shorter, extract however many frames are available.
        
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
            total_frames = int(duration * fps)
            
            logger.info(f"Video info - Duration: {duration}s, FPS: {fps}, Total frames: {total_frames}, Resolution: {video_info['width']}x{video_info['height']}")
            
            # Check if duration is valid
            if duration <= 0:
                logger.error("Invalid video duration")
                return []
            
            # Calculate how many frames to extract and at what intervals
            frames_to_extract = min(self.target_frames, total_frames)
            
            logger.info(f"Will extract {frames_to_extract} frames from video")
            
            # Use parallel subprocess approach for frame extraction
            interval = duration / frames_to_extract
            
            try:
                logger.info(f"Extracting {frames_to_extract} frames using parallel subprocess approach")
                
                # Create temporary directory for frames
                with tempfile.TemporaryDirectory() as temp_dir:
                    # Extract frames in parallel using subprocess
                    frames = await self._extract_frames_parallel_subprocess(
                        video_path, temp_dir, frames_to_extract, interval
                    )
                    
                    logger.info(f"Successfully extracted {len(frames)} frames using parallel subprocess")
                
            except Exception as e:
                logger.error(f"Error in parallel subprocess frame extraction: {str(e)}")
                # Fallback to single frame extraction if batch fails
                return await self._extract_frames_fallback(video_path, video_info, duration, frames_to_extract)
            
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
    
    async def _extract_frames_fallback(self, video_path: str, video_info: dict, duration: float, frames_to_extract: int) -> List[np.ndarray]:
        """
        Fallback method to extract frames one by one if batch extraction fails.
        
        Args:
            video_path: Path to the video file
            video_info: Video information from ffprobe
            duration: Video duration in seconds
            frames_to_extract: Number of frames to extract
            
        Returns:
            List of frame arrays
        """
        logger.info("Using fallback method for frame extraction")
        
        # Calculate timestamps for equally spaced frames
        if frames_to_extract <= 1:
            timestamps = [duration / 2]  # Middle of the video
        else:
            interval = duration / frames_to_extract
            timestamps = [i * interval for i in range(frames_to_extract)]
        
        frames = []
        
        for i, timestamp in enumerate(timestamps):
            try:
                logger.info(f"Extracting frame {i+1}/{len(timestamps)} at {timestamp:.2f}s...")
                # Extract frame at specific timestamp
                out, _ = (
                    ffmpeg
                    .input(video_path, ss=timestamp)
                    .output('pipe:', format='rawvideo', pix_fmt='rgb24', vframes=1)
                    .run(capture_stdout=True, quiet=True)
                )
                
                if not out:
                    logger.warning(f"No frame data extracted at {timestamp:.2f}s")
                    continue
                
                # Convert to numpy array
                frame = np.frombuffer(out, np.uint8)
                
                # Reshape based on video dimensions
                height = int(video_info['height'])
                width = int(video_info['width'])
                
                if len(frame) != height * width * 3:
                    logger.warning(f"Frame size mismatch at {timestamp:.2f}s: expected {height * width * 3}, got {len(frame)}")
                    continue
                
                frame = frame.reshape([height, width, 3])
                
                # Resize frame to standard size
                frame_img = Image.fromarray(frame)
                frame_img = frame_img.resize((self.frame_width, self.frame_height), Image.Resampling.LANCZOS)
                frame_array = np.array(frame_img)
                
                frames.append(frame_array)
                logger.info(f"Successfully extracted frame {i+1} at {timestamp:.2f}s")
                
            except Exception as e:
                logger.warning(f"Failed to extract frame at {timestamp:.2f}s: {str(e)}")
                continue
        
        return frames
    
    async def _extract_frames_parallel_subprocess(self, video_path: str, temp_dir: str, frames_count: int, interval: float) -> List[np.ndarray]:
        """
        Extract frames using parallel subprocess calls for maximum speed.
        
        Args:
            video_path: Path to the video file
            temp_dir: Temporary directory for frame files
            frames_count: Number of frames to extract
            interval: Time interval between frames
            
        Returns:
            List of frame arrays
        """
        import subprocess
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        
        async def extract_single_frame(frame_index: int) -> tuple:
            """Extract a single frame using subprocess."""
            ts = frame_index * interval
            out_name = os.path.join(temp_dir, f"frame_{frame_index:03d}.jpg")
            
            def run_ffmpeg():
                try:
                    result = subprocess.run([
                        "ffmpeg", "-ss", str(ts), "-i", video_path,
                        "-frames:v", "1", "-q:v", "2", 
                        "-s", f"{self.frame_width}x{self.frame_height}",  # Scale to target size
                        out_name, "-y"  # overwrite
                    ], capture_output=True, text=True, timeout=30)
                    
                    if result.returncode == 0 and os.path.exists(out_name):
                        return frame_index, out_name, None
                    else:
                        return frame_index, None, f"FFmpeg failed: {result.stderr}"
                        
                except subprocess.TimeoutExpired:
                    return frame_index, None, "FFmpeg timeout"
                except Exception as e:
                    return frame_index, None, f"Exception: {str(e)}"
            
            # Run subprocess in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(max_workers=1) as executor:
                result = await loop.run_in_executor(executor, run_ffmpeg)
                return result
        
        # Create tasks for all frames
        logger.info(f"Starting parallel extraction of {frames_count} frames...")
        tasks = [extract_single_frame(i) for i in range(frames_count)]
        
        # Process in batches to avoid overwhelming the system
        batch_size = 20  # Process 20 frames at a time
        frames = [None] * frames_count
        
        for batch_start in range(0, frames_count, batch_size):
            batch_end = min(batch_start + batch_size, frames_count)
            batch_tasks = tasks[batch_start:batch_end]
            
            logger.info(f"Processing extraction batch {batch_start//batch_size + 1}/{(frames_count + batch_size - 1)//batch_size} "
                       f"(frames {batch_start + 1}-{batch_end})")
            
            # Execute batch in parallel
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
            
            # Process results
            for result in batch_results:
                if isinstance(result, Exception):
                    logger.error(f"Frame extraction exception: {str(result)}")
                    continue
                    
                frame_index, frame_path, error = result
                if error:
                    logger.warning(f"Failed to extract frame {frame_index}: {error}")
                    continue
                    
                if frame_path and os.path.exists(frame_path):
                    try:
                        # Load image and convert to numpy array
                        frame_img = Image.open(frame_path)
                        frame_array = np.array(frame_img)
                        frames[frame_index] = frame_array
                    except Exception as e:
                        logger.warning(f"Failed to load frame {frame_index}: {str(e)}")
            
            logger.info(f"Completed extraction batch {batch_start//batch_size + 1}")
        
        # Filter out None values and return successful frames
        successful_frames = [frame for frame in frames if frame is not None]
        logger.info(f"Successfully extracted {len(successful_frames)} out of {frames_count} frames")
        
        return successful_frames
    
    async def _process_frames_parallel(self, frames: List[np.ndarray]) -> List[str]:
        """
        Process frames in parallel batches to speed up description generation.
        
        Args:
            frames: List of frame arrays
            
        Returns:
            List of descriptions for each frame
        """
        import asyncio
        
        batch_size = 20  # Process 20 frames at a time
        descriptions = [None] * len(frames)  # Pre-allocate list to maintain order
        
        # Process frames in batches
        for batch_start in range(0, len(frames), batch_size):
            batch_end = min(batch_start + batch_size, len(frames))
            batch_frames = frames[batch_start:batch_end]
            
            logger.info(f"Processing batch {batch_start//batch_size + 1}/{(len(frames) + batch_size - 1)//batch_size} "
                       f"(frames {batch_start + 1}-{batch_end})")
            
            # Create tasks for this batch
            tasks = []
            for i, frame in enumerate(batch_frames):
                frame_index = batch_start + i
                task = self._get_frame_description(frame, frame_index)
                tasks.append(task)
            
            # Execute batch in parallel
            batch_descriptions = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Store results in correct positions
            for i, description in enumerate(batch_descriptions):
                frame_index = batch_start + i
                if isinstance(description, Exception):
                    logger.error(f"Error processing frame {frame_index + 1}: {str(description)}")
                    descriptions[frame_index] = f"Error analyzing frame {frame_index + 1}: {str(description)}"
                else:
                    descriptions[frame_index] = description
            
            logger.info(f"Completed batch {batch_start//batch_size + 1}, processed {len(batch_descriptions)} frames")
        
        # Filter out None values (shouldn't happen, but just in case)
        descriptions = [desc for desc in descriptions if desc is not None]
        
        return descriptions
    
    async def _get_frame_description(self, frame: np.ndarray, frame_index: int) -> str:
        """
        Get description for an individual frame using Gemini Vision.
        
        Args:
            frame: Frame array
            frame_index: Index of the frame
            
        Returns:
            Description of the frame
        """
        try:
            # Convert frame to bytes
            frame_img = Image.fromarray(frame)
            img_byte_arr = io.BytesIO()
            frame_img.save(img_byte_arr, format='JPEG', quality=85)
            frame_bytes = img_byte_arr.getvalue()
            
            prompt = f"""Analyze this frame from a video (frame {frame_index + 1}).

            Describe what you see in this frame, including:
            - What is happening in the scene
            - Any people, objects, or scenes visible
            - Colors, lighting, and visual elements
            - Any text, logos, or brands visible
            - Activities or actions being performed
            - The setting or environment

            Provide a concise but detailed description focusing on the key visual elements 
            and what's happening in this moment of the video."""
            
            response = await gemini_image_vision(
                model=settings.GEMINI_VISION_MODEL,
                prompt=prompt,
                img_bytes=frame_bytes
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
            logger.error(f"Error getting frame description for frame {frame_index}: {str(e)}")
            return f"Error analyzing frame {frame_index + 1}: {str(e)}"
    
    async def _generate_final_summary(self, descriptions: List[str]) -> str:
        """
        Generate a final summary of all frame descriptions.
        
        Args:
            descriptions: List of descriptions for each frame
            
        Returns:
            Final summary description
        """
        try:
            if not descriptions:
                return "No video content could be analyzed."
            
            # Limit descriptions to avoid token limits
            max_descriptions = 10
            if len(descriptions) > max_descriptions:
                # Take evenly spaced descriptions to represent the whole video
                step = len(descriptions) // max_descriptions
                descriptions = [descriptions[i] for i in range(0, len(descriptions), step)][:max_descriptions]
            
            combined_descriptions = "\n\n".join([
                f"Frame {i+1}: {desc}" for i, desc in enumerate(descriptions)
            ])
            
            prompt = f"""Analyze this video content based on individual frames and provide a concise summary in about 50 words.

Frame descriptions from the video:
{combined_descriptions}

Provide a clear, descriptive summary of what this video shows, including key subjects, activities, and visual elements. Focus on the overall story or content of the video."""
            
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

import asyncio
import time
import psutil
import os
import threading
import logging

# Global peak tracker
peak_mem = 0

def monitor_memory(process: psutil.Process, interval: float = 0.1):
    """Continuously track peak memory of process + children."""
    global peak_mem
    while process.is_running():
        try:
            # Current process memory
            mem = process.memory_info().rss

            # Add child processes (e.g., ffmpeg)
            for child in process.children(recursive=True):
                try:
                    mem += child.memory_info().rss
                except psutil.NoSuchProcess:
                    pass

            peak_mem = max(peak_mem, mem)
        except psutil.NoSuchProcess:
            break

        time.sleep(interval)

async def main():
    global peak_mem
    process = psutil.Process(os.getpid())

    # Start background memory monitor
    t = threading.Thread(target=monitor_memory, args=(process,), daemon=True)
    t.start()

    start_time = time.time()
    start_mem = process.memory_info().rss

    print(os.path.exists("/mnt/d/Github Repos/Vamory/Vamory-backend/temp_video.mp4"))

    video_desc = await video_processor.process_video(
        "/mnt/d/Github Repos/Vamory/Vamory-backend/temp_video.mp4"
    )

    end_time = time.time()
    end_mem = process.memory_info().rss
    elapsed_time = end_time - start_time
    mem_used_mb = (end_mem - start_mem) / (1024 * 1024)
    peak_used_mb = peak_mem / (1024 * 1024)

    print(f"Total time: {elapsed_time:.2f} seconds")
    print(f"RAM usage change: {mem_used_mb:.2f} MB")
    print(f"Peak RAM usage: {peak_used_mb:.2f} MB")
    print(video_desc)

if __name__ == "__main__":
    # Configure logging to show INFO level messages
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    asyncio.run(main())