"""Canned, schema-valid responses for USE_MOCK_AI=true (UI work without tokens)."""
from __future__ import annotations

import json

from ..workspace.model import Workspace

AGENT = """\
mcs.metadata:
  componentName: IT Helpdesk
kind: GptComponentMetadata
displayName: IT Helpdesk
instructions: |
  You are the IT Helpdesk agent for Contoso employees.

  - Help with passwords, devices, software requests and outages.
  - Use the "Create helpdesk ticket" tool when the user reports a problem you cannot solve.
  - Route HR questions (leave, payroll, contracts) to the HR Specialist agent.
  - Be concise. Never ask for passwords.
conversationStarters:
  - title: Reset my password
    text: I forgot my password
  - title: Report an issue
    text: My laptop won't connect to the VPN
"""

RESET_TOPIC = """\
# Name: Reset Password
kind: AdaptiveDialog
modelDescription: Guides the user through a self-service password reset. Use when the user forgot their password or is locked out.
beginDialog:
  kind: OnRecognizedIntent
  id: main
  intent:
    displayName: Reset Password
    triggerQueries:
      - I forgot my password
      - reset my password
      - I am locked out
      - password expired
      - can't log in

  actions:
    - kind: Question
      id: question_Rp4kQ1
      variable: init:Topic.HasPhone
      prompt: Do you have access to the phone registered for MFA?
      entity: BooleanPrebuiltEntity

    - kind: ConditionGroup
      id: conditionGroup_Rp4kQ2
      conditions:
        - id: conditionItem_Rp4kQ3
          condition: =Topic.HasPhone = true
          actions:
            - kind: SendActivity
              id: sendActivity_Rp4kQ4
              activity: Go to https://aka.ms/sspr and follow the steps. You'll get a code on your phone.

      elseActions:
        - kind: SendActivity
          id: sendActivity_Rp4kQ5
          activity: No problem, I'll create a ticket so the service desk can verify your identity.

        - kind: BeginDialog
          id: beginDialog_Rp4kQ6
          dialog: contoso_itHelpdesk.topic.Escalate
"""

ESCALATE_TOPIC = """\
# Name: Escalate
kind: AdaptiveDialog
beginDialog:
  kind: OnEscalate
  id: main
  actions:
    - kind: SendActivity
      id: sendActivity_Es7aB1
      activity: I'm connecting you with the service desk. Expect a reply within 4 working hours.

    - kind: EndDialog
      id: endDialog_Es7aB2
"""

HR_CHILD = """\
kind: AgentDialog
beginDialog:
  kind: OnToolSelected
  id: main
  description: Handles HR questions about leave, payroll, contracts and benefits. Example messages - "How many vacation days do I have left?", "When is payday?"

settings:
  instructions: |
    You are the HR specialist. Only answer HR questions about leave, payroll, contracts and benefits.
    If a question is outside HR, say you only handle HR topics.

inputType: {}
outputType: {}
"""

FLOW_META = """\
displayName: Create Helpdesk Ticket
workflowId: 6f1c2a9e-0d1b-4c55-9a51-1d2a3b4c5d6e
triggerType: Copilot
connectionType: EmbeddedAndInvoker
isEnabled: true
inputType:
  properties:
    Title:
      displayName: Title
      type: String
    Description:
      displayName: Description
      type: String
outputType:
  properties:
    TicketId:
      displayName: TicketId
      type: String
"""

FLOW_JSON = {
    "properties": {
        "connectionReferences": {
            "shared_office365": {
                "runtimeSource": "embedded",
                "connection": {"connectionReferenceLogicalName": "<connection-reference-logical-name>"},
                "api": {"name": "shared_office365"},
            }
        },
        "definition": {
            "$schema": "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#",
            "contentVersion": "1.0.0.0",
            "parameters": {"$connections": {"defaultValue": {}, "type": "Object"},
                           "$authentication": {"defaultValue": {}, "type": "SecureObject"}},
            "triggers": {"manual": {"type": "Request", "kind": "Skills", "inputs": {"schema": {
                "type": "object",
                "properties": {"text": {"title": "Title", "type": "string"}, "text_1": {"title": "Description", "type": "string"}},
                "required": ["text", "text_1"]}}}},
            "actions": {
                "Try": {"type": "Scope", "runAfter": {}, "actions": {
                    "Ticket_id": {"type": "Compose", "inputs": "@{guid()}", "runAfter": {}},
                    "Send_an_email": {"type": "OpenApiConnection", "runAfter": {"Ticket_id": ["Succeeded"]}, "inputs": {
                        "host": {"apiId": "/providers/Microsoft.PowerApps/apis/shared_office365",
                                 "connectionName": "shared_office365", "operationId": "SendEmailV2"},
                        "parameters": {"emailMessage/To": "servicedesk@contoso.com",
                                       "emailMessage/Subject": "[@{outputs('Ticket_id')}] @{triggerBody()?['text']}",
                                       "emailMessage/Body": "<p>@{triggerBody()?['text_1']}</p>"},
                        "authentication": "@parameters('$authentication')"}},
                }},
                "Respond_to_the_agent": {"type": "Response", "kind": "Skills", "runAfter": {"Try": ["Succeeded"]},
                                         "inputs": {"statusCode": 200, "body": {"ticketid": "@{outputs('Ticket_id')}"}}},
                "Respond_on_failure": {"type": "Response", "kind": "Skills", "runAfter": {"Try": ["Failed", "TimedOut"]},
                                       "inputs": {"statusCode": 200, "body": {"ticketid": "ERROR"}}},
            },
        },
    },
    "schemaVersion": "1.0.0.0",
}

FLOW_TOOL = """\
kind: TaskDialog
modelDisplayName: Create helpdesk ticket
modelDescription: Creates an IT helpdesk ticket and emails the service desk. Use when the user reports an IT problem you cannot solve and has described it.
inputs:
  - kind: AutomaticTaskInput
    propertyName: Title
    description: Short title of the issue

  - kind: AutomaticTaskInput
    propertyName: Description
    description: Full description of the issue in the user's own words

action:
  kind: InvokeFlowTaskAction
  flowId: 6f1c2a9e-0d1b-4c55-9a51-1d2a3b4c5d6e

outputMode: All
"""


def _f(path: str, content: str) -> dict:
    return {"path": path, "content": content}


def respond(mode: str, prompt: str, target: str | None, ws: Workspace) -> dict:
    flow_files = [_f("workflows/CreateHelpdeskTicket/metadata.yaml", FLOW_META),
                  _f("workflows/CreateHelpdeskTicket/workflow.json", json.dumps(FLOW_JSON, indent=2)),
                  _f("actions/CreateHelpdeskTicket.mcs.yml", FLOW_TOOL)]
    if mode == "agent":
        return {"summary": "[mock] IT Helpdesk orchestrator with a password-reset topic, escalation, an HR child agent "
                           "and a ticket flow exposed as a tool.",
                "notes": ["Create an Office 365 Outlook connection reference and put its logical name in workflow.json.",
                          "Check the topic schema prefix 'contoso_itHelpdesk' matches your agent."],
                "files": [_f("agent.mcs.yml", AGENT), _f("topics/ResetPassword.mcs.yml", RESET_TOPIC),
                          _f("topics/Escalate.mcs.yml", ESCALATE_TOPIC),
                          _f("agents/HRSpecialist/agent.mcs.yml", HR_CHILD), *flow_files]}
    if mode == "topic":
        return {"summary": "[mock] Password reset topic with a branch and escalation.", "notes": [],
                "files": [_f("topics/ResetPassword.mcs.yml", RESET_TOPIC)]}
    if mode == "flow":
        return {"summary": "[mock] Agent flow with try/catch and a TaskDialog tool.",
                "notes": ["Map the connection reference."], "files": flow_files}
    if mode == "childAgent":
        return {"summary": "[mock] HR child agent.", "notes": [], "files": [_f("agents/HRSpecialist/agent.mcs.yml", HR_CHILD)]}
    if mode in ("modify", "fix") and target:
        comp = ws.get(target)
        if comp and comp.category != "workflow":
            return {"summary": f"[mock] No real change made to {comp.path} (mock mode).", "notes": [],
                    "files": [_f(comp.path, comp.text.rstrip() + "\n# reviewed by Copilot Studio Builder (mock)\n")]}
    return {"summary": "**[mock]** Set `USE_MOCK_AI=false` and add your `GENAI_API_KEY` to get real answers.",
            "notes": [], "files": []}
