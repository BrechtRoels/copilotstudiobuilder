# Agent flows (Power Automate) for Copilot Studio — authoring guide

Not part of the vendored Microsoft pack. Written for this app, from the
Power Automate workflow definition language and the Copilot Studio file layout.
Treat the JSON shape as best effort: always review generated flows in the
Power Automate designer after applying changes.

## Files

```
workflows/<FlowName>/metadata.yaml   # flow metadata (CloudFlowDefinition-like)
workflows/<FlowName>/workflow.json   # Power Automate clientdata
actions/<FlowName>.mcs.yml           # (optional) TaskDialog that exposes the flow as a tool
```

### metadata.yaml

```yaml
displayName: Create Helpdesk Ticket
workflowId: 00000000-0000-0000-0000-000000000000   # keep the existing id when editing; new flows: a fresh GUID
triggerType: Copilot          # Copilot = "When an agent calls the flow"; Standard = any other trigger
connectionType: EmbeddedAndInvoker
isEnabled: true
inputType:
  properties:
    Title: { displayName: Title, type: String }
    Description: { displayName: Description, type: String }
outputType:
  properties:
    TicketId: { displayName: TicketId, type: String }
```

### workflow.json

```json
{
  "properties": {
    "connectionReferences": {
      "shared_office365": {
        "runtimeSource": "embedded",
        "connection": { "connectionReferenceLogicalName": "cr123_sharedoffice365_abc" },
        "api": { "name": "shared_office365" }
      }
    },
    "definition": {
      "$schema": "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#",
      "contentVersion": "1.0.0.0",
      "parameters": {
        "$connections": { "defaultValue": {}, "type": "Object" },
        "$authentication": { "defaultValue": {}, "type": "SecureObject" }
      },
      "triggers": {
        "manual": {
          "type": "Request",
          "kind": "Skills",
          "inputs": {
            "schema": {
              "type": "object",
              "properties": {
                "text": { "title": "Title", "type": "string", "x-ms-content-hint": "TEXT", "x-ms-dynamically-added": true },
                "text_1": { "title": "Description", "type": "string", "x-ms-content-hint": "TEXT", "x-ms-dynamically-added": true }
              },
              "required": ["text", "text_1"]
            }
          }
        }
      },
      "actions": {
        "Try": {
          "type": "Scope",
          "actions": {
            "Send_an_email": {
              "type": "OpenApiConnection",
              "inputs": {
                "host": { "apiId": "/providers/Microsoft.PowerApps/apis/shared_office365", "connectionName": "shared_office365", "operationId": "SendEmailV2" },
                "parameters": { "emailMessage/To": "helpdesk@contoso.com", "emailMessage/Subject": "@triggerBody()?['text']", "emailMessage/Body": "<p>@{triggerBody()?['text_1']}</p>" },
                "authentication": "@parameters('$authentication')"
              },
              "runAfter": {}
            }
          },
          "runAfter": {}
        },
        "Respond_to_the_agent": {
          "type": "Response",
          "kind": "Skills",
          "inputs": { "statusCode": 200, "body": { "ticketid": "@{guid()}" },
                      "schema": { "type": "object", "properties": { "ticketid": { "title": "TicketId", "type": "string", "x-ms-dynamically-added": true } } } },
          "runAfter": { "Try": ["Succeeded"] }
        },
        "Respond_on_failure": {
          "type": "Response",
          "kind": "Skills",
          "inputs": { "statusCode": 200, "body": { "ticketid": "ERROR" },
                      "schema": { "type": "object", "properties": { "ticketid": { "title": "TicketId", "type": "string", "x-ms-dynamically-added": true } } } },
          "runAfter": { "Try": ["Failed", "TimedOut"] }
        }
      }
    },
    "templateName": null
  },
  "schemaVersion": "1.0.0.0"
}
```

## Rules

- Agent-called flows: trigger `manual` with `"type": "Request", "kind": "Skills"`. They must end with a
  `"type": "Response", "kind": "Skills"` action ("Respond to the agent"). It must answer within 100 seconds,
  so slow work goes after the response or into a child flow.
- Input property keys follow the designer convention (`text`, `text_1`, `number`, `boolean`) with a `title`
  that matches the Copilot Studio input display name. Output keys are lower-case titles.
- `runAfter` keys must name sibling actions in the **same scope**. An empty `{}` means "first in scope".
- Error handling: wrap the work in a `Scope` called `Try`, then add actions with `runAfter: {"Try": ["Failed","TimedOut"]}`
  that respond with a clear error value. Never leave an agent flow without a failure response.
- Connector actions: `"type": "OpenApiConnection"` with `inputs.host.{apiId, connectionName, operationId}`.
  Every `connectionName` needs an entry in `properties.connectionReferences`. Connection reference logical
  names must already exist in the environment (see `connectionreferences.mcs.yml`). If unknown, use a clear
  placeholder like `<connection-reference-logical-name>` and say so in notes.
- Other common types: `Compose`, `InitializeVariable`, `SetVariable`, `If` (with `expression`, `actions`,
  `else.actions`), `Foreach` (with `foreach`, `actions`), `Switch` (with `cases`, `default`), `Http`, `ParseJson`, `Scope`, `Terminate`.
- Expressions use workflow definition language: `@triggerBody()?['text']`, `@{outputs('Compose')}`, `@body('Get_items')?['value']`.
- Action names use underscores instead of spaces (`Send_an_email`).

## Calling a flow from Copilot Studio

Option A: as a **tool** for generative orchestration (recommended). Create `actions/<FlowName>.mcs.yml`:

```yaml
kind: TaskDialog
modelDisplayName: Create helpdesk ticket
modelDescription: Creates an IT helpdesk ticket. Use when the user reports an IT problem and confirmed the details.
inputs:
  - kind: AutomaticTaskInput
    propertyName: Title
    description: Short title of the issue
  - kind: AutomaticTaskInput
    propertyName: Description
    description: Full description of the issue in the user's words
action:
  kind: InvokeFlowTaskAction
  flowId: <workflowId from metadata.yaml>
outputMode: All
```

Option B: from a **topic node**:

```yaml
- kind: InvokeFlowAction
  id: invokeFlow_k3J9sd
  flowId: <workflowId from metadata.yaml>
  input:
    binding:
      text: =Topic.Title
      text_1: =Topic.Description
  output:
    binding:
      ticketid: Topic.TicketId
```
