from google import genai
from google.genai import types
from app.config import settings
import json

gemini_API_KEY = settings.GEMINI_API_KEY


async def gemini_image_vision(model,prompt,img_bytes):
    # Initialize client (assumes GOOGLE_CLOUD_PROJECT and location set)
    client = genai.Client(api_key=gemini_API_KEY)
    # Prepare content parts: inlineData + user text
    contents = [
        types.Part.from_bytes(data=img_bytes, mime_type='image/jpeg'),
        types.Part.from_text(text=prompt)
    ]

    import asyncio
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: client.models.generate_content(
            model=f"models/{model}",
            contents=[types.Content(parts=contents)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
    )

    return json.loads(response.text)

async def gemini_text(prompt,model):
    # Initialize client (assumes GOOGLE_CLOUD_PROJECT and location set)
    client = genai.Client(api_key=gemini_API_KEY)
    # Prepare content parts: inlineData + user text
    contents = [
        types.Part.from_text(text=prompt)
    ]

    import asyncio
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: client.models.generate_content(
            model=f"models/{model}",
            contents=[types.Content(parts=contents)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
    )

    return json.loads(response.text)

async def gemini_image_vision_search(model,prompt,img_bytes):
    # Initialize client (assumes GOOGLE_CLOUD_PROJECT and location set)
    client = genai.Client(api_key=gemini_API_KEY)
    # Prepare content parts: inlineData + user text
    contents = [
        types.Part.from_bytes(data=img_bytes, mime_type='image/jpeg'),
        types.Part.from_text(text=prompt)
    ]    
    search_tool = types.Tool(google_search=types.GoogleSearch())

    import asyncio
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: client.models.generate_content(
            model=f"models/{model}",
            contents=[types.Content(parts=contents)],
            config=types.GenerateContentConfig(
                tools=[search_tool]
            ),
        )
    )

    return response.text



async def gemini_text_search(prompt, model, web_search=False):
    client = genai.Client(api_key=gemini_API_KEY)

    # Prepare text content
    contents = [
        types.Part.from_text(text=prompt)
    ]

    # Conditionally add the Google Search tool
    tools = [types.Tool(google_search=types.GoogleSearch())] if web_search else []

    import asyncio
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: client.models.generate_content(
            model=f"models/{model}",
            contents=[types.Content(parts=contents)],
            config=types.GenerateContentConfig(
                tools=tools,
                response_mime_type="application/json" if not web_search else None
            ),
        )
    )

    return response.text
