# JavaScript project workspace

Plain JavaScript ES modules and CSS, served by the existing Python application. The frontend has no runtime package dependencies or build step.

From the repository root:

```sh
python3 app.py --execution-limit 4 --port 8765
```

Open `http://127.0.0.1:8765`. Create a project through three steps: describe the goal, choose the team, and review the organization chart. Project-type suggestions fill editable scope and success criteria. Role dropdowns fill editable instructions and permissions; internal keys are generated automatically. The chart is the main team editor: click a card for its inspector, add a role under the selected node, drag the actual role card onto its new manager (invalid drops return it to its marked starting position), and use Fit team or zoom controls to see larger organizations. The same editor opens from saved project charts. Nothing executes merely by creating a project.

The optional Dictate button appears when the browser exposes a speech-recognition interface. Microphone access starts only after the user clicks it. Recognition is handled by the browser’s speech service; this app stores no audio. Unsupported browsers retain normal text entry and can use device keyboard dictation. Speech results remain editable before saving.

Agent counts describe assigned team members. Active execution counts come from the backend. Model providers and remote execution adapters are still unconnected; the available actions are the backend's real local file and runtime operations.

## Modules

- `src/api.js`: HTTP requests and session error handling.
- `src/domain.js`: payload construction, client-side checks, starter definitions, and display data.
- `src/form-controls.js`: shared dialog and field controls.
- `src/project-form.js`: guided project setup and editable team organization.
- `src/presets.js`: role and project suggestions.
- `src/organization.js`: reporting-tree validation, SVG layout, and chart markup.
- `src/organization-editor.js`: chart selection, inspector edits, drag reparenting, pan, zoom, and fit.
- `src/team-form.js`: explicit saving of organization changes for existing projects.
- `src/speech.js`: opt-in browser dictation and cleanup.
- `src/forms.js`: goal editor and execution evidence.
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

## CSS organization

`styles.css` is the stylesheet entry point. Its imports preserve the original cascade in this order:

1. `styles/base.css`: theme tokens, reset, focus, and shared defaults.
2. `styles/layout.css`: application shell, navigation, headings, and buttons.
3. `styles/projects.css`: metrics, project cards, and empty states.
4. `styles/work.css`: charters, teams, tasks, status badges, and activity.
5. `styles/forms.css`: feedback, dialogs, fields, and evidence panels.
6. `styles/organization.css`: shared organization chart and reporting connectors.
7. `styles/project-setup.css`: guided steps, role suggestions, and review.
8. `styles/responsive.css`: shared breakpoints and reduced-motion overrides.

Keep responsive overrides last. Add component-specific rules to their owning file instead of growing the entry point. Function references live beside the JavaScript implementations as JSDoc; Python functions use docstrings.

Saved teams can be edited only when their project has no queued or running work. Agent IDs remain stable across edits. Agents with recorded tasks cannot be removed, so their historical ownership remains resolvable; unused agents can be added or removed.
