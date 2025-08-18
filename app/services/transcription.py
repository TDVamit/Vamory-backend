import os
import time
import uuid
from typing import Any, Dict, Optional, List
import json

import requests
from app.config import settings
from app.services.s3 import s3_service
from app.services.gemini import gemini_text

class SpeechIsCheapError(Exception):
    pass


class SpeechIsCheapClient:
    API_BASE = "https://api.speechischeap.com/v2"

    def __init__(self, api_key: str, timeout: int = 30):
        self.api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

    def create_job(self, *, input_url: str, can_label_audio: bool = True) -> str:
        payload = {"input_url": input_url, "can_label_audio": can_label_audio}
        r = self._session.post(f"{self.API_BASE}/jobs/", json=payload, timeout=self.timeout)
        if r.status_code != 202:
            raise SpeechIsCheapError(f"Job creation failed ({r.status_code}): {r.text}")
        job_id = r.json().get("id")
        if not job_id:
            raise SpeechIsCheapError("No job ID returned.")
        return job_id

    def get_job(self, job_id: str) -> Dict[str, Any]:
        r = self._session.get(f"{self.API_BASE}/jobs/{job_id}", timeout=self.timeout)
        if r.status_code != 200:
            raise SpeechIsCheapError(f"Get job failed ({r.status_code}): {r.text}")
        return r.json()

    def wait_for_completion(self, job_id: str, *, poll_interval: float = 2.0, max_wait_seconds: int = 1800) -> Dict[str, Any]:
        start = time.time()
        while True:
            job = self.get_job(job_id)
            status = job.get("status")
            if status in ("COMPLETED", "FAILED", "CANCELED"):
                return job
            if time.time() - start > max_wait_seconds:
                raise SpeechIsCheapError("Polling job timeout.")
            time.sleep(poll_interval)


def estimate_tokens(text: str) -> int:
    """Rough estimation of tokens (1 token ≈ 4 characters for English text)"""
    return len(text) // 4


def split_text_by_tokens(text: str, max_tokens: int = 500000) -> List[str]:
    """Split text into chunks that don't exceed max_tokens"""
    chunks = []
    current_chunk = ""
    current_tokens = 0
    
    # Split by sentences to maintain context
    sentences = text.split('. ')
    
    for sentence in sentences:
        sentence_tokens = estimate_tokens(sentence)
        
        if current_tokens + sentence_tokens > max_tokens and current_chunk:
            chunks.append(current_chunk.strip())
            current_chunk = sentence
            current_tokens = sentence_tokens
        else:
            if current_chunk:
                current_chunk += ". " + sentence
            else:
                current_chunk = sentence
            current_tokens += sentence_tokens
    
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    return chunks


async def summarize_transcription(transcription_data: Dict[str, Any], model: str = "gemini-1.5-flash") -> str:
    """Summarize transcription data using Gemini"""
    try:
        # Extract text from transcription
        segments = transcription_data.get("output", {}).get("segments", [])
        if not segments:
            return "No transcription content found."
        
        # Combine all text from segments
        full_text = " ".join([seg.get("text", "") for seg in segments])
        
        # Create prompt for summarization
        prompt = f"""
        Please summarize the following transcription in exactly 50 words or less. 
        Focus on the main points and key information:
        
        Transcription: {full_text}
        
        Provide a concise summary that captures the essential content.
        """
        
        # Check if prompt exceeds token limit
        prompt_tokens = estimate_tokens(prompt)
        
        if prompt_tokens > 500000:
            # Split the transcription into chunks
            text_chunks = split_text_by_tokens(full_text, 400000)  # Leave room for prompt overhead
            
            summaries = []
            for chunk in text_chunks:
                chunk_prompt = f"""
                Please summarize the following transcription segment in exactly 50 words or less:
                
                {chunk}
                
                Provide a concise summary that captures the essential content.
                """
                
                response = await gemini_text(chunk_prompt, model)
                if isinstance(response, dict):
                    summary = response.get("summary") or response.get("text") or response.get("content") or str(response)
                else:
                    summary = str(response)
                
                summaries.append(summary)
            
            # Combine all summaries
            combined_summaries = " ".join(summaries)
            final_prompt = f"""
            Please combine and summarize the following summaries into exactly 50 words or less:
            
            {combined_summaries}
            
            Provide a final concise summary that captures the essential content from all parts.
            """
            
            final_response = await gemini_text(final_prompt, model)
            if isinstance(final_response, str):
                try:
                    final_data = json.loads(final_response)
                    return final_data.get("summary", final_data.get("text", final_response))
                except json.JSONDecodeError:
                    return final_response
            else:
                return str(final_response)
        else:
            # Single summarization call
            response = await gemini_text(prompt, model)
            if isinstance(response, dict):
                return response.get("summary") or response.get("text") or response.get("content") or str(response)
            else:
                return str(response)
                
    except Exception as e:
        return f"Error summarizing transcription: {str(e)}"


async def transcribe_via_s3(
    file_path: str,
    api_key: str = None,
    key_prefix: Optional[str] = None,
    expires_in: int = 3600,
) -> Dict[str, Any]:
    """Transcribe audio/video file using S3 and SpeechIsCheap"""
    if api_key is None:
        api_key = settings.speech_is_cheap_api_key
    
    # Generate unique S3 key
    file_extension = os.path.splitext(file_path)[1]
    unique_id = uuid.uuid4().hex[:12]
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    s3_key = f"transcription_temp/{base_name}-{unique_id}{file_extension}"
    
    try:
        # Upload file to S3
        with open(file_path, 'rb') as file_content:
            s3_url = await s3_service.upload_file(
                file_content=file_content,
                s3_key=s3_key,
                content_type=_get_content_type(file_path)
            )
        
        # Generate presigned URL for SpeechIsCheap
        presigned_url = await s3_service.generate_presigned_url(
            s3_key=s3_key,
            expiration=expires_in
        )
        
        if not presigned_url:
            raise Exception("Failed to generate presigned URL")
        
        # Transcribe using SpeechIsCheap
        client = SpeechIsCheapClient(api_key=api_key)
        job_id = client.create_job(input_url=presigned_url, can_label_audio=True)
        result = client.wait_for_completion(job_id)
        
        return result
        
    finally:
        # Clean up temporary file from S3
        await s3_service.delete_file(s3_key)


def _get_content_type(file_path: str) -> str:
    """Get content type based on file extension"""
    ext = os.path.splitext(file_path)[1].lower()
    return {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".mp4": "video/mp4",
        ".aac": "audio/aac",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
    }.get(ext, "application/octet-stream")


async def transcribe_and_summarize(
    file_path: str,
    api_key: str = None,
    model: str = "gemini-1.5-flash"
) -> Dict[str, Any]:
    """Transcribe file and return both transcription and summary"""
    try:
        # Transcribe the file
        transcription_result = await transcribe_via_s3(file_path, api_key)
        
        # Generate summary
        summary = await summarize_transcription(transcription_result, model)
        
        return {
            "transcription": transcription_result,
            "summary": summary,
            "success": True
        }
        
    except Exception as e:
        return {
            "transcription": None,
            "summary": None,
            "error": str(e),
            "success": False
        }


async def summarize_with_transcription_context(
    descriptions: List[str],
    transcription_data: Dict[str, Any],
    model: str = "gemini-1.5-flash"
) -> str:
    """Summarize video descriptions with transcription context"""
    try:
        # Extract transcription text
        segments = transcription_data.get("output", {}).get("segments", [])
        transcription_text = " ".join([seg.get("text", "") for seg in segments])
        
        # Combine descriptions
        combined_descriptions = " ".join(descriptions)
        
        # Create comprehensive prompt
        full_content = f"""
        Video Descriptions: {combined_descriptions}
        
        Transcription: {transcription_text}
        
        Please provide a comprehensive summary of this video content in exactly 50 words or less, 
        incorporating both the visual descriptions and the spoken content.
        """
        
        # Check token limit
        content_tokens = estimate_tokens(full_content)
        
        if content_tokens > 500000:
            # Split content into manageable chunks
            description_chunks = split_text_by_tokens(combined_descriptions, 200000)
            transcription_chunks = split_text_by_tokens(transcription_text, 200000)
            
            summaries = []
            
            # Process description chunks
            for chunk in description_chunks:
                chunk_prompt = f"""
                Summarize this video description segment in 25 words or less:
                {chunk}
                """
                response = await gemini_text(chunk_prompt, model)
                if isinstance(response, dict):
                    summary = response.get("summary") or response.get("text") or response.get("content") or str(response)
                else:
                    summary = str(response)
                summaries.append(summary)
            
            # Process transcription chunks
            for chunk in transcription_chunks:
                chunk_prompt = f"""
                Summarize this transcription segment in 25 words or less:
                {chunk}
                """
                response = await gemini_text(chunk_prompt, model)
                if isinstance(response, dict):
                    summary = response.get("summary") or response.get("text") or response.get("content") or str(response)
                else:
                    summary = str(response)
                summaries.append(summary)
            
            # Combine all summaries
            combined_summaries = " ".join(summaries)
            final_prompt = f"""
            Combine these summaries into a final 50-word summary of the video:
            {combined_summaries}
            """
            
            final_response = await gemini_text(final_prompt, model)
            if isinstance(final_response, dict):
                return final_response.get("summary") or final_response.get("text") or final_response.get("content") or str(final_response)
            else:
                return str(final_response)
        else:
            # Single summarization call
            response = await gemini_text(full_content, model)
            if isinstance(response, str):
                try:
                    response_data = json.loads(response)
                    return response_data.get("summary", response_data.get("text", response))
                except json.JSONDecodeError:
                    return response
            else:
                return str(response)
                
    except Exception as e:
        return f"Error creating comprehensive summary: {str(e)}"


if __name__ == "__main__":
    import asyncio
    
    async def test_transcription():
        file_path = '/mnt/d/Github Repos/Vamory/Vamory-backend/output.mp3'
        
        result = await transcribe_and_summarize(file_path)
        
        if result["success"]:
            print(f"Transcription: {result['transcription']}")
            print(f"Summary: {result['summary']}")
        else:
            print(f"Error: {result['error']}")
    
    asyncio.run(test_transcription())
