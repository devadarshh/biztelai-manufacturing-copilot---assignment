import { useCallback, useEffect, useRef, useState } from 'react'
import { BrainCircuit, FileText, History, LoaderCircle, MessageSquarePlus, MessageSquareText, RefreshCw, SendHorizonal, ShieldCheck, Sparkles, UploadCloud, Wifi, WifiOff } from 'lucide-react'

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from './components/ui/accordion'
import { Alert } from './components/ui/alert'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from './components/ui/card'
import { Input } from './components/ui/input'
import { ScrollArea } from './components/ui/scroll-area'
import { Separator } from './components/ui/separator'
import { Skeleton } from './components/ui/skeleton'

const suggestedQuestions = [
  'What are the key safety requirements for machine operation?',
  'What maintenance checks are required before shift handover?',
  'Where do the documents describe quality control checkpoints?',
]
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL?.replace(/\/+$/, '') || '/api'
const CHAT_HISTORY_STORAGE_KEY = 'biztelai.chatHistory'

function apiUrl(path: string) {
  return `${API_BASE_URL}/${path.replace(/^\/+/, '')}`
}

function welcomeMessage(): ChatMessage {
  return {
    role: 'assistant',
    content:
      'Welcome to the BiztelAI Manufacturing Copilot. Ask about safety procedures, SOPs, maintenance tasks, or quality checks from the indexed PDF documents.',
  }
}

function isSavedChat(value: unknown): value is SavedChat {
  if (typeof value !== 'object' || value === null) return false
  if (
    !('id' in value) ||
    typeof value.id !== 'string' ||
    !('title' in value) ||
    typeof value.title !== 'string' ||
    !('created_at' in value) ||
    typeof value.created_at !== 'string' ||
    !('updated_at' in value) ||
    typeof value.updated_at !== 'string' ||
    !('messages' in value) ||
    !Array.isArray(value.messages)
  ) {
    return false
  }
  return value.messages.every(
    (message: unknown) =>
      typeof message === 'object' &&
      message !== null &&
      'role' in message &&
      (message.role === 'assistant' || message.role === 'user') &&
      'content' in message &&
      typeof message.content === 'string',
  )
}

function readChatHistory(): {
  chats: SavedChat[]
  currentChatId: string | null
  error: string | null
} {
  try {
    const stored = localStorage.getItem(CHAT_HISTORY_STORAGE_KEY)
    if (!stored) return { chats: [], currentChatId: null, error: null }

    const parsed: unknown = JSON.parse(stored)
    if (
      typeof parsed !== 'object' ||
      parsed === null ||
      !('chats' in parsed) ||
      !Array.isArray(parsed.chats) ||
      !parsed.chats.every(isSavedChat) ||
      !('currentChatId' in parsed) ||
      (parsed.currentChatId !== null && typeof parsed.currentChatId !== 'string')
    ) {
      throw new Error('Saved chat history has an invalid format.')
    }

    return {
      chats: parsed.chats as SavedChat[],
      currentChatId: parsed.currentChatId,
      error: null,
    }
  } catch (storageError) {
    return {
      chats: [],
      currentChatId: null,
      error: storageError instanceof Error
        ? `Could not read saved chats from this browser: ${storageError.message}`
        : 'Could not read saved chats from this browser.',
    }
  }
}

function persistChatHistory(chats: SavedChat[], currentChatId: string | null) {
  localStorage.setItem(
    CHAT_HISTORY_STORAGE_KEY,
    JSON.stringify({ chats, currentChatId }),
  )
}

function tryPersistChatHistory(chats: SavedChat[], currentChatId: string | null): string | null {
  try {
    persistChatHistory(chats, currentChatId)
    return null
  } catch (storageError) {
    return storageError instanceof Error
      ? `Could not save chat history in this browser: ${storageError.message}`
      : 'Could not save chat history in this browser.'
  }
}

type Source = {
  document: string
  page?: number | null
  excerpt: string
  score?: number | null
  revision?: string | null
  effective_date?: string | null
  revision_status?: string | null
  superseded_by?: string | null
  section?: string | null
}

type ChatMessage = {
  role: 'assistant' | 'user'
  content: string
  sources?: Source[]
  confidence?: string
}

type SavedChat = {
  id: string
  title: string
  created_at: string
  updated_at: string
  messages: ChatMessage[]
}

type DocumentInfo = {
  name: string
  size_bytes: number
  revision?: string | null
  effective_date?: string | null
  revision_status: 'current' | 'superseded' | 'not_yet_effective' | 'revision_unknown'
  superseded_by?: string | null
  processing_status?: 'ready' | 'error' | 'no_extractable_text'
  processing_error?: string | null
}

type HealthStatus = {
  status: 'ok' | 'degraded'
  backend: string
}

type StorageStatus = {
  status: 'supabase' | 'local' | 'fallback'
  bucket: string
  warning: string | null
}

type IngestResult = {
  pdf_count: number
  chunks_indexed: number
  errors: string[]
  detail?: string
}

type UploadResult = {
  status: 'queued' | 'completed'
  message: string
  document: string
  job_id?: string | null
  chunks_indexed?: number
  detail?: string
}

type IngestionJob = {
  status: 'queued' | 'started' | 'finished' | 'failed' | string
  result?: { chunks_indexed?: number }
  message?: string
  detail?: string
}

function revisionStatusLabel(status?: string | null) {
  switch (status) {
    case 'current':
      return 'Current'
    case 'superseded':
      return 'Superseded'
    case 'not_yet_effective':
      return 'Not yet effective'
    default:
      return 'Revision unknown'
  }
}

function revisionStatusClass(status?: string | null) {
  switch (status) {
    case 'current':
      return 'border-emerald-200 bg-emerald-50 text-emerald-700'
    case 'superseded':
      return 'border-amber-200 bg-amber-50 text-amber-800'
    case 'not_yet_effective':
      return 'border-violet-200 bg-violet-50 text-violet-700'
    default:
      return 'border-slate-200 bg-slate-100 text-slate-600'
  }
}

async function queryAssistant(question: string) {
  const response = await fetch(apiUrl('ask'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  })

  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: 'The assistant could not answer that request.' }))
    throw new Error(payload.detail || 'The assistant could not answer that request.')
  }

  return response.json() as Promise<{
    answer: string
    sources: Source[]
    confidence: string
  }>
}

function App() {
  const [question, setQuestion] = useState('')
  const [initialChatHistory] = useState(readChatHistory)
  const [chatId, setChatId] = useState<string | null>(initialChatHistory.currentChatId)
  const [savedChats, setSavedChats] = useState<SavedChat[]>(initialChatHistory.chats)
  const [chatHistoryError, setChatHistoryError] = useState<string | null>(initialChatHistory.error)
  const restoredChat = initialChatHistory.chats.find(
    (chat) => chat.id === initialChatHistory.currentChatId,
  )
  const [loading, setLoading] = useState(false)
  const [reindexing, setReindexing] = useState(false)
  const [uploading, setUploading] = useState(false)
  const uploadInputRef = useRef<HTMLInputElement>(null)
  const [documents, setDocuments] = useState<DocumentInfo[]>([])
  const [storageStatus, setStorageStatus] = useState<StorageStatus | null>(null)
  const [apiStatus, setApiStatus] = useState<'online' | 'degraded' | 'offline'>('offline')
  const [messages, setMessages] = useState<ChatMessage[]>(() =>
    restoredChat ? [welcomeMessage(), ...restoredChat.messages] : [welcomeMessage()],
  )
  const [error, setError] = useState<string | null>(initialChatHistory.error)
  const [notice, setNotice] = useState<string | null>(null)

  const fetchDocuments = useCallback(async () => {
    try {
      const [healthResponse, documentsResponse] = await Promise.all([
        fetch(apiUrl('health')),
        fetch(apiUrl('documents')),
      ])
      if (!healthResponse.ok || !documentsResponse.ok) {
        throw new Error('Unable to load indexed document list.')
      }
      const storageResponse = await fetch(apiUrl('storage'))
      if (!storageResponse.ok) {
        throw new Error('Unable to load document storage status.')
      }
      const health = (await healthResponse.json()) as HealthStatus
      const data = (await documentsResponse.json()) as DocumentInfo[]
      const storage = (await storageResponse.json()) as StorageStatus
      setDocuments(data)
      setStorageStatus(storage)
      setApiStatus(health.status === 'ok' ? 'online' : 'degraded')
      setError(null)
    } catch (requestError) {
      setApiStatus('offline')
      setError(requestError instanceof Error ? requestError.message : 'Unable to reach the backend API.')
    }
  }, [])

  useEffect(() => {
    void fetchDocuments()
  }, [fetchDocuments])

  const selectChat = (selectedChat: SavedChat) => {
    if (loading) return
    setChatId(selectedChat.id)
    setMessages([welcomeMessage(), ...selectedChat.messages])
    setError(null)
    setChatHistoryError(tryPersistChatHistory(savedChats, selectedChat.id))
  }

  const reindexDocuments = async () => {
    if (reindexing) return
    setReindexing(true)
    setError(null)
    setNotice(null)
    try {
      const response = await fetch(apiUrl('ingest'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reindex: true }),
      })
      const result = (await response.json()) as IngestResult
      if (!response.ok) {
        throw new Error(result.detail || 'Unable to re-index documents.')
      }
      setNotice(
        result.errors.length
          ? `Indexed ${result.pdf_count} PDFs and ${result.chunks_indexed} sections. ${result.errors.join(' ')}`
          : `Re-indexed ${result.pdf_count} PDFs and ${result.chunks_indexed} document sections.`,
      )
      await fetchDocuments()
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Unable to re-index documents.')
    } finally {
      setReindexing(false)
    }
  }

  const uploadDocument = async (file?: File) => {
    if (!file || uploading) return
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setError('Choose a PDF file to upload.')
      return
    }

    setUploading(true)
    setError(null)
    setNotice(`Uploading ${file.name}…`)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const response = await fetch(apiUrl('documents/upload'), {
        method: 'POST',
        body: formData,
      })
      const result = (await response.json()) as UploadResult
      if (!response.ok) {
        throw new Error(result.detail || 'Unable to upload this PDF.')
      }

      if (result.status === 'queued' && result.job_id) {
        setNotice(`${result.document} uploaded. Waiting for the indexing worker…`)
        for (let attempt = 0; attempt < 180; attempt += 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 2000))
          const jobResponse = await fetch(apiUrl(`jobs/${encodeURIComponent(result.job_id)}`))
          const job = (await jobResponse.json()) as IngestionJob
          if (!jobResponse.ok) {
            throw new Error(job.detail || 'Unable to check the document indexing status.')
          }
          if (job.status === 'finished') {
            const chunkCount = job.result?.chunks_indexed ?? 0
            setNotice(`${result.document} uploaded and indexed (${chunkCount} chunks).`)
            await fetchDocuments()
            return
          }
          if (job.status === 'failed') {
            throw new Error(job.message || `Indexing failed for ${result.document}.`)
          }
        }
        throw new Error(`Indexing is taking longer than expected for ${result.document}. Check the worker and retry later.`)
      }

      setNotice(`${result.document} uploaded and indexed (${result.chunks_indexed ?? 0} chunks).`)
      await fetchDocuments()
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'PDF upload failed.'
      setError(message)
      setNotice(null)
    } finally {
      setUploading(false)
      if (uploadInputRef.current) uploadInputRef.current.value = ''
    }
  }

  const handleSubmit = async (nextQuestion?: string) => {
    const trimmed = (nextQuestion ?? question).trim()
    if (!trimmed || loading) return

    const userMessage: ChatMessage = { role: 'user', content: trimmed }
    const assistantPlaceholder: ChatMessage = { role: 'assistant', content: '', confidence: 'low' }
    setMessages((prev) => [...prev, userMessage, assistantPlaceholder])
    setQuestion('')
    setLoading(true)
    setError(null)

    try {
      const activeChatId = chatId || crypto.randomUUID()
      const previousChat = savedChats.find((chat) => chat.id === activeChatId)
      const createdAt = previousChat?.created_at || new Date().toISOString()
      const draftChat: SavedChat = {
        id: activeChatId,
        title: previousChat?.title || trimmed.slice(0, 50) + (trimmed.length > 50 ? '...' : ''),
        created_at: createdAt,
        updated_at: new Date().toISOString(),
        messages: [...(previousChat?.messages || []), userMessage],
      }
      const draftChats = [
        draftChat,
        ...savedChats.filter((chat) => chat.id !== activeChatId),
      ]
      setChatId(activeChatId)
      setSavedChats(draftChats)
      setChatHistoryError(tryPersistChatHistory(draftChats, activeChatId))

      const result = await queryAssistant(trimmed)
      const assistantMessage: ChatMessage = {
        role: 'assistant',
        content: result.answer || 'No answer available.',
        sources: result.sources || [],
        confidence: result.confidence || 'low',
      }
      setMessages((prev) => [...prev.slice(0, -1), assistantMessage])
      const savedChat: SavedChat = {
        ...draftChat,
        updated_at: new Date().toISOString(),
        messages: [...draftChat.messages, assistantMessage],
      }
      const nextChats = [
        savedChat,
        ...draftChats.filter((chat) => chat.id !== activeChatId),
      ]
      setSavedChats(nextChats)
      setChatHistoryError(tryPersistChatHistory(nextChats, activeChatId))
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'The request failed unexpectedly.'
      setError(message)
      setMessages((prev) => {
        const next = [...prev]
        const lastAssistantIndex = next.length - 1
        next[lastAssistantIndex] = {
          role: 'assistant',
          content: message,
          sources: [],
          confidence: 'low',
        }
        return next
      })
    } finally {
      setLoading(false)
    }
  }

  const clearConversation = () => {
    setChatId(null)
    setMessages([welcomeMessage()])
    setError(null)
    setChatHistoryError(tryPersistChatHistory(savedChats, null))
  }

  return (
    <div className="min-h-screen text-slate-900">
      <header className="sticky top-0 z-10 border-b border-slate-200/80 bg-white/85 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-2 px-3 py-3 sm:gap-4 sm:px-6 lg:px-8">
          <div className="flex min-w-0 items-center gap-2 sm:gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-slate-950 text-white shadow-lg shadow-slate-900/15">
              <BrainCircuit className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs font-bold tracking-tight text-slate-950 sm:text-base">BiztelAI <span className="font-medium text-slate-500">Manufacturing Copilot</span></p>
              <p className="hidden text-xs text-slate-500 sm:block">Plant operations intelligence</p>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2 text-sm sm:gap-3">
            <Badge variant="outline" className={apiStatus === 'online' ? 'gap-1.5 border-emerald-200 bg-emerald-50 text-emerald-700' : apiStatus === 'degraded' ? 'gap-1.5 border-amber-200 bg-amber-50 text-amber-800' : 'gap-1.5 border-slate-200 bg-slate-50 text-slate-500'}>
              {apiStatus === 'offline' ? <WifiOff className="h-3.5 w-3.5" /> : <Wifi className="h-3.5 w-3.5" />}
              {apiStatus === 'online' ? 'Ready' : apiStatus === 'degraded' ? 'Needs setup' : 'Offline'}
            </Badge>
            <Badge variant="outline" className="hidden gap-1.5 border-slate-200 bg-white text-slate-600 sm:inline-flex">
              <FileText className="h-3.5 w-3.5" />
              {documents.length} PDFs
            </Badge>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-7xl px-3 pb-8 pt-5 sm:px-6 sm:pb-10 sm:pt-10 lg:px-8">
        <div className="mb-5 max-w-3xl sm:mb-7">
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-sky-200/80 bg-white/80 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-sky-800 shadow-sm">
            <Sparkles className="h-3.5 w-3.5" />
            Plant operations intelligence
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-950 sm:text-4xl">Make every procedure easier to find.</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600 sm:text-base">
            Get grounded answers from your indexed SOPs, maintenance manuals, and quality documents.
          </p>
        </div>

        <div className="grid min-w-0 grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,1.65fr)_minmax(280px,0.75fr)] lg:gap-6">
          <div className="min-w-0 space-y-4">
          <Card className="overflow-hidden rounded-2xl border-slate-200/80 shadow-xl shadow-slate-900/[0.04]">
            <CardHeader className="border-b border-slate-100 bg-white px-5 py-4 sm:px-6">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-sky-50 text-sky-700">
                    <MessageSquareText className="h-4 w-4" />
                  </div>
                  <div>
                    <CardTitle className="text-sm font-semibold text-slate-900">Operations Q&amp;A</CardTitle>
                    <p className="mt-1 text-xs text-slate-500">Answers supported by your plant documents</p>
                  </div>
                </div>
                <Button variant="ghost" size="sm" onClick={clearConversation} disabled={loading} className="text-slate-500 hover:bg-slate-100 hover:text-slate-900">
                  <MessageSquarePlus className="h-4 w-4" />
                  <span className="hidden sm:inline">New chat</span>
                </Button>
              </div>
            </CardHeader>

            <CardContent className="p-0">
              <ScrollArea className="h-[min(52vh,560px)] min-h-[280px] bg-slate-50/50 p-3 sm:h-[min(56vh,560px)] sm:min-h-[360px] sm:p-6">
                <div className="mx-auto min-w-0 max-w-3xl space-y-5">
                  {messages.map((message, index) => (
                    <div key={`${message.role}-${index}`} className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                      <div
                        className={`max-w-[90%] rounded-2xl border px-4 py-3.5 shadow-sm sm:max-w-[84%] ${
                          message.role === 'user'
                            ? 'border-slate-900 bg-slate-900 text-white'
                            : 'border-slate-200/80 bg-white text-slate-700'
                        }`}
                      >
                        {message.role === 'assistant' && (
                          <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-sky-700">BiztelAI assistant</p>
                        )}
                        <p className="whitespace-pre-wrap text-sm leading-6">{message.content || (loading && index === messages.length - 1 ? 'Thinking…' : '')}</p>

                        {message.role === 'assistant' && message.sources && message.sources.length > 0 && (
                          <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3">
                            <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Source evidence</p>
                            <Accordion type="single" collapsible className="w-full">
                              {message.sources.map((source, sourceIndex) => (
                                <AccordionItem key={`${source.document}-${source.page ?? sourceIndex}`} value={`${source.document}-${source.page ?? sourceIndex}`}>
                                  <AccordionTrigger className="text-left text-xs font-medium text-slate-700">
                                    <span className="min-w-0">
                                      {source.document} · Page {source.page ?? 'unknown'}
                                      {source.revision && <span className="ml-1 text-slate-500">· Rev {source.revision}</span>}
                                    </span>
                                  </AccordionTrigger>
                                  <AccordionContent className="text-xs leading-6 text-slate-600">
                                    <div className="mb-2 flex flex-wrap items-center gap-2">
                                      <Badge variant="outline" className={`text-[10px] ${revisionStatusClass(source.revision_status)}`}>
                                        {revisionStatusLabel(source.revision_status)}
                                      </Badge>
                                      {source.effective_date && <span className="text-[11px] text-slate-500">Effective {source.effective_date}</span>}
                                      {source.superseded_by && <span className="text-[11px] font-medium text-amber-800">Replaced by {source.superseded_by}</span>}
                                      {source.section && <span className="text-[11px] text-slate-500">Section: {source.section}</span>}
                                    </div>
                                    <p className="mb-2 italic text-slate-500">“{source.excerpt}”</p>
                                    <p className="text-[11px] uppercase tracking-wide text-sky-700">
                                      Source-backed verification
                                    </p>
                                  </AccordionContent>
                                </AccordionItem>
                              ))}
                            </Accordion>
                          </div>
                        )}
                      </div>
                    </div>
                  ))}

                  {loading && (
                    <div className="flex justify-start">
                      <div className="max-w-[88%] rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                        <div className="space-y-2">
                          <Skeleton className="h-4 w-32" />
                          <Skeleton className="h-4 w-72" />
                          <Skeleton className="h-4 w-52" />
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </ScrollArea>

              <div className="border-t border-slate-100 bg-white p-4 sm:p-5">
                <div className="mx-auto max-w-3xl">
                <p className="mb-2.5 text-[11px] font-semibold uppercase tracking-[0.13em] text-slate-400">Suggested questions</p>
                <div className="mb-4 flex flex-wrap gap-2">
                  {suggestedQuestions.map((suggested) => (
                    <Button
                      key={suggested}
                      type="button"
                      variant="secondary"
                      size="sm"
                      className="h-auto max-w-full whitespace-normal break-words rounded-full border border-slate-200 bg-white px-3 py-1.5 text-left text-xs leading-4 text-slate-600 shadow-sm hover:border-sky-200 hover:bg-sky-50 hover:text-sky-800"
                      onClick={() => void handleSubmit(suggested)}
                    >
                      {suggested}
                    </Button>
                  ))}
                </div>

                <div className="flex gap-2 rounded-xl border border-slate-200 bg-slate-50 p-1.5 shadow-inner shadow-slate-900/[0.02] focus-within:border-sky-300 focus-within:ring-4 focus-within:ring-sky-100">
                  <Input
                    className="h-10 min-w-0 flex-1 border-0 bg-transparent shadow-none focus-visible:ring-0"
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    placeholder="Ask about safety, procedures, maintenance, or quality..."
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        event.preventDefault()
                        void handleSubmit()
                      }
                    }}
                  />
                  <Button type="button" onClick={() => void handleSubmit()} disabled={loading} className="h-10 rounded-lg bg-slate-900 px-4 whitespace-nowrap shadow-sm hover:bg-slate-700">
                    {loading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <SendHorizonal className="h-4 w-4" />}
                    <span className="hidden sm:inline">Ask</span>
                  </Button>
                </div>
                <p className="mt-2 text-center text-[11px] text-slate-400">Responses are decision support. Always follow approved plant procedures.</p>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        <div className="min-w-0 space-y-4">
          <Card className="rounded-2xl border-slate-200/80 shadow-lg shadow-slate-900/[0.03]">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                <History className="h-4 w-4 text-sky-700" />
                Recent chats
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-1">
              {chatHistoryError && (
                <p className="mb-2 rounded-lg bg-rose-50 px-3 py-2 text-xs leading-5 text-rose-700">
                  {chatHistoryError}
                </p>
              )}
              {savedChats.length > 0 ? (
                <div className="max-h-64 space-y-1 overflow-y-auto">
                  {savedChats.map((chat) => (
                    <Button
                      key={chat.id}
                      type="button"
                      variant="ghost"
                      className={`h-auto w-full justify-start px-2 py-2 text-left ${
                        chatId === chat.id ? 'bg-sky-50 text-sky-900' : 'text-slate-700'
                      }`}
                      onClick={() => selectChat(chat)}
                      disabled={loading}
                      aria-pressed={chatId === chat.id}
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-xs font-medium">{chat.title || 'New chat'}</span>
                        <span className="mt-0.5 block text-[11px] text-slate-500">
                          {new Date(chat.updated_at).toLocaleString()}
                        </span>
                      </span>
                    </Button>
                  ))}
                </div>
              ) : (
                <p className="px-2 py-2 text-xs text-slate-500">
                  No saved conversations yet. Chats are stored in this browser only.
                </p>
              )}
            </CardContent>
          </Card>

          <Card className="overflow-hidden rounded-2xl border-slate-200/80 shadow-lg shadow-slate-900/[0.03]">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-50">
                  <ShieldCheck className="h-4 w-4 text-emerald-700" />
                </span>
                <span className="text-sm font-semibold">Built for safer decisions</span>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm leading-6 text-slate-600">
              <p>
                Use answers as a starting point, not a replacement for approved procedures or supervisor guidance.
              </p>
              <Separator />
              <p>
                Every response is grounded in your indexed SOPs, maintenance manuals, and quality documents.
              </p>
            </CardContent>
          </Card>

          <Card className="rounded-2xl border-slate-200/80 shadow-lg shadow-slate-900/[0.03]">
            <CardHeader>
              <CardTitle className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-2 text-sm font-semibold">
                  <FileText className="h-4 w-4 text-sky-700" />
                  Documents
                </span>
                <div className="flex items-center gap-2">
                  <Badge variant="secondary" className="border-0 bg-slate-100 text-slate-600">{documents.length}</Badge>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-8 px-2.5 text-xs"
                    onClick={() => void reindexDocuments()}
                    disabled={reindexing || apiStatus === 'offline'}
                    title="Re-index PDFs and update document revision status"
                  >
                    {reindexing ? <LoaderCircle className="h-3.5 w-3.5 animate-spin sm:mr-1.5" /> : <RefreshCw className="h-3.5 w-3.5 sm:mr-1.5" />}
                    <span className="hidden sm:inline">{reindexing ? 'Re-indexing' : 'Re-index'}</span>
                  </Button>
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <div className="flex items-center gap-3 rounded-xl border border-dashed border-slate-300 bg-slate-50/70 p-3">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white text-sky-700 shadow-sm">
                  {uploading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium text-slate-700">Add a PDF to the document library</p>
                  <p className="text-[11px] leading-5 text-slate-500">The file is stored and indexed before it is used for answers.</p>
                </div>
                <Input
                  ref={uploadInputRef}
                  type="file"
                  accept="application/pdf,.pdf"
                  className="hidden"
                  disabled={uploading || apiStatus === 'offline'}
                  onChange={(event) => void uploadDocument(event.target.files?.[0])}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={uploading || apiStatus === 'offline'}
                  onClick={() => uploadInputRef.current?.click()}
                >
                  {uploading ? 'Processing' : 'Upload PDF'}
                </Button>
              </div>
              {storageStatus && (
                <p className={`rounded-lg px-3 py-2 text-xs leading-5 ${storageStatus.status === 'fallback' ? 'border border-amber-200 bg-amber-50 text-amber-900' : 'bg-slate-50 text-slate-600'}`}>
                  {storageStatus.warning || (storageStatus.status === 'supabase'
                    ? `PDFs synced from Supabase Storage bucket “${storageStatus.bucket}”.`
                    : 'PDFs are being read from the local document folder.')}
                </p>
              )}
              <p className="text-xs leading-5 text-slate-500">PDFs are listed from the selected storage source. Click Re-index to add their contents to search; revision details are detected from cover text and filenames.</p>
              {notice && <p className="rounded-lg bg-emerald-50 px-3 py-2 text-xs leading-5 text-emerald-800">{notice}</p>}
              {documents.length > 0 ? (
                documents.map((document) => (
                  <div key={document.name} className="flex items-start gap-3 rounded-xl border border-slate-100 bg-slate-50/80 px-3 py-3 text-sm">
                    <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white text-sky-700 shadow-sm">
                      <FileText className="h-4 w-4" />
                    </span>
                    <div className="min-w-0">
                      <div className="break-all font-medium text-slate-700">{document.name}</div>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5">
                        <Badge variant="outline" className={`px-2 py-0 text-[10px] ${revisionStatusClass(document.revision_status)}`}>
                          {revisionStatusLabel(document.revision_status)}
                        </Badge>
                        {document.revision && <span className="text-[11px] text-slate-500">Rev {document.revision}</span>}
                      </div>
                      <div className="mt-1 text-xs text-slate-500">
                        {document.effective_date ? `Effective ${document.effective_date}` : `${(document.size_bytes / 1024).toFixed(1)} KB`}
                      </div>
                      {document.processing_status && document.processing_status !== 'ready' && (
                        <div className="mt-1 text-xs font-medium text-rose-700">
                          {document.processing_error || 'No text could be extracted from this PDF.'}
                        </div>
                      )}
                      {document.superseded_by && (
                        <div className="mt-1 break-all text-xs text-amber-800">Superseded by {document.superseded_by}</div>
                      )}
                    </div>
                  </div>
                ))
              ) : (
                <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50/70 px-4 py-6 text-center">
                  <FileText className="mx-auto h-6 w-6 text-slate-400" />
                  <p className="mt-2 text-sm font-medium text-slate-700">No documents yet</p>
                  <p className="mt-1 text-xs leading-5 text-slate-500">Ingest PDFs from the backend to enable document-backed answers.</p>
                </div>
              )}
            </CardContent>
          </Card>

          {error && (
            <Alert>
              <p className="font-medium">Request issue</p>
              <p>{error}</p>
            </Alert>
          )}
        </div>
        </div>
      </main>
    </div>
  )
}

export default App
