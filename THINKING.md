# Reflection

## 1. Understand the user

The primary user is a shift supervisor coordinating operators, production targets, quality checks, and maintenance or safety escalations. They may need to resolve a machine alarm or confirm a procedure while standing on a noisy shop floor with a phone or tablet and limited time to search long PDFs. A wrong or outdated answer is more frustrating—and riskier—than being told that the system cannot answer. These are reasonable product assumptions; I did not interview a manufacturing supervisor.

## 2. User journey

**Draft for me to review and rewrite in my own voice:** the assignment asks for this first draft to be written by the applicant before AI critique. No applicant-authored draft was available for this pass, so this is an AI-assisted starting point rather than a claim that I completed that step as requested.

1. At shift handover, a supervisor opens the copilot on a phone and asks a direct question, such as what to do for an alarm or which inspection sample size to use.
2. The copilot searches the approved manuals and SOPs, then gives a short answer with the document, page, and revision status visible.
3. Before acting—especially on a safety or quality issue—the supervisor opens the source evidence and checks that it supports the answer and is the current procedure.
4. If the documents do not answer, the system should say so and direct the supervisor to the appropriate authorized person rather than guess.
5. If the app is unavailable, the PDF is unreadable, revisions conflict, or the Wi-Fi/model service fails, the supervisor uses the controlled source or existing escalation process instead of treating a missing response as approval.

## 3. AI challenge

I asked an AI assistant to challenge the journey from two skeptical perspectives: “Review this shop-floor copilot journey as a skeptical supervisor and product manager. Identify unsafe assumptions, likely failure cases, and one concrete change that would make the flow trustworthy.”

**Supervisor perspective:** “A citation is not enough if I cannot tell whether it is the current procedure or quickly check the exact step. If the answer is uncertain, I need a clear stop-and-escalate path—not a confident-sounding guess.”

**Product manager perspective:** “The journey assumes connectivity and a working model at the moment of need. What happens when Wi-Fi drops, a PDF is not indexed, or two revisions disagree? The fallback must be explicit, and the product must not turn a service error into an operational instruction.”

I agreed that a source name alone does not establish trust and that failures need a deliberate fallback. I would not treat an AI answer as authorization for safety-critical work; the controlled SOP and designated safety/maintenance roles remain authoritative. I made the source page and revision status part of the evidence flow, kept unsupported questions as abstentions, and documented that users must fall back to their approved procedure and escalation path when the app fails. This was an AI-generated critique, not a user interview.

## 4. The improvement you added, and why you chose it.

Revision awareness was prioritized because using an old quality or safety procedure as current guidance can misdirect shop-floor work. The ingestion path extracts explicit revision labels and effective dates from filenames and the first pages, groups likely document families, classifies unique current/superseded/future revisions, and stores the result in Qdrant payload metadata. Retrieval prefers current material, omits future-effective passages, and can surface relevant superseded material with an explicit replacement warning. The UI shows the same status in the document inventory and evidence panel. Ambiguous or tied revisions remain unknown rather than being guessed.

The improvement also validates that a model-generated source citation points to a document/page actually retrieved. If it does not, the service returns the standard not-found response instead of presenting the unsupported answer as grounded.

## 5. Final reflection

The project remains the React/Vite + FastAPI application. It extracts PDF pages, chunks text, uses local Sentence Transformers embeddings by default, retrieves from Qdrant, and generates answers with Gemini. Supabase Storage is optional for PDFs; this evaluation used the local `sample-docs/` source. Chat history stays in browser local storage and is not synchronized to a database or across devices.

The improvement I prioritized was revision-aware retrieval and evidence display: Rev04 of the incoming inspection SOP is current while Rev03 is marked superseded. The intent is to reduce the chance of relying on an old instruction, while still showing source evidence so a supervisor can verify the result. Other implementation decisions include correcting PDF page numbering, showing document-processing errors, replacing stale vectors when files change, bounding question length, and rejecting unverified citations.

The biggest challenge was verifying end-to-end behavior without overstating what a successful API response proves. The local service reported all four manufacturing PDFs ready, and the app answered the ten assignment questions, abstaining on the salary question. I also ran five additional questions; the daily production target question was correctly reported as missing. Repeated calls sometimes returned HTTP 503, so failures must be retried rather than presented as answers. One paraphrase of the spindle-runout question incorrectly named the operator instead of the maintenance technician; the assignment wording returned the correct role. That discrepancy shows that retrieved citations and model phrasing still need human verification.

Backend tests passed (36 tests). Frontend lint and build completed; lint reported an existing React effect warning and the build reported a large-chunk warning. The frontend and API responded locally, but a second phone/tablet was not available, so LAN use from another device has not been verified. A duplicate dev-server attempt also found port 5174 already occupied; I used the already-running local app instead. I have not made a screen recording or uploaded one.

Next I would test on the actual shop-floor devices and network, add OCR and document-control metadata from an authoritative source, tune retrieval using reviewed question/answer evidence, and add authentication and an audit trail. I would ask customers which manuals are authoritative, how revision approval is represented, who may upload or re-index documents, what the required offline fallback is, and what confidence/evidence they need before acting. Conversation history remains local to the browser profile; it does not follow a supervisor between devices.
