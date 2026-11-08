export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export class ApiClient {
  constructor({
    token,
    fetch: request = (...args) => globalThis.fetch(...args),
    baseUrl = "",
  }) {
    this.token = token;
    this.request = request;
    this.baseUrl = baseUrl;
  }

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

  state() {
    return this.send("/api/state");
  }
  createProject(project) {
    return this.send("/api/projects", project);
  }
  submitGoal(goal) {
    return this.send("/api/goals", goal);
  }
  cancelGoal(id) {
    return this.send("/api/cancel", { id });
  }
  artifact(taskId) {
    return this.send(`/api/artifact/${encodeURIComponent(taskId)}`);
  }
}
