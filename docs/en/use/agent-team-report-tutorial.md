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

## Previous entry points

| Previous entry | Current entry |
| --- | --- |
| Workflow canvas modal | **Assets → Workflows → Edit**, in the dedicated workflow editor |
| Independent runs / Run monitoring | **Runs → In progress**, **Needs attention**, or **History** |
| Approval only in run details | **Runs → Needs attention** or **Chat → the run → To-dos and artifacts** |
| Cancel from the Chat title | **Inbox & artifacts → Whole run controls** |
| Older message-platform link | From the original message conversation, send `/asset status <run ID>` to recover the restricted link |

See [Agent Teams assets and run workbench](./agent-team-workflows.md) for more on versioning, permissions and recovery.
