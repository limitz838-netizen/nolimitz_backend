const ADMIN_TOKEN_KEY = "admin_token";

export function getApiBaseUrl() {
  return (
    process.env.NEXT_PUBLIC_API_BASE_URL ||
    "https://nolimitz-backend-yfne.onrender.com"
  );
}

export function getAdminToken() {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ADMIN_TOKEN_KEY);
}

function buildHeaders(isJson: boolean = true): HeadersInit {
  const token = getAdminToken();

  return {
    ...(isJson ? { "Content-Type": "application/json" } : {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function parseResponse(res: Response) {
  const text = await res.text();

  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return text;
  }
}

export async function apiGet(path: string) {
  const res = await fetch(`${getApiBaseUrl()}${path}`, {
    method: "GET",
    headers: buildHeaders(false),
  });

  const data = await parseResponse(res);

  if (!res.ok) {
    throw new Error(
      typeof data === "object" && data?.detail ? data.detail : "Request failed"
    );
  }

  return data;
}

export async function apiPost(path: string, body?: unknown) {
  const res = await fetch(`${getApiBaseUrl()}${path}`, {
    method: "POST",
    headers: buildHeaders(true),
    body: body ? JSON.stringify(body) : undefined,
  });

  const data = await parseResponse(res);

  if (!res.ok) {
    throw new Error(
      typeof data === "object" && data?.detail ? data.detail : "Request failed"
    );
  }

  return data;
}

export async function apiPut(path: string, body?: unknown) {
  const res = await fetch(`${getApiBaseUrl()}${path}`, {
    method: "PUT",
    headers: buildHeaders(true),
    body: body ? JSON.stringify(body) : undefined,
  });

  const data = await parseResponse(res);

  if (!res.ok) {
    throw new Error(
      typeof data === "object" && data?.detail ? data.detail : "Request failed"
    );
  }

  return data;
}