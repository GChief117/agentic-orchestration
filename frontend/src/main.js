import { ApiClient } from "./api.js";
import { mountApp } from "./app.js";

const token = document.querySelector(
  'meta[name="orchestration-session"]',
)?.content;
const api = new ApiClient({ token });
mountApp({ document, api });
