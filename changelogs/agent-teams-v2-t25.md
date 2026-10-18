# Agent Teams v2 default entry

- Versioned Assets and Runs are the default Agent Teams workspace. The previous team creation, member editor and execution workspace have been retired.
- The top legacy run notice lists work still owned by the original engine, including normal pauses. Users can let it finish, continue its original pause or explicitly confirm stopping it. New WebUI and chat launches wait until those owned runs are resolved.
- Legacy definitions remain available for explicit migration. Original runs and long-term member conversations remain readable in Runs → History. Ownerless unfinished records show interruption alongside their saved status and cannot be replayed. Retired legacy starts and ownerless resumes return HTTP 410.
- Entry mapping: Previous team workspace / Members / Workflow Editor → Migrate previous definitions → Assets → Teams / Workflows / Role templates; original completion controls → top legacy run notice; Previous History / member conversations → Runs → History.
- Updated English and Chinese instructions and actual screenshots cover the default entry, report revision and exact-version approval, cross-instance package installation, and ordinary chat invocation.

Existing executor recovery limits and unknown external-effect handling continue to apply. The QA report uses controlled external model responses and is test content.
