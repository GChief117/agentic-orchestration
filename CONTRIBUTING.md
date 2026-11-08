# Contributing

Contributions are welcome: counterexamples, reproducible failures, experiments, patterns, and implementation mappings.

For substantial changes, open an RFC issue first. Describe the problem, concrete scenario, proposed observable behavior, alternatives, compatibility impact, and how success can be tested. Small corrections can go directly to a pull request.

Distinguish research findings, engineering experience, and untested proposals. Link primary sources for external claims. Do not submit credentials, personal data, proprietary traces, or material you cannot license.

Before a pull request:

```sh
python3 tools/validate_plan.py examples/multi_goal.json
python3 -m unittest discover -s tests -v
```

Keep the specification, examples, validator, and tests consistent. A runtime feature described in prose must not be represented as implemented by the static validator.

The initial repository maintainer makes merge decisions; significant semantic changes should receive public RFC discussion. Recommendations can be revised or removed when evidence contradicts them. No certification, standards-body affiliation, or consensus is implied.

By contributing, you agree that your contributions are licensed under this repository's MIT license. Be respectful, critique ideas rather than people, and redact sensitive information from reports.
