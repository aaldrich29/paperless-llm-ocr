# paperless-llm-ocr

A lightweight FastAPI microservice that replaces Paperless-ngx's built-in Tesseract OCR with a vision-capable LLM. Drop a document into Paperless and the service automatically:

- Transcribes the full text as Markdown
- Sets a descriptive title
- Detects the document date
- Identifies the correspondent (sender/author)
- Assigns a document type
- Suggests and applies 3–8 tags — prefers your existing tags, correspondents, and document types before creating new ones

Works with any OpenAI-compatible API — [OpenRouter](https://openrouter.ai) (Gemini, Claude, GPT-4o, etc.) or OpenAI directly.

### How It Works

1. **Deploy** this container on the same Docker network as Paperless-ngx
2. **Create a webhook** in Paperless that fires on new documents (and/or when a tag is added)
3. **Documents are processed automatically** — or manually by adding a trigger tag (default: `Run LLM OCR`)
4. *(Optional)* [Disable Paperless built-in OCR](#disable-paperless-built-in-ocr) to avoid redundant Tesseract processing
5. *(Optional)* [Customize the OCR prompt](#configuration) via the `CUSTOM_PROMPT` environment variable

The service receives the webhook, downloads the PDF from Paperless, fetches your existing tags/correspondents/document types for context, sends everything to the LLM, and writes the results back via the Paperless API.

### Why This Beats Tesseract

Paperless-ngx ships with Tesseract, which struggles with anything that isn't a clean, high-contrast, machine-printed document. Skewed scans, low-resolution photos, stylized fonts, and handwriting all produce garbled or empty text.

The default model is **`google/gemini-flash-lite-3.1`** via OpenRouter:

- **Highly accurate** — handles printed text, handwriting, tables, receipts, and mixed layouts
- **Extremely cheap** — most documents cost a fraction of a cent to process
- **Fast** — typical document turnaround is a few seconds
- **Handwriting support** — works well on handwritten notes and forms

---

## Quick Start

The fastest way to get running — just create a compose file and a `.env`:

**1. Create `docker-compose.yml`:**

```yaml
services:
  paperless-llm-ocr:
    image: ghcr.io/aaldrich29/paperless-llm-ocr:latest
    # To build locally instead, comment out "image:" and uncomment:
    # build: .
    container_name: paperless-llm-ocr
    restart: unless-stopped
    # Uncomment to expose the port externally (e.g. if not on the same
    # Docker network as Paperless). Note: the webhook has no authentication.
    # ports:
    #   - "8080:8080"
    environment:
      - PAPERLESS_URL=http://paperless-ngx:8000
      - PAPERLESS_TOKEN=${PAPERLESS_TOKEN}
      - LLM_API_KEY=${LLM_API_KEY}
      # Optional:
      #- MODEL=google/gemini-3.1-flash-lite-preview
      #- LLM_BASE_URL=https://openrouter.ai/api/v1
      #- TRIGGER_TAG_NAME=Run LLM OCR
      - PROCESSED_TAG_NAME=OCR Done
      #- MAX_TOKENS=4096

    # If Paperless is on a different Docker network:
    # networks:
    #   - paperless_net

# networks:
#   paperless_net:
#     external: true
#     name: paperless_default
```

**2. Create `.env`:**

```env
PAPERLESS_TOKEN=your_paperless_token_here
LLM_API_KEY=your_api_key_here
```

**3. Start:**

```bash
docker compose up -d
```

**4. Verify:**

```bash
docker exec paperless-llm-ocr python -c "import httpx; print(httpx.get('http://localhost:8080/health').json())"
```

---

## Building From Source

If you prefer to build the container yourself:

```bash
git clone https://github.com/aaldrich29/paperless-llm-ocr.git
cd paperless-llm-ocr
```

Edit `docker-compose.yml` — comment out the `image:` line and uncomment `build: .`:

```yaml
services:
  paperless-llm-ocr:
    # image: ghcr.io/aaldrich29/paperless-llm-ocr:latest
    build: .
```

Then:

```bash
cp .env.example .env
# Edit .env with your values
docker compose up -d
```

---

## Setup

### Get Your API Key

**OpenRouter (recommended):**

1. Sign up at [openrouter.ai](https://openrouter.ai)
2. Go to **Keys** → **Create Key**
3. Copy the key (starts with `sk-or-v1-...`)
4. Set `LLM_API_KEY` to this value

**OpenAI directly:**

1. Go to [platform.openai.com/api-keys](https://platform.openai.com/api-keys) → **Create new secret key**
2. Set `LLM_API_KEY` to the key
3. Set `LLM_BASE_URL=https://api.openai.com/v1`
4. Set `MODEL=gpt-4o` or `gpt-4o-mini`

### Get Your Paperless API Token

1. Log into Paperless-ngx
2. Go to your profile (top-right) → **My Profile**
3. Under **API Auth Token**, click **Create Token** if one doesn't exist
4. Copy the token and set it as `PAPERLESS_TOKEN`

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `PAPERLESS_URL` | `http://paperless-ngx:8000` | Internal Docker URL or IP:port of Paperless |
| `PAPERLESS_TOKEN` | *(required)* | Paperless API token |
| `LLM_API_KEY` | *(required)* | API key for your LLM provider |
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` | Base URL of any OpenAI-compatible API |
| `MODEL` | `google/gemini-3.1-flash-lite-preview` | Vision-capable model to use |
| `TRIGGER_TAG_NAME` | `Run LLM OCR` | Tag that triggers manual reprocessing |
| `PROCESSED_TAG_NAME` | *(disabled)* | Tag added after successful processing |
| `MAX_TOKENS` | *(API default)* | Max output tokens — increase if text gets cut off |
| `CUSTOM_PROMPT` | *(built-in)* | Replace the built-in OCR prompt entirely |

> **Network note:** If this service and Paperless-ngx are on the same Docker Compose network, use the container/service name as the hostname (e.g. `http://paperless-ngx:8000`). If they are on separate stacks, use the host IP and exposed port, or connect them via a shared external Docker network.

---

## Disable Paperless Built-in OCR

Since this service handles all OCR, tell Paperless to skip it. Add this to your Paperless `docker-compose.yml` under the `webserver` environment:

```yaml
PAPERLESS_OCR_MODE: skip_noarchive
```

Then restart Paperless:
```bash
docker compose up -d webserver
```

---

## Configure Paperless Workflows

This service is triggered by Paperless **Workflows**. The webhook URL uses the container name (`paperless-llm-ocr`) which works when both services share a Docker network. If they're on separate networks, use the host IP and exposed port instead (e.g. `http://192.168.1.50:8080/webhook`).

### Workflow 1 — Automatic (process every new document)

1. In Paperless, go to **Settings → Workflows → Add Workflow**
2. Configure:
   - **Name:** LLM OCR - Auto
   - **Trigger:** Document Added
   - **Filter:** *(leave blank to process all documents)*
   - **Action:** Webhook
   - **Webhook URL:** `http://paperless-llm-ocr:8080/webhook`
   - **Send as JSON:** Yes
   - **Body:**
     ```json
     {"doc_url": "{{ doc_url }}"}
     ```
3. Save

### Workflow 2 — Manual (reprocess an existing document)

1. Go to **Settings → Tags → Add Tag** and create a tag named exactly `Run LLM OCR` (or whatever you set `TRIGGER_TAG_NAME` to)
2. Go to **Settings → Workflows → Add Workflow**
3. Configure:
   - **Name:** LLM OCR - Manual
   - **Trigger:** Document Updated
   - **Filter:** Has tag `Run LLM OCR`
   - **Action:** Webhook
   - **Webhook URL:** `http://paperless-llm-ocr:8080/webhook`
   - **Send as JSON:** Yes
   - **Body:**
     ```json
     {"doc_url": "{{ doc_url }}"}
     ```
4. Save

> **Why `doc_url` instead of `document.id`?** Paperless-ngx's "Document Updated" trigger does not expose `document` as a template variable. The `doc_url` placeholder is available for all trigger types and contains the document ID in the URL path, which this service parses automatically.

---

## What Gets Updated

| Field | Source |
|---|---|
| **Content** | Full Markdown transcription of all text |
| **Title** | Descriptive title generated by the LLM |
| **Created date** | Date detected from document content |
| **Correspondent** | Sender/author identified from document |
| **Document type** | Category detected (Invoice, Letter, etc.) |
| **Tags** | 3–8 tags, reusing existing ones where possible |

The manual trigger tag (`Run LLM OCR`) is never re-added to the document after processing.

---

## Supported Models

Any model accessible via an OpenAI-compatible API that supports **vision input** (images or PDFs). Recommended:

| Provider | Model | Notes |
|---|---|---|
| OpenRouter | `google/gemini-flash-lite-3.1` | **Recommended** — highly accurate, fraction of a cent per doc |
| OpenRouter | `google/gemini-3-flash-preview` | Newer Gemini, slightly higher cost |

---

## License

MIT
