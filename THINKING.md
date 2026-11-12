# Reflection

## 1. Understand the user.

The intended users are manufacturing supervisors and authorized shop-floor staff who need to locate approved operating, quality, maintenance, and safety information quickly. A reasonable design assumption is that they may be using a phone or tablet, working under time pressure, and need concise answers with a source they can verify. These are product assumptions, not findings from user interviews.

## 2. User journey.

An operator or supervisor opens the responsive copilot, asks a procedure question in plain language, and receives a concise response backed by retrieved PDF excerpts. The response exposes document, page, section when present, and revision status. A supervisor can inspect the evidence, check whether a revision is current or superseded, and use the indexed-document panel to confirm extraction status. After adding or replacing approved PDFs, an authorized user re-indexes the library.

## 3. AI challenge.

**No actual critique conversation with an AI reviewer or end user was provided in the material available for this implementation. Complete this section with the real critique and response before submission; do not present a hypothetical exchange as one that occurred.**

The engineering challenge observed during code inspection was safely distinguishing current instructions from superseded or future-effective ones while not pretending that ambiguous metadata is authoritative. The implementation uses explicit revision/date evidence, conservative unknown states, current-revision retrieval priority, and citation validation. This is a technical design response and is not a recorded critique conversation.

## 4. The improvement you added, and why you chose it.

Revision awareness was prioritized because using an old quality or safety procedure as current guidance can misdirect shop-floor work. The ingestion path extracts explicit revision labels and effective dates from filenames and the first pages, groups likely document families, classifies unique current/superseded/future revisions, and stores the result in Qdrant payload metadata. Retrieval prefers current material, omits future-effective passages, and can surface relevant superseded material with an explicit replacement warning. The UI shows the same status in the document inventory and evidence panel. Ambiguous or tied revisions remain unknown rather than being guessed.

The improvement also validates that a model-generated source citation points to a document/page actually retrieved. If it does not, the service returns the standard not-found response instead of presenting the unsupported answer as grounded.

## 5. Final reflection.

The project remains the original React/Vite + FastAPI application. The backend performs PDF page extraction, recursive chunking, local Sentence Transformers embeddings by default (hosted Hugging Face inference is optional), Qdrant vector persistence/retrieval, and Gemini answer generation. Supabase Storage is the primary PDF source when configured, with a private local cache and a local-folder fallback. Qdrant can run locally or as a cloud service. The browser uses a same-origin API path by default, with configurable Vite proxy and direct API base options for LAN deployments.

Important decisions include keeping the existing architecture, declaring the missing PyMuPDF runtime dependency, correcting zero-based PDF page metadata to human page numbers, making document-processing errors visible, replacing stale vectors when a file changes, bounding question input, setting a model request timeout, and rejecting unverified citations. An empty or unextractable directory no longer erases a previously populated index during re-index.

The four assigned manufacturing PDFs and a sample-question PDF were found in `sample-docs/`; local extraction produced eight pages. They were not ingested into the configured runtime, and no live Supabase, model, embedding, or Qdrant request was made. Unit tests use generated PDFs and mocked model/vector/storage services where appropriate; these validate code paths but do not establish answer correctness against the assignment corpus. The development app has not been tested from a separate phone/tablet. Before submission, add the approved PDFs to the configured source, confirm that model and Qdrant services are ready, re-index, run the ten-question API evaluator, review every citation and revision classification, and test the LAN flow on a real device.

Future improvements include OCR for scanned documents, authoritative document identifiers and revision manifests instead of filename heuristics, calibrated retrieval thresholds, administrator-reviewed supersession metadata, authenticated upload/ingestion, persistent conversation history, and end-to-end evaluation fixtures derived from approved source pages.
