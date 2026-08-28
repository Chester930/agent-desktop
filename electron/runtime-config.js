const DEFAULT_FRONTEND_URL = 'http://127.0.0.1:4200';
const DEFAULT_BACKEND_URL = 'http://127.0.0.1:8765';

function normalizeUrl(value, fallback) {
  if (!value) return fallback;

  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol)) return fallback;
    return url.toString().replace(/\/$/, '');
  } catch {
    return fallback;
  }
}

module.exports = {
  frontendUrl: normalizeUrl(process.env.AGENT_DESKTOP_FRONTEND_URL, DEFAULT_FRONTEND_URL),
  backendUrl: normalizeUrl(process.env.AGENT_DESKTOP_BACKEND_URL, DEFAULT_BACKEND_URL),
};
