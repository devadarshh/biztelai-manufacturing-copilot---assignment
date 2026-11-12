# BiztelAI Manufacturing Copilot Assignment Audit

## Final status

| Requirement | Status | Evidence / remaining work |
|---|---|---|
| Preserve the existing React/Vite frontend and FastAPI backend | PASS | Existing application structure was extended, not replaced. |
| PDF extraction, chunking, and metadata | PASS | PyMuPDF extraction and chunking are implemented. The runtime reports all four manufacturing PDFs in `sample-docs/` as ready. |
| Supabase Storage as primary PDF source | PASS (mocked); NOT LIVE-VERIFIED | Configured bucket defaults to `files`; root PDFs sync atomically to a private cache. A mocked SDK verifies update caching, empty-bucket semantics, and local fallback. No live Supabase request was made. |
| Local-folder fallback and status reporting | PASS (mocked) | Sync/configuration failures select `DOCUMENT_STORAGE_PATH`, emit a server log, and expose an explicit warning through `GET /api/storage` and the UI. A successful empty remote bucket does not use local files. |
| PDF uploads and background ingestion | PASS (tests); NOT LIVE-VERIFIED | UI upload supports local synchronous indexing and queued Redis/RQ processing with shared Supabase and Qdrant. Queued uploads fail clearly if shared storage cannot save the PDF. No live services were contacted. |
| Embeddings, Qdrant persistence, and Gemini answers | PASS (local evaluation); PARTIAL (reliability) | Local embeddings, Qdrant, and Gemini were exercised against the sample corpus. Answers were returned, but repeated requests intermittently returned HTTP 503; see the evaluation reports and limitations. |
| Duplicate/replacement ingestion behavior | PASS (unit tests) | Deterministic IDs and stale-source cleanup are tested with fakes/local Qdrant. |
| Revision awareness for QC-SOP-014 Rev03/Rev04 | PASS (local runtime) | Live document inventory reports Rev03 superseded and Rev04 current. |
| Ten assignment questions and citations | PASS (evaluated) | Ten live API responses are documented in `assignment_qa_results.md`; the manager-salary question correctly abstained. A separate five-question evaluation is in `assignment_extra_qa_results.md`. |
| Chat UI, loading/error states, source evidence, responsive layout | PASS (build); MANUAL DEVICE TEST | Frontend lint/build succeeds; physical phone/tablet behavior has not been tested. |
| Local-network access | PARTIAL | Configurable Vite proxy, API base, bind addresses, and CORS are documented; a separate device was not available for verification. |
| API validation, logging, errors, and health | PASS (tests/smoke checks) | Input validation, explicit API errors, startup health, and per-PDF extraction errors are present. |
| README, reflection, and audit documentation | PASS (draft); HUMAN INPUT NEEDED | README contains observed answers. `THINKING.md` includes an AI-generated critique and an AI-assisted journey draft; the applicant should rewrite the journey in their own voice before submission, as the assignment requests. |

## Architecture

- **Frontend:** React 19, TypeScript, Vite, Tailwind CSS, Radix UI/shadcn-style components.
- **Backend:** Python 3.11, FastAPI, Pydantic Settings, LangChain.
- **Document storage:** Supabase Storage bucket `files` is primary when both server-side Supabase settings are present. PDFs are synchronized from the bucket root to `storage/supabase_cache`; `storage/documents` is the local fallback. The service-role key is backend-only.
- **Extraction and retrieval:** PyMuPDF page extraction, recursive text splitting, local Sentence Transformers embeddings (hosted Hugging Face inference is optional), Qdrant local/cloud vector storage, and Gemini answer generation.
- **Relational database:** None used by this MVP.

## Verification performed

- Backend: `36 passed`; deprecation warnings originate from PyMuPDF/Supabase dependencies.
- Frontend: `npm run lint` and `npm run build` succeeded. Lint reports one existing React effect warning; build reports a JavaScript chunk-size warning.
- Python compile check and assignment evaluator: passed; the ten sample questions and five additional questions were sent to the running API. Some repeated requests returned HTTP 503.
- `pip check`: no broken requirements.
- The local API health endpoint returned `ok`; document storage reported `local`; all four manufacturing PDFs were ready. The sample corpus also includes `Sample_Questions.pdf`, which is excluded from retrieval.
- Supabase behavior: mocked only; no request was made using the configured service-role credential.

## Remaining before submission

1. Rewrite the first-draft user journey in the applicant's own words; the current text is an AI-assisted draft, not a claim that the applicant-authored-before-AI requirement was met.
2. Record the requested 2–3 minute screen demo, including two sample questions, an unanswerable question, and use from a real phone on the same Wi-Fi.
3. If an evaluation request returns HTTP 503, check provider availability and rerun that question before relying on its response.

## Known limitations

- Supabase synchronization and live service behavior are not verified against the user's bucket; this evaluation used the local source.
- Model requests intermittently returned HTTP 503. At least one differently worded spindle-runout question also returned an incorrect responsible role, demonstrating that paraphrase robustness is not yet reliable.
- Revision extraction is heuristic; unknown or ambiguous revisions require human review.
- No OCR exists for scanned PDFs.
- Retrieval thresholds need calibration on the approved corpus.
- There is no authentication/authorization, per-user document isolation, or user-specific audit trail. Conversation history uses browser local storage and is not shared across browsers/devices. Do not expose the development server publicly.
- A second phone/tablet was unavailable, so LAN access from another device and the requested recording remain unverified.
