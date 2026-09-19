/**
 * Typed API client for AgentDesk backend.
 * Uses generated OpenAPI types, attaches in-memory Bearer token,
 * maps backend ErrorEnvelope, and never leaks response bodies to console.
 */

import type {
  ActorResponse,
  ErrorEnvelope,
  LoginRequest,
  TicketAcceptedResponse,
  TicketCreateRequest,
  TicketDetailResponse,
  TicketSummaryResponse,
  TokenResponse,
  TraceEventResponse,
} from './types'

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId: string = '',
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export interface ApiClientLike {
  login(credentials: LoginRequest): Promise<TokenResponse>
  getCurrentActor(): Promise<ActorResponse>
  createTicket(data: TicketCreateRequest): Promise<TicketAcceptedResponse>
  listTickets(): Promise<TicketSummaryResponse[]>
  getTicket(ticketId: string, signal?: AbortSignal): Promise<TicketDetailResponse>
  getTicketTrace(ticketId: string): Promise<TraceEventResponse[]>
}

export class ApiClient implements ApiClientLike {
  private readonly baseUrl: string
  private readonly tokenGetter: () => string | null

  constructor(tokenGetter: () => string | null = () => null, baseUrl: string = '') {
    this.tokenGetter = tokenGetter
    this.baseUrl = baseUrl
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const url = `${this.baseUrl}${path}`
    const headers = new Headers(options.headers ?? {})

    if (!headers.has('Content-Type') && options.body) {
      headers.set('Content-Type', 'application/json')
    }

    const token = this.tokenGetter()
    if (token && !headers.has('Authorization')) {
      headers.set('Authorization', `Bearer ${token}`)
    }

    const response = await fetch(url, {
      ...options,
      headers,
    })

    if (!response.ok) {
      let code = 'HTTP_ERROR'
      let message = response.statusText || `Request failed with status ${response.status}`
      let requestId = ''

      try {
        const errorData = (await response.json()) as ErrorEnvelope
        if (errorData?.error) {
          code = errorData.error.code ?? code
          message = errorData.error.message ?? message
          requestId = errorData.error.request_id ?? ''
        }
      } catch {
        // Fall back to statusText if body is not JSON
      }

      throw new ApiError(response.status, code, message, requestId)
    }

    return (await response.json()) as T
  }

  async login(credentials: LoginRequest): Promise<TokenResponse> {
    return this.request<TokenResponse>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify(credentials),
    })
  }

  async getCurrentActor(): Promise<ActorResponse> {
    return this.request<ActorResponse>('/api/auth/me', {
      method: 'GET',
    })
  }

  async createTicket(data: TicketCreateRequest): Promise<TicketAcceptedResponse> {
    return this.request<TicketAcceptedResponse>('/api/tickets', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }

  async listTickets(): Promise<TicketSummaryResponse[]> {
    return this.request<TicketSummaryResponse[]>('/api/tickets', {
      method: 'GET',
    })
  }

  async getTicket(ticketId: string, signal?: AbortSignal): Promise<TicketDetailResponse> {
    return this.request<TicketDetailResponse>(`/api/tickets/${ticketId}`, {
      method: 'GET',
      signal,
    })
  }

  async getTicketTrace(ticketId: string): Promise<TraceEventResponse[]> {
    return this.request<TraceEventResponse[]>(`/api/tickets/${ticketId}/trace`, {
      method: 'GET',
    })
  }
}

let defaultClient: ApiClient | null = null

export function getApiClient(tokenGetter?: () => string | null): ApiClient {
  if (tokenGetter) {
    return new ApiClient(tokenGetter)
  }
  if (!defaultClient) {
    defaultClient = new ApiClient()
  }
  return defaultClient
}
