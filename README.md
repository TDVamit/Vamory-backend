# Vamory backend

Vamory is a FastAPI service for a full-stack gallery and storage platform: it manages users, folders, media metadata, sharing, search, thumbnails, and large video uploads while storing binaries in Amazon S3 and metadata in MongoDB.

Repository: [TDVamit/Vamory-backend](https://github.com/TDVamit/Vamory-backend)

## What it provides

- JWT-protected API endpoints for users, profiles, folders, files, notifications, costs, and storage usage.
- Folder hierarchies with owner/access-level sharing, public links, pagination, filtering, sorting, recursive statistics, recycle-bin behavior, and storage-class inheritance.
- S3-backed uploads and downloads using presigned URLs, including multipart handling for videos up to the configured `MAX_VIDEO_SIZE` (10 GB in the checked-in example configuration).
- Three storage policies: S3 Standard-IA, Glacier Instant Retrieval, and Glacier Deep Archive, with tracked conversion/retrieval jobs and thumbnail lifecycle rules.
- Image metadata and WebP thumbnail generation through Pillow; thumbnails are skipped for Deep Archive items.
- Video-oriented CDN upload/status endpoints and an HLS queue listener for asynchronous processing.
- Google Drive ingestion, face detection/embedding workflows, AI image search, cost calculation, exchange-rate lookup, email notifications, and scheduled maintenance jobs.

## Architecture

```text
React/Vite client ── HTTPS/JSON + bearer tokens ──> FastAPI
                                                       │
                         ┌─────────────────────────────┼──────────────────────────┐
                         ▼                             ▼                          ▼
                   MongoDB metadata              Amazon S3                  Auth0/JWT
                         │                             │
                         ▼                             ▼
              Qdrant / AI services       CloudFront + SQS/HLS workers
```

`main.py` mounts the feature routers under `/api/v1`, opens the MongoDB connection during application startup, and starts the HLS queue listener as a background task. The service also loads the bundled MobileFaceNet TensorFlow Lite model at startup.

## Technology

- Python 3.10–3.12, FastAPI, Uvicorn, Pydantic Settings
- MongoDB with Motor/PyMongo
- AWS S3, CloudFront signed cookies, SQS, and `aioboto3`
- JWT/Auth0 integration, `python-jose`, bcrypt/passlib
- Pillow, FFmpeg bindings, MediaPipe, TensorFlow Lite runtime
- Google Gemini, OpenAI, LangChain, FAISS, and Qdrant integrations

## Local setup

### Prerequisites

- Python 3.10–3.12
- MongoDB
- An S3 bucket and IAM credentials with the permissions needed by your deployment
- FFmpeg for video-related processing
- The external services used by the features you enable (Auth0, Qdrant, AI providers, mail, CDN/SQS)

### Install and configure

```bash
git clone https://github.com/TDVamit/Vamory-backend.git
cd Vamory-backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env
```

Populate `.env` with real values locally. Never commit `.env`, credentials, private keys, or provider tokens. The checked-in `env.example` is intentionally incomplete for the later integrations; `app/config.py` currently requires the variables below as well:

```dotenv
MONGODB_URL=mongodb://localhost:27017
DATABASE_NAME=gallery_db
SECRET_KEY=<long-random-value>
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7
AWS_ACCESS_KEY_ID=<access-key>
AWS_SECRET_ACCESS_KEY=<secret-key>
AWS_REGION=<aws-region>
S3_BUCKET_NAME=<bucket-name>
DRIVE_API_KEY=<drive-provider-key>
GEMINI_API_KEY=<gemini-key>
GEMINI_VISION_MODEL=<model-name>
OPENAI_API_KEY=<openai-key>
OPENAI_EMBEDDING_MODEL=<model-name>
SPEECH_IS_CHEAP_API_KEY=<speech-provider-key>
QDRANT_API_KEY=<qdrant-key>
QDRANT_URL=<qdrant-url>
AUTH0_DOMAIN=<auth0-domain>
API_AUDIENCE=<auth0-audience>
AUTH0_CLIENT_ID=<auth0-client-id>
AUTH0_CLIENT_SECRET=<auth0-client-secret>
AUTH0_CANONICAL_DOMAIN=<auth0-canonical-domain>
AUTH0_API_CLIENT_ID=<auth0-api-client-id>
PUBLIC_SECRET_KEY=<public-token-secret>
MAILGUN_API_KEY=<mailgun-key>
MAILGUN_BASE_URL=<mailgun-base-url>
FROM_NAME=<sender-name>
EMAIL_DOMAIN=<email-domain>
EMAIL_NAME=<email-name>
SEND_DOMAIN=<send-domain>
CDN_KEY_GROUP_ID=<cloudfront-key-group-id>
CDN_PRIVATE_KEY_PATH=<path-to-private-key>
CDN_DOMAIN_NAME=<cdn-domain>
SQS_URL=<sqs-queue-url>
REDIS_URL=<redis-url>
```

The variable names are case-insensitive under Pydantic Settings. Use the uppercase spelling above for clarity. The bundled TensorFlow Lite wheel is Linux CPython 3.10-specific; installations on other platforms may need a compatible runtime package.

### Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The API serves interactive documentation at `http://localhost:8000/docs`, ReDoc at `/redoc`, and a health endpoint at `/health`. The cron scripts (`5minute_cron_job.py`, `hourly_cron_job.py`, and `daily_cron_job.py`) are separate processes and must be scheduled by the deployment environment when their related features are enabled.

## API surface

All application routers are mounted under `/api/v1`:

| Area | Examples |
| --- | --- |
| Auth | profile, user search, role updates, profile images |
| Folders | hierarchy, sharing, public folders, stats, storage conversion |
| Files | upload/presign/complete, download, thumbnails, public access, trash, Google Drive import |
| AI search | private and public image search |
| Faces | detection results, naming, merging, unknown faces, file references |
| CDN | uploads, signed access, upload status, HLS status |
| Costs | exchange rates, storage cost estimates, admin-managed cost structure |
| Notifications | user notification retrieval and read state |

The OpenAPI document generated by the running service is the authoritative contract for request and response details.

## Current status and operational notes

- The repository is an active application codebase with production-oriented domains and configured production origins, but no Docker, Terraform, or CI deployment definition is present in the repository.
- There is no automated test suite visible in the checked-in tree; validate changes with the local API docs, a configured integration environment, and frontend flows.
- AI search, face recognition, Google Drive imports, email, Redis, CDN signing, SQS/HLS processing, and scheduled jobs are integration-dependent. They should be treated as optional until their credentials, permissions, workers, and monitoring are provisioned.
- Configure CORS for the actual frontend origin before deploying; the current allow-list includes the Vamory production origin and local development origins.

## Related project

The companion Vite/React client lives in [TDVamit/Vamory-frontend](https://github.com/TDVamit/Vamory-frontend).
