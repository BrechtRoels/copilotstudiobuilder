# Agent capabilities: turn requirements into configuration

Written for Copilot Studio Builder (not part of Microsoft's pack). When the maker's request needs one of the
capabilities below, **configure it in the files** (every key here exists in the authoring schema). Put a step
in `notes` only when YAML cannot express it, and then say exactly where in Copilot Studio to do it.

## Users upload or attach documents and the agent must read them

1. Agent-wide switch, in `settings.mcs.yml` (this becomes the bot's `configuration.json` on export):
   ```yaml
   configuration:
     aISettings:
       isFileAnalysisEnabled: true
   ```
   Keep every other key in `settings.mcs.yml` as it is, and return the complete file. If the workspace has no
   `settings.mcs.yml` yet, create it with `GenerativeActionsEnabled: true` plus the block above.
2. Every `AnswerQuestionWithAI` or `SearchAndSummarizeContent` node that must work on the uploaded files needs
   `isFileAnalysisEnabled: true` on the node itself. Without it the node answers from the conversation text only.
3. In the instructions, refer to "the documents the user uploaded in this conversation", and say what to do when
   there are none (ask the user to upload them).

## Answer only from the documents or knowledge, never from general knowledge

- `settings.mcs.yml` → `configuration.aISettings.useModelKnowledge: false`.
- Back it up in the instructions (grounding rules), and in `additionalInstructions` on AI nodes.

## Search or research the internet

- **Specific sites** (regulators, product docs): add a knowledge source per site in `knowledge/` with
  `kind: PublicSiteSearchSource` (Bing search scoped to the URL, at most 2 path levels below the domain; it returns
  snippets, it does not read full pages). See the add-knowledge guide.
- **The open web**: `agent.mcs.yml` → `gptCapabilities: { webBrowsing: true }`. For a child agent, set it under
  `settings.gptCapabilities` in `agents/<Name>/agent.mcs.yml`.
- **Inside a topic**: `SearchAndSummarizeContent` with `webBrowsing: true`.
- Tell the agent in its instructions to cite the source URL for every web finding.

## Other capabilities (`gptCapabilities` in agent.mcs.yml)

| Requirement | Key |
|---|---|
| Calculations, charts, data analysis on uploaded spreadsheets | `codeInterpreter: true` |
| Generate images | `generateImages: true` |
| Search the user's Teams chats, emails, meetings, OneDrive/SharePoint, people (M365 Copilot licence) | `searchTeams`, `searchEmails`, `searchMeetings`, `searchOneDriveAndSharePoint`, `searchPeople` |

## Checklist before you answer

- Every capability the request implies is configured in a file, not only mentioned in `notes`.
- A global variable's `schemaName` (required) is `<agent schema name>.globalvariable.<Name>`, using the schema
  name from the workspace summary. Only when the summary has none, write the literal `<agentSchemaName>` prefix
  and add a note that the maker must set it before a VS Code push (solution export assigns its own names).
- Descriptions and instructions name only topics, tools and agents that exist after your change.
