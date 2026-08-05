from fastapi import FastAPI, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from google.genai import types
from dotenv import load_dotenv
import os
import json
import time

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class TopicRequest(BaseModel):
    topic: str
    difficulty: str = "Medium"
    num_questions: int = 5

MCQ_INSTRUCTIONS = """Return ONLY valid JSON, no other text, in exactly this format:
{
  "questions": [
    {
      "question": "...",
      "options": ["...", "...", "...", "..."],
      "correct_index": 0,
      "explanation": "...",
      "hint": "..."
    }
  ]
}
correct_index is the 0-based index (0, 1, 2, or 3) of the correct option in the options array.
Give exactly 4 options per question.
The "hint" should be a short one-sentence nudge that helps without giving away the answer.
"""

def parse_json_response(raw_text):
    raw_text = raw_text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()
    return json.loads(raw_text)

def clamp_count(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 5
    return max(1, min(n, 20))

# Try these models in order -- if one is busy/unavailable, fall back to the next
MODEL_FALLBACK_CHAIN = ["gemini-3-flash-preview", "gemini-2.5-flash-lite", "gemini-2.5-flash"]

def generate_with_retry(contents, max_retries_per_model=2, retry_delay_seconds=2):
    """
    Tries each model in MODEL_FALLBACK_CHAIN. For each model, retries a couple
    times with a short delay if the server reports it's busy (503).
    Raises the last error if every model/attempt fails.
    """
    last_error = None
    for model_name in MODEL_FALLBACK_CHAIN:
        for attempt in range(max_retries_per_model):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents
                )
                return response
            except Exception as e:
                last_error = e
                # Only worth retrying on "busy" style errors -- otherwise move on fast
                if "UNAVAILABLE" in str(e) or "503" in str(e):
                    time.sleep(retry_delay_seconds)
                    continue
                else:
                    break  # non-busy error, no point retrying this model
    raise last_error

@app.post("/generate")
def generate_questions(request: TopicRequest):
    count = clamp_count(request.num_questions)
    prompt = f"""Generate {count} multiple choice practice questions about "{request.topic}" for a computer science student.
Difficulty level: {request.difficulty}.
{MCQ_INSTRUCTIONS}"""

    try:
        response = generate_with_retry(prompt)
        parsed = parse_json_response(response.text)
        return {"topic": request.topic, "questions": parsed["questions"]}
    except Exception as e:
        return {"topic": request.topic, "questions": [], "error": str(e)[:150]}


@app.post("/generate-from-image")
async def generate_from_image(file: UploadFile = File(...), num_questions: int = Form(5)):
    image_bytes = await file.read()
    count = clamp_count(num_questions)

    prompt = f"""Look at this photo of exam or textbook questions.
First, read and understand the topics and concepts being tested in the image.
Then generate {count} NEW multiple choice practice questions that test the SAME topics/concepts,
in a similar style and difficulty -- do not just copy the original questions.
{MCQ_INSTRUCTIONS}"""

    try:
        response = generate_with_retry([
            types.Part.from_bytes(
                data=image_bytes,
                mime_type=file.content_type or "image/jpeg"
            ),
            prompt
        ])
        parsed = parse_json_response(response.text)
        return {"topic": "From your uploaded photo", "questions": parsed["questions"]}
    except Exception as e:
        return {"topic": "From your uploaded photo", "questions": [], "error": str(e)[:150]}