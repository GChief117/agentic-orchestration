/**
 * Bootstrap the workspace from the same-origin, in-memory session credential.
 * This module mounts the frontend once; it does not persist the credential.
 */
import { ApiClient } from "./api.js";
import { mountApp } from "./app.js";

const token = document.querySelector(
  'meta[name="orchestration-session"]',
)?.content;
const api = new ApiClient({ token });
mountApp({ document, api });
