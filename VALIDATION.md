# Local validation · 2026-11-07

## Passed

- 36 Python tests covering AWS adapter contracts, real temporary SQLite persistence, customer isolation, code-enforced return eligibility, bug-report follow-ups, canceled workflows, optional AI tool contracts and local HTTP security.
- JavaScript syntax check and Python syntax parsing.
- Chrome: real local order lookup; return eligibility check followed by a saved synthetic return request; three-turn bug reporting followed by a saved local ticket; retrieval of both records after a page reload.
- The UI displays the optional AI agent explicitly as not configured, with Send disabled. No model was installed or invoked.
- Both project views, example prompt filling, mode switching, connection details open/refresh/close, New chat and reload were exercised.
- Updated phone layout at 390 × 844: full-page screenshot reviewed; document width equals viewport width (390 px), with no horizontal overflow. The temporary viewport override was reset afterward.
- No browser console errors observed during this pass.
- Local interface served at `http://127.0.0.1:8765`, defaulting to the working rules-based local workflow. `ENABLE_LIVE_AWS=0` remains explicitly set.
- Generated local database is gitignored and is not part of the published source.

## Limits

- Local workflow responses are deterministic and explicitly labeled **not an AI model**. Local tools and persistence are real, but all orders/policies are synthetic and no external ticket, payment or return occurs.
- Optional AI adapter tests use a fake model transport, not live inference. Model installation, tool-selection quality and end-to-end AI output are unverified and deferred.
- AWS adapter tests use fake AWS clients. Live credentials, resource existence, model access, IAM, AWS writes and costs are unverified. No AWS resources were created, no AWS request ran, and no cloud deployment is included.
- The original course integration suites were not run. This independent local demonstration does not reproduce the full original cloud architecture.
- This validation does not claim public hosting. A repository publication is separate from a hosted demo.
