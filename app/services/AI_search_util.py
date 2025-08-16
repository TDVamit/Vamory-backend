from app.services.gemini import gemini_image_vision,gemini_text
from typing import Union
from app.config import settings

async def get_image_description(image_bytes: Union[bytes, bytearray]) -> str:
    prompt = f"""
    Analyze the image and provide a detailed description of the image.
    Make sure to describe the image in detail.
    include every minute detail of the image.
    like scene, objects, people, etc.
    description of background, foreground, etc.
    color of objects, clothing etc.
    all the brands detected in the image.
    all the logos.
    all the activities in the image.
    every single detail of the image.

    return in json format strictly.
    {{
        "description": "description of the image",
    }}
    """
    response = await gemini_image_vision(model=settings.GEMINI_VISION_MODEL,prompt=prompt,img_bytes=image_bytes)
    return response["description"]

async def enhance_search(query:str):
    prompt = f"""
    You are a helpful assistant that can enhance search results.
    You are given a query 
    You need to enhance the search query by making it more descriptive.
    also you need to returns mentioned name and remove them from the query

    for example if query is "john is sitting beside alex" then you need to return
    {{
    "enhanced_query": "boy sitting beside girl",
    "mentioned_names": ["john","alex"]
    }}
    query: {query}

    return in json format strictly.
    {{
        "enhanced_query": "enhanced query",
        "mentioned_names": ["mentioned names"]
    }}
    """
    response = await gemini_text(prompt=prompt,model=settings.GEMINI_VISION_MODEL)
    return response