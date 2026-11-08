# JavaScript project workspace

Plain JavaScript ES modules and CSS, served by the existing Python application. The frontend has no runtime package dependencies or build step.

From the repository root:

```sh
python3 app.py --execution-limit 4 --port 8765
```

Open `http://127.0.0.1:8765`. Create a project, define its charter and agent roster, then create a goal with assigned tasks. The example buttons fill editable starter definitions; nothing executes until the form is submitted.

Agent counts describe assigned team members. Active execution counts come from the backend. Model providers and remote execution adapters are still unconnected; the available actions are the backend's real local file and runtime operations.

## Modules

- `src/api.js`: HTTP requests and session error handling.
- `src/domain.js`: payload construction, client-side checks, starter definitions, and display data.
- `src/forms.js`: project and goal editors and execution evidence.
- `src/views.js`: escaped workspace, project, team, and task markup.
- `src/app.js`: navigation, state refresh, and user actions.
- `src/main.js`: application entry point.

The local session credential comes from the same-origin HTML shell and stays in memory. It is never written to local storage, session storage, URLs, or source files. The server retains the authorization checks; client-side validation is only a usability aid.

## Testing

```sh
npm ci --prefix frontend
npm run check --prefix frontend
npm test --prefix frontend
```

Tests use Node and jsdom. The integration test starts a real Python backend and drives DOM events through project creation, task execution, and artifact inspection. It does not launch a browser or replace visual browser QA. `node_modules/` and private runtime data are ignored by Git.
