const ADMIN_TOKEN_KEY = "admin_token";

export function getApiBaseUrl() {
  return process.env.NEXT_PUBLIC_API_BASE_URL || "https://nolimitz-backend-yfne.onrender.com";
}

export function saveAdminToken(token: string) {
  if (typeof window !== "undefined") {
    localStorage.setItem(ADMIN_TOKEN_KEY, token);
  }
}

export function getAdminToken() {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ADMIN_TOKEN_KEY);
}

export function removeAdminToken() {
  if (typeof window !== "undefined") {
    localStorage.removeItem(ADMIN_TOKEN_KEY);
  }
}