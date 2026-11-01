import {
  accountSessionRevision,
  accountSessionSignal,
  assertCurrentAccount,
  expireAccountSession,
} from "../utils/accountSession";
import axios, {
  type AxiosError,
  type AxiosInstance,
  type InternalAxiosRequestConfig,
} from "axios";

const AUTH_HEADER = "Authorization";
const LOCALE_HEADER = "Accept-Language";

let configured = false;
let originalFetch: typeof window.fetch | null = null;

export const httpClient = axios;
export const apiV1Client = axios.create({ baseURL: "/api/v1" });

function getToken(): string | null {
  return localStorage.getItem("token");
}

function getLocale(): string | null {
  return localStorage.getItem("astrbot-locale");
}

function setAxiosHeader(
  headers: InternalAxiosRequestConfig["headers"],
  key: string,
  value: string,
) {
  if (typeof headers.set === "function") {
    headers.set(key, value);
    return;
  }
  headers[key] = value;
}

function attachAxiosHeaders(config: InternalAxiosRequestConfig) {
  // Dashboard credentials must not be attached to third-party requests.
  try {
    const requestUrl = new URL(axios.getUri(config), window.location.href);
    if (requestUrl.origin !== window.location.origin) return config;
  } catch {
    return config;
  }

  (config as any).accountRevision = accountSessionRevision.value;
  config.signal ??= accountSessionSignal();
  const token = getToken();
  if (token) {
    setAxiosHeader(config.headers, AUTH_HEADER, `Bearer ${token}`);
  }

  const locale = getLocale();
  if (locale) {
    setAxiosHeader(config.headers, LOCALE_HEADER, locale);
  }

  return config;
}

function normalizeAxiosError(error: AxiosError) {
  const revision = (error.config as any)?.accountRevision;
  if (revision !== undefined) assertCurrentAccount(revision);
  if (error.response?.status === 401) {
    let requestPath = "";
    try {
      const url = error.config?.url || "";
      const baseURL = error.config?.baseURL;
      const resolvedUrl =
        url && baseURL && !/^([a-z][a-z\d+\-.]*:)?\/\//i.test(url)
          ? `${baseURL.replace(/\/+$/, "")}/${url.replace(/^\/+/, "")}`
          : url;
      const requestUrl = new URL(resolvedUrl || "/", window.location.origin);
      if (requestUrl.origin === window.location.origin) {
        requestPath = requestUrl.pathname;
      }
    } catch {
      requestPath = "";
    }

    const isAuthChallenge =
      [
        "/api/auth/login",
        "/api/auth/setup",
        "/api/auth/setup-status",
        "/api/v1/auth/login",
        "/api/v1/auth/setup",
        "/api/v1/auth/setup-status",
      ].includes(requestPath) ||
      Boolean(
        (
          error.response.data as
            | { data?: { totp_required?: boolean } }
            | undefined
        )?.data?.totp_required,
      );

    if (requestPath.startsWith("/api/") && !isAuthChallenge) {
      expireAccountSession();
    }
  }

  if (error.response?.status === 429) {
    const data = error.response.data as { message?: string } | undefined;
    if (data?.message) {
      return Promise.reject(data.message);
    }
  }
  return Promise.reject(error);
}

function installAxiosInterceptors(instance: AxiosInstance) {
  instance.interceptors.request.use(attachAxiosHeaders);
  instance.interceptors.response.use((response) => {
    const revision = (response.config as any).accountRevision;
    if (revision !== undefined) assertCurrentAccount(revision);
    return response;
  }, normalizeAxiosError);
}

export async function fetchWithAuth(
  input: RequestInfo | URL,
  init?: RequestInit,
) {
  const revision = accountSessionRevision.value;
  const fetchImpl = originalFetch ?? window.fetch.bind(window);
  // The global fetch wrapper also handles public, cross-origin resources.
  try {
    const requestUrl = new URL(
      input instanceof Request ? input.url : input,
      window.location.href,
    );
    if (requestUrl.origin !== window.location.origin) {
      return fetchImpl(input, init);
    }
  } catch {
    return fetchImpl(input, init);
  }

  const token = getToken();
  const locale = getLocale();

  if (!token && !locale) {
    return fetchImpl(input, init);
  }

  const requestHeaders =
    typeof input !== "string" && "headers" in input
      ? (input as Request).headers
      : undefined;
  const headers = new Headers(init?.headers || requestHeaders);

  if (token && !headers.has(AUTH_HEADER)) {
    headers.set(AUTH_HEADER, `Bearer ${token}`);
  }
  if (locale && !headers.has(LOCALE_HEADER)) {
    headers.set(LOCALE_HEADER, locale);
  }

  const abort = new AbortController();
  const requestSignal =
    init?.signal ?? (input instanceof Request ? input.signal : undefined);
  for (const signal of [accountSessionSignal(), requestSignal]) {
    if (!signal) continue;
    if (signal.aborted) abort.abort();
    else signal.addEventListener("abort", () => abort.abort(), { once: true });
  }
  const response = await fetchImpl(input, {
    ...init,
    headers,
    signal: abort.signal,
  });
  assertCurrentAccount(revision);
  const path = new URL(
    input instanceof Request ? input.url : input,
    window.location.href,
  ).pathname;
  if (
    response.status === 401 &&
    path.startsWith("/api/") &&
    !/\/auth\/(login|setup|setup-status)$/.test(path)
  ) {
    expireAccountSession();
  }
  return response;
}

export function setupHttpClient() {
  if (configured) {
    return;
  }

  installAxiosInterceptors(axios);
  installAxiosInterceptors(apiV1Client);

  originalFetch = window.fetch.bind(window);
  window.fetch = fetchWithAuth;

  configured = true;
}
