import os
import re
import json
import base64
import httpx
from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Paperless LLM OCR")

# --- Configuration (set via environment variables) ---
PAPERLESS_URL = os.getenv("PAPERLESS_URL", "http://paperless-ngx:8000").rstrip("/")
PAPERLESS_TOKEN = os.getenv("PAPERLESS_TOKEN", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
MODEL = os.getenv("MODEL", "google/gemini-3.1-flash-lite-preview")
TRIGGER_TAG_NAME = os.getenv("TRIGGER_TAG_NAME", "Run LLM OCR")
PROCESSED_TAG_NAME = os.getenv("PROCESSED_TAG_NAME", "")  # Leave blank to disable
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "0")) or None  # 0 = let the API decide
CUSTOM_PROMPT = os.getenv("CUSTOM_PROMPT", "")  # Overrides the built-in OCR prompt if set

if not PAPERLESS_TOKEN:
    raise RuntimeError("PAPERLESS_TOKEN environment variable is required")
if not LLM_API_KEY:
    raise RuntimeError("LLM_API_KEY environment variable is required")

OCR_PROMPT = """You are a document digitization assistant. Your job is to fully transcribe and classify a document.

Rules:
- text: Produce a complete Markdown transcription of every word on the document. Do not summarize, skip, or paraphrase anything. Follow these formatting rules:
    - [Page Number: X] at the beginning of each page
    - Use `#`, `##`, `###` for headings and section titles as they appear
    - Use `**bold**` for labels, field names, or emphasized text
    - Use `|table|format|` for any tabular data — reproduce every row and column
    - Use `-` bullet lists for itemized content
    - Use `>` blockquotes for quoted or indented passages
    - Preserve all amounts, dates, addresses, phone numbers, reference numbers, and codes exactly as printed
    - If a field is blank on the document, write the label followed by *(blank)*
    - Do not add commentary, headers, or any content that is not on the document
    - Important: At the end of the document, add "-- END OF DOCUMENT --"
- title: A short, descriptive title (e.g. "Invoice - Acme Corp - March 2025", "Meeting Agenda - Board - Jan 12 2025").
- date: The primary date of the document in YYYY-MM-DD format. Use the document date, invoice date, meeting date, etc. If no date is found, return null.
- correspondent: The name of the organization or person who sent or authored the document (e.g. "AT&T", "IRS", "Dr. John Smith"). Return null if unclear.
- document_type: One of the existing types if it fits, otherwise a short category like "Invoice", "Letter", "Statement", "Meeting Minutes", "Contract", "Receipt", etc.
- tags: 3 to 8 short tags that categorize this document. Prefer reusing existing tags.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "text": {"type": "string"},
        "date": {"type": ["string", "null"]},
        "correspondent": {"type": ["string", "null"]},
        "document_type": {"type": ["string", "null"]},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "text", "date", "correspondent", "document_type", "tags"],
    "additionalProperties": False,
}


def paginated_get(client, url, headers):
    """Fetch all results from a paginated Paperless API endpoint."""
    results = []
    while url:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("results", []))
        url = data.get("next")
    return results


def get_or_create(client, endpoint, name, headers):
    """Find an existing item by name (case-insensitive) or create it. Returns ID or None."""
    items = paginated_get(client, f"{PAPERLESS_URL}/api/{endpoint}/", headers)
    lookup = {i["name"].lower(): i["id"] for i in items}
    key = name.lower().strip()
    if key in lookup:
        return lookup[key]
    resp = client.post(f"{PAPERLESS_URL}/api/{endpoint}/", headers=headers, json={"name": name.strip()})
    if resp.status_code == 201:
        return resp.json()["id"]
    print(f"Failed to create {endpoint} '{name}': {resp.status_code} {resp.text}")
    return None


def process_document(doc_id: int):
    print(f"[{doc_id}] Starting OCR processing")
    headers = {"Authorization": f"Token {PAPERLESS_TOKEN}"}

    with httpx.Client(timeout=120) as client:
        # 1. Download the PDF
        try:
            doc_resp = client.get(
                f"{PAPERLESS_URL}/api/documents/{doc_id}/download/",
                headers=headers,
                timeout=60,
            )
            doc_resp.raise_for_status()
            base64_pdf = base64.b64encode(doc_resp.content).decode("utf-8")
        except Exception as e:
            print(f"[{doc_id}] Failed to download document: {e}")
            return

        # 2. Fetch existing tags, correspondents, document types for context
        try:
            existing_tags = {t["name"].lower(): t["id"] for t in paginated_get(client, f"{PAPERLESS_URL}/api/tags/", headers)}
            existing_correspondents = [c["name"] for c in paginated_get(client, f"{PAPERLESS_URL}/api/correspondents/", headers)]
            existing_doctypes = [d["name"] for d in paginated_get(client, f"{PAPERLESS_URL}/api/document_types/", headers)]
        except Exception as e:
            print(f"[{doc_id}] Failed to fetch metadata: {e}")
            existing_tags, existing_correspondents, existing_doctypes = {}, [], []

        # 3. Call LLM
        base_prompt = CUSTOM_PROMPT if CUSTOM_PROMPT else OCR_PROMPT
        prompt = (
            f"{base_prompt}\n"
            f"Existing tags to prefer: {list(existing_tags.keys())}\n"
            f"Existing correspondents to prefer: {existing_correspondents}\n"
            f"Existing document types to prefer: {existing_doctypes}"
        )

        llm_payload = {
            "model": MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:application/pdf;base64,{base64_pdf}"},
                        },
                    ],
                }
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "ocr_result", "strict": True, "schema": SCHEMA},
            },
        }
        if MAX_TOKENS:
            llm_payload["max_tokens"] = MAX_TOKENS

        llm_headers = {
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            resp = client.post(
                f"{LLM_BASE_URL}/chat/completions",
                headers=llm_headers,
                json=llm_payload,
            )
            resp.raise_for_status()
            raw_content = resp.json()["choices"][0]["message"]["content"]
            extracted = json.loads(raw_content)
        except Exception as e:
            print(f"[{doc_id}] LLM error: {e}")
            return

        ocr_text = extracted.get("text", "").strip()
        title = extracted.get("title", "").strip()
        date = extracted.get("date")
        correspondent_name = extracted.get("correspondent")
        document_type_name = extracted.get("document_type")
        suggested_tags = extracted.get("tags", [])

        print(
            f"[{doc_id}] title={title!r} date={date!r} "
            f"correspondent={correspondent_name!r} type={document_type_name!r} "
            f"tags={suggested_tags} text_len={len(ocr_text)}"
        )

        # 4. Resolve/create tags, correspondent, document type
        final_tag_ids = []
        for tag_name in suggested_tags:
            tag_lower = tag_name.lower().strip()
            if not tag_lower or tag_lower == TRIGGER_TAG_NAME.lower():
                continue
            if tag_lower in existing_tags:
                final_tag_ids.append(existing_tags[tag_lower])
            else:
                new_id = get_or_create(client, "tags", tag_name, headers)
                if new_id:
                    final_tag_ids.append(new_id)
                    existing_tags[tag_lower] = new_id

        if PROCESSED_TAG_NAME:
            processed_id = get_or_create(client, "tags", PROCESSED_TAG_NAME, headers)
            if processed_id:
                final_tag_ids.append(processed_id)

        correspondent_id = None
        if correspondent_name:
            correspondent_id = get_or_create(client, "correspondents", correspondent_name, headers)

        doctype_id = None
        if document_type_name:
            doctype_id = get_or_create(client, "document_types", document_type_name, headers)

        # 5. Patch the document
        update_data = {
            "content": ocr_text,
            "tags": final_tag_ids,
        }
        if title:
            update_data["title"] = title
        if date and re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            update_data["created"] = date
        if correspondent_id:
            update_data["correspondent"] = correspondent_id
        if doctype_id:
            update_data["document_type"] = doctype_id

        try:
            patch_resp = client.patch(
                f"{PAPERLESS_URL}/api/documents/{doc_id}/",
                headers=headers,
                json=update_data,
                timeout=30,
            )
            patch_resp.raise_for_status()
            print(f"[{doc_id}] Successfully updated document.")
        except Exception as e:
            print(f"[{doc_id}] Failed to update document: {e}")


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL, "paperless_url": PAPERLESS_URL}


def extract_doc_id(payload) -> int | None:
    """Extract document ID from payload. Handles dict, double-encoded JSON string, or bare URL."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            match = re.search(r"/documents/(\d+)", payload)
            return int(match.group(1)) if match else None

    if not isinstance(payload, dict):
        return None

    if "document_id" in payload:
        val = payload["document_id"]
        return int(val) if str(val).isdigit() else None

    if "doc_url" in payload:
        match = re.search(r"/documents/(\d+)", str(payload["doc_url"]))
        return int(match.group(1)) if match else None

    for val in payload.values():
        if isinstance(val, str):
            match = re.search(r"/documents/(\d+)", val)
            if match:
                return int(match.group(1))

    return None


@app.post("/webhook")
async def webhook_receiver(request: Request, background_tasks: BackgroundTasks):
    """Receives a Paperless webhook and queues OCR processing in the background."""
    try:
        payload = await request.json()
    except Exception:
        raw = await request.body()
        print(f"[webhook] failed to parse JSON, raw body: {raw.decode()}")
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid JSON"})

    print(f"[webhook] received payload type={type(payload).__name__}")
    doc_id = extract_doc_id(payload)
    if not doc_id:
        return JSONResponse(status_code=400, content={"status": "error", "message": "No document_id or doc_url in payload"})

    background_tasks.add_task(process_document, doc_id)
    print(f"[{doc_id}] Queued for processing")
    return {"status": "queued", "document_id": doc_id}
