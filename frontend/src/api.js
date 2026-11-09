/**
 * An API failure that retains its HTTP status for UI handling.
 */
export class ApiError extends Error {
  /**
   * Create an error from a user-facing message and HTTP status code.
   * @param {string} message Explanation suitable for the interface.
   * @param {number} status HTTP response status.
   */
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Small JSON client shared by all frontend views. Credentials stay in memory.
 */
export class ApiClient {
  /**
   * Configure the local API connection and an optional injectable fetch function.
   * @param {object} options In-memory token, optional baseUrl, and fetch implementation.
   */
  constructor({
    token,
    fetch: request = (...args) => globalThis.fetch(...args),
    baseUrl = "",
  }) {
    this.token = token;
    this.request = request;
    this.baseUrl = baseUrl;
  }

  /**
   * Send a JSON request and normalize backend error responses.
   * Omitting body makes a GET; providing body makes a POST. No automatic retries.
   * @param {string} path Relative API route.
   * @param {object} [body] JSON-serializable request data.
   * @returns {Promise<object>} Decoded response; rejects for HTTP or network failures.
   */
  async send(path, body) {
    const response = await this.request(this.baseUrl + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        Authorization: `Bearer ${this.token}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      cache: "no-store",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let result;
    try {
      result = await response.json();
    } catch {
      throw new ApiError(
        "The backend returned an unreadable response.",
        response.status,
      );
    }
    if (!response.ok) {
      const message =
        response.status === 403
          ? "Your local session expired. Reload this page to reconnect."
          : result.error || "The request could not be completed.";
      throw new ApiError(message, response.status);
    }
    return result;
  }

  /** Suggest conventional local search folders without scanning them. */
  scanRoots() {
    return this.send("/api/projects/scan-roots");
  }
  /** Find project markers under a user-selected folder without executing its files. */
  scanProjects(root, includeRoot = false) {
    return this.send("/api/projects/scan", { root, include_root: includeRoot });
  }
  /** Register only the checked candidates from a server-issued discovery snapshot. */
  importProjects(scanId, candidateIds) {
    return this.send("/api/projects/import", {
      scan_id: scanId,
      candidate_ids: candidateIds,
    });
  }
  /** List selectable source-file metadata from one imported project. */
  projectFiles(projectId) {
    return this.send(`/api/projects/${encodeURIComponent(projectId)}/files`);
  }
  /** Ask a project's coordinator to propose tasks without executing the plan. */
  planGoal(projectId, goal) {
    return this.send(`/api/projects/${encodeURIComponent(projectId)}/plan`, goal);
  }
  /** List API connection readiness and cached model choices without secrets. */
  models() {
    return this.send("/api/models");
  }
  /** Verify a provider API key, or reload models for the current credential. */
  connectModel(provider, key) {
    return this.send("/api/models/connect", {
      provider,
      ...(key === undefined ? {} : { key }),
    });
  }
  /** Forget the running server's credential for a provider. */
  disconnectModel(provider) {
    return this.send("/api/models/disconnect", { provider });
  }
  /** List official provider tools and their reported authentication state. */
  providers() {
    return this.send("/api/providers");
  }
  /** Launch a selected provider’s own interactive sign-in on this computer. */
  signInProvider(provider) {
    return this.send("/api/providers/sign-in", { provider });
  }
  /**
   * Fetch current projects, agents, tasks, receipts, and capacity.
   * @returns {Promise<object>} Backend workspace snapshot.
   */
  state() {
    return this.send("/api/state");
  }
  /**
   * Persist a validated charter and agent roster.
   * @param {object} project Project definition produced by projectPayload.
   * @returns {Promise<{id: string}>} Newly created project identity.
   */
  createProject(project) {
    return this.send("/api/projects", project);
  }
  /** Save an idle project's edited roster while preserving stable agent ownership. */
  updateTeam(projectId, team) {
    return this.send(
      `/api/projects/${encodeURIComponent(projectId)}/team`,
      team,
    );
  }
  /**
   * Queue an explicitly assigned task graph in its project.
   * @param {object} goal Goal definition produced by goalPayload.
   * @returns {Promise<{id: string}>} Newly created goal identity.
   */
  submitGoal(goal) {
    return this.send("/api/goals", goal);
  }
  /**
   * Request cancellation while retaining already completed work.
   * @param {string} id Durable goal identity.
   * @returns {Promise<object>} Cancellation acknowledgment, not a rollback guarantee.
   */
  cancelGoal(id) {
    return this.send("/api/cancel", { id });
  }
  /**
   * Read the artifact referenced by a task receipt.
   * @param {string} taskId Durable task identity, encoded as one path segment.
   * @returns {Promise<{name: string, content: string}>} Artifact name and text.
   */
  artifact(taskId) {
    return this.send(`/api/artifact/${encodeURIComponent(taskId)}`);
  }
}
