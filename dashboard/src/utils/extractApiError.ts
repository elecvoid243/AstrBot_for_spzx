// Pure helper for surfacing backend error reasons from API call failures.
//
// Backend contract: business/validation errors arrive as HTTP 400/409 with an
// `{ status: 'error', message, data? }` envelope body, so axios rejects with
// the response attached. The 429 rate-limit interceptor instead rejects with
// `data.message` as a bare string (see api/http.ts normalizeAxiosError).
// Callers use this in catch blocks so users see the backend reason instead of
// axios's generic "Request failed with status code N".

/** One structured validation entry carried by the error envelope (Plan 2). */
export interface ApiErrorField {
  path: string;
  message: string;
}

export interface ExtractedApiError {
  /** Best available human-readable reason; always a non-empty string. */
  message: string;
  /** Structured validation fields when the backend provided them, else []. */
  fields: ApiErrorField[];
}

/** Keep only well-formed `{ path, message }` entries from a fields payload. */
function asFields(value: unknown): ApiErrorField[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (field): field is ApiErrorField =>
      !!field &&
      typeof field === 'object' &&
      typeof field.path === 'string' &&
      typeof field.message === 'string',
  );
}

/**
 * Extract the user-facing reason and structured fields from an API failure.
 *
 * Resolution order: the axios error envelope message (`response.data.message`)
 * → plain-string rejection (the 429 case) → `Error.message` → the fallback.
 * The envelope's `response.data.data.fields` array is passed through whenever
 * present, regardless of which message branch resolved.
 *
 * Args:
 *   err: The value caught from an API call (axios error, string, Error, ...).
 *   fallback: Message used when nothing better can be resolved; also keeps the
 *     result non-empty for blank envelope/string messages.
 *
 * Returns:
 *   `{ message, fields }` with a non-empty message and `fields: []` default.
 */
export function extractApiError(err: unknown, fallback: string): ExtractedApiError {
  const data = (err as { response?: { data?: any } } | null | undefined)?.response?.data;
  const fields = asFields(data?.data?.fields);

  let message = '';
  if (data && typeof data.message === 'string' && data.message.trim()) {
    message = data.message;
  } else if (typeof err === 'string' && err.trim()) {
    message = err;
  } else if (err instanceof Error && err.message.trim()) {
    message = err.message;
  }

  return { message: message || fallback, fields };
}
