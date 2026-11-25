# Build and review a multi-document report

In this tutorial, two Agents extract facts from managed Markdown files in parallel. The workflow gathers the facts in source order, calls a fixed report-writing workflow, and gives you two rounds to review the report. You'll reopen the same run in Dashboard Chat and download the approved Markdown file.

Download the <a href="/examples/multi-document-report/multi-document-report.json" download>native workflow package</a>, <a href="/examples/multi-document-report/DOC-A.md" download>DOC-A</a>, and <a href="/examples/multi-document-report/DOC-B.md" download>DOC-B</a>.

## Configure a model and prepare the sources

1. In **Providers**, add an OpenAI-compatible chat model and enable its **Tool Use** modality. For a local model, set the API base to the local server’s `/v1` URL, enter the model name served by that endpoint, and use its required key. Select the model as the default for the workflow or map it during import.
2. This example uses built-in document reading and the `store_task_file` and `submit_task_result` tools. It needs no extra plugin. For workflows that use a plugin component, have the administrator install and enable the plugin in **Extensions → Plugins** before import, then verify the component version in the canvas. Asset packages carry dependency references. See [installed plugin components](./agent-team-workflows.md#installed-plugin-components).
3. In **More Features → Agent Teams → Assets → Workflows → New Workflow**, create a one-Agent workflow named **Prepare report source**. Select a tool-capable model and **Use structured ports**. Declare a required string input named `material` and connect it to the Agent's `material` port. Select the Agent as the result task. Declare required output fields `text` (string) and `files` (array of objects with a required string `file_id`).
4. Use this task prompt: “Save the supplied Markdown verbatim with `store_task_file`. Use the document ID plus .md as the filename. Call `submit_task_result` with a short text summary and a one-item files array whose file_id is the returned ID. Markdown: {material}”. Choose **Save version** and start the source workflow twice. Paste one of the following JSON objects into **Input values (JSON)** each time. The escaped newlines preserve the headings and paragraph breaks.

   ```json
   {"material":"# DOC-A\n\n## Survey results\n\nThe survey reached 37 households.\n"}
   ```

   ```json
   {"material":"# DOC-B\n\n## Planning target\n\nThe target is 40 households.\n"}
   ```

5. Open each completed run and preview its attachment. Verify that each heading and number is on the correct document. Keep each filename and exact artifact version handy for the report's managed file pickers.

## Import, test and publish

1. In **Assets → Workflows**, choose **Import asset package**. Select the downloaded JSON and choose **Validate and preview**. Map every requested execution profile and model to one on your instance, then install. The import includes **Write the sourced report** at its fixed dependency version.
2. Open **Multi-document field report**. The two **Read DOC** nodes feed separate **Extract DOC** Agents. Their required structured results contain `source`, `heading`, and integer `value`. The **Collect results in source order** node joins both results. Its array goes to the fixed child workflow. The review node can return feedback to the child for one revision.
3. To test a draft, use **Fixed versions → Restore to draft**. To check parsing first, select **Read DOC-A** and open **Test draft → Selected node only**. Choose the exact DOC-A version and file, start the test, and confirm in the workbench that the extracted text contains its heading and value. A **Full workflow** test uses both exact source attachments and creates its own approval request. Open that trial's workbench and approve its report or complete its bounded rework before checking the final result.
4. Choose **Save version** to publish your reviewed draft. Return to **Assets → Workflows → Start run**, confirm the fixed version shown, and select the same two exact attachments.

![Dashboard canvas showing the two source branches and fixed report workflow](/images/agent-team-report-canvas-en.png)

![Trial dialog for the exact DOC-A attachment](/images/agent-team-report-trial-en.png)

## Review, reopen and export

1. In the Bot run workbench, expand the tasks and check that both extraction results reached **Collect results in source order**. Open **Inbox & artifacts**, preview the first report, and choose **Return for revision** with feedback such as `Add a Source index to the attachment with headings for both documents.`
2. Open **Open in Chat** from the workbench. Wait for the second request and record its artifact ID and version. In a disposable instance, stop and restart AstrBot while this request is waiting. Sign in again if needed and reopen the run from the Chat sidebar. Confirm that the same request and exact report are still there.
3. Preview the second report and its attachment. Check DOC-A and DOC-B, their headings and values, and the new **Source index**. Approve this request, then choose **Download file · report.md** to save the Markdown attachment. **Export approved version** saves the approved artifact as JSON. **Open run workbench** returns to the same run and decision. On a narrow screen, use **Tasks**, **Execution**, and **Inbox & artifacts** to switch panels.
4. To share the workflow definition, use **Assets → Workflows → Export asset JSON**. The report file comes from the approved run. From the completed Chat run, **Run again** opens a new run with the original fixed version and inputs.

![The reopened Chat run with the approved version and report download](/images/agent-team-report-chat-approved-en.png)

![The approved report and download action in the narrow Chat view](/images/agent-team-report-chat-narrow-en.png)

## Debug a report that failed citation review

The screenshots use a controlled extension of the report example. Its historical run used **v3** of the report and citation review workflows. Round 1 checked the initial draft and received approval. Round 2 reused the draft and received a superseded but readable PDF through loop feedback. The frozen citation node rejected that exact reference. The original package above covers report preparation and approval. The delivery steps below assume your workflow has an external connector and an archive path.

1. In **Bot → Agent Teams → Runs → Needs attention**, open the failed root run. You can also open its run conversation from **Dashboard Chat**. Expand the execution graph. On the parent graph, choose round 2, select **Review report citations · v3**, and choose **Debug failed child call**. Check that the preview names the round-2 parent call, the child version, and the failed **Check citations** attempt.
2. In the child scope, select the `pdf` data line into **Check citations** to see the consumed file ID, artifact version, and attempt. Switch to the round-1 child scope to compare the successful read. On the line to **Export an optional appendix**, the inspector shows **Skipped · not read**. Expand **View upstream output** to inspect the draft that was available in that scope.
3. Start with **Original run** and the default single-node scope. In **Choose an input to edit**, select `pdf`, enter the authorized artifact ID and exact version, then select `citations.pdf`. Check that `draft` is **Reused**, `pdf` is **Replaced**, and the version is still v3. Start the fork and compare the original failure with the successful result.
4. Reopen the comparison after reloading. **Open parent call** returns to round 2 and **Open source attempt** returns to its child attempt. For a separate check of an edited workflow, open the preview again, choose **Current draft**, and verify the displayed revision before starting a single-node fork. The example's current draft explicitly accepts both known references under its revised output contract.

![The skipped appendix data line and the draft available upstream](/images/report-debug-skipped-en.png)

![The frozen v3 fork preview with the selected PDF and first draft](/images/report-debug-preview-en.png)

### Verify approval and delivery

1. Open the original failed attempt again and choose **Run downstream from this node**. Review the frozen scope and its inputs, then select the corrected PDF. Start the fork and open **Run workspace → Inbox & artifacts**. Review and approve the new exact artifact. The first-round approval is still recorded against its own artifact.
2. Open **External write preview** and verify the destination and parameters. To test the actual connector, choose **Execute real write** once and check the execution ID and receipt after reopening the branch. The example's isolated connector recorded one delivery and the archive record read its returned count of `78`.
3. For a rehearsal, start another downstream fork, approve that branch's artifact, and choose **Continue with test value** at the delivery preview. In the example, enter `{"record":{"count":192}}`, then choose **Submit simulated receipt**. Confirm from the archive record's input line that it read `192` with simulated provenance. The next archive write has its own preview and remains paused. The delivery ledger has no additional entry from the rehearsal.

![External write preview with the report destination and parameters](/images/report-debug-delivery-en.png)

The [run workbench guide](./agent-team-workflows.md#debug-a-historical-node-or-its-downstream-path) covers additional scopes, recovery, and read-only inspection of autonomous member attempts. To reproduce the controlled scenario from a checkout with the built Dashboard and Playwright Chromium, run `python -m tests.agent_teams.report_debug_browser --evidence data/report-debug-local` with an unused evidence directory.

## Previous entry points

| Previous entry | Current entry |
| --- | --- |
| Workflow canvas modal | **Assets → Workflows → Edit**, in the dedicated workflow editor |
| Independent runs / Run monitoring | **Runs → In progress**, **Needs attention**, or **History** |
| Approval only in run details | **Runs → Needs attention** or **Chat → the run → To-dos and artifacts** |
| Cancel from the Chat title | **Inbox & artifacts → Whole run controls** |
| Older message-platform link | From the original message conversation, send `/asset status <run ID>` to recover the restricted link |

See [Agent Teams assets and run workbench](./agent-team-workflows.md) for more on versioning, permissions and recovery.
