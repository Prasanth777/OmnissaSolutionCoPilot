# Omnissa Agentic HLD Copilot

Agentic chatbot for solution architects to produce customer-tailored High Level Design (HLD) PowerPoint decks.

## Core behavior

- Uses **only** `https://techzone.omnissa.com` content
- Crawls only from `https://techzone.omnissa.com/sitemap.xml`
- By default ingests only `/resource/` URLs from sitemap (`SITEMAP_RESOURCE_ONLY=1`)
- Builds a local Chroma vector store for RAG
- Downloads Tech Zone images and stores local image-caption mappings
- Uses Azure AI Foundry (Azure OpenAI cognitive services) for:
  - response synthesis
  - image captions
- Uses Hugging Face open-source embeddings (default: `BAAI/bge-large-en-v1.5`) for RAG indexing and retrieval
- Enforces guardrails so answers are returned only when grounded with Tech Zone citations

## Agent architecture

- `Questionnaire Agent`: guided HLD interview with predefined clickable answers
- `Retrieval Agent`: semantic retrieval from Chroma
- `Solution Agent`: synthesizes architecture answer from retrieved chunks only
- `Guardrail Agent`: blocks injection patterns and non-grounded/non-Tech Zone output

## Supported product drill-downs

- Horizon 8
- Horizon Cloud
- App Volumes
- Dynamic Environment Manager
- Workspace ONE UEM
- Omnissa Access
- Unified Access Gateway

## Setup

```bash
python3 -m venv .venv_local
source .venv_local/bin/activate
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

Playwright is used as a browser fallback when Tech Zone blocks non-browser HTTP clients with 403 responses.

## Required environment variables

```bash
export AZURE_OPENAI_ENDPOINT="https://<your-resource>.openai.azure.com"
export AZURE_OPENAI_API_KEY="<api-key>"
export AZURE_OPENAI_API_VERSION="2024-10-21"
export AZURE_OPENAI_CHAT_DEPLOYMENT="<chat-deployment>"
export AZURE_OPENAI_VISION_DEPLOYMENT="<vision-deployment-optional>"
export HF_EMBEDDING_MODEL="BAAI/bge-large-en-v1.5"
```

The app auto-loads environment variables from `.env` and `.env.local` at startup.

## Run

```bash
streamlit run app.py
```

If you see `transformers` watcher warnings on macOS/Python 3.13, ensure dependencies are up to date (`torchvision` is included) and use Streamlit watcher disablement in `.streamlit/config.toml`.

## Usage flow

1. Configure Azure environment variables
2. Open app and click **Build / Rebuild Tech Zone RAG**
3. Select families and products
4. Answer guided questions via clickable suggestions or custom input
5. Ask additional design questions (RAG-only answers)
6. Generate customer-facing HLD PPT with retrieved images and citations

## Data paths

- Chroma DB: `data/chroma/`
- Downloaded images: `data/images/`
- Image caption mappings: `data/image_captions.jsonl`
- Generated decks: `output/`
