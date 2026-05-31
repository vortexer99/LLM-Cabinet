# Privacy Notice

> 简体中文版本：[PRIVACY.zh-CN.md](PRIVACY.zh-CN.md)

Last updated: 2026-05-31

LLM Cabinet is a **local desktop application**. This document explains what the app stores on your machine, and what data leaves your machine when you use the *"LLM metadata suggestion"* feature.

---

## 1. Data stored only on your machine

The following data lives **only on your local disk** and is never proactively uploaded by the app:

- **Project database**: `%APPDATA%/Fileman/fileman.db`
  - Project metadata (title, author, date, rating, source, tags, description, custom fields)
  - File records (paths, per-file notes, kind)
  - Tag relations, field definitions, user settings
  - LLM task history (request payloads, raw responses, parsed suggestions, token usage)
  - LLM API config — **including API keys**

- **Library directory**: `%APPDATA%/Fileman/library/` (or whatever you choose in settings)
  - File copies for projects in `copy` mode
  - Cover images captured from previews or pasted from the clipboard

- **Original file paths in `link` mode**
  - Only the path string is recorded. The app does not copy or move these files.
  - Deleting a project does **not** delete the original files.

**Important**: API keys are stored in the database as **plaintext JSON** (in the `settings` table under the `llm_config` key). This is so the app can issue requests immediately at startup; there is currently **no extra encryption**. Do not share the `fileman.db` file with others.

---

## 2. Network requests — only in these scenarios

The app makes **no network requests at startup**. HTTPS requests are made to the **third-party LLM providers you yourself configured** under the following conditions:

### 2.1 "🔌 Test Connection"
- Target: `{base_url}/models` (for OpenAI-compatible providers) or Gemini's `/models?key=…`
- Purpose: verify the API key and Base URL are reachable
- Sent payload: **auth header only** (Bearer token or URL query key); no project content is sent

### 2.2 "✨ LLM Metadata Suggestion"
Triggered from: right-click on a project, the ✨ button in the project edit dialog, or bulk operations in the task queue panel.

**Data sent to the selected LLM provider** includes, but is not limited to:
- The project's current metadata values (title, author, date, tags, description, and any field with "Visible" = ON)
- A **listing of every file in the project** (filename, kind, per-file note — but **not the file contents**)
- The **content** of the files you ticked as *reference files* in the dialog:
  - **PDF**: text from the first 6 pages (~12,000 chars max)
  - **xlsx / docx / csv / txt / code files**: extracted text (~8,000 chars max)
  - **Images**: raw bytes, base64-encoded (max 3 MB per image, max 4 images)
  - **Videos / other binaries**: filename only — content is not sent
- Your free-form note (if you wrote one)
- Field type descriptions and the prompt template (hardcoded text in the app)

**Not sent**:
- The contents of files you did **not** tick as reference
- API keys for providers other than the one you're calling
- Your OS / hardware information
- The full project database

### 2.3 The LLM provider you chose
The app supports four providers — **each one uses your own account and your own billing**:
- DeepSeek: `https://api.deepseek.com`
- OpenAI: `https://api.openai.com/v1`
- Google Gemini: `https://generativelanguage.googleapis.com/v1beta`
- xAI Grok: `https://api.x.ai/v1`

Once data leaves your machine it is governed by **that provider's own privacy policy and data retention rules**. The author of this app cannot control or revoke that data. Please read the provider's terms before use, and **avoid sending sensitive/confidential material as reference files**.

---

## 3. Your controls

- **Fully offline**: don't configure any LLM provider in Settings → the app will never make network requests
- **Per-field isolation**: in Settings → Fields, uncheck "LLM Suggest" on a field → that field will not be requested for suggestion (its current value is still sent as context)
- **Hidden fields**: in older versions, "Visible = No" excluded a field from LLM context entirely. The current version decouples them — "Visible" only controls the list view, and **all fields go into the prompt**. If you do not want a field's value sent, leave it blank or clear it before triggering LLM
- **Reference file granularity**: every trigger lets you tick which files to include
- **Target field granularity**: every trigger lets you also adjust the list of "fields you want suggestions for"
- **Clear API keys**: Settings → API → clear the API Key input. The app no longer holds the key
- **Wipe everything**: delete the `%APPDATA%/Fileman/` directory

---

## 4. What the app **does not** do

- Send data to any telemetry, analytics, or crash-reporting server
- "Phone home" via update checks
- Bundle ads, tracking SDKs, or third-party analytics
- Sync any data across devices
- Call any network service other than the LLM providers above

---

## 5. Known limitations and risks

- **Plaintext API keys**: as noted above. Recommendation: clear keys you don't need long-term; or encrypt the partition holding `%APPDATA%/Fileman/` at the OS level
- **LLM provider retention**: DeepSeek / OpenAI / Gemini / Grok each have their own data retention and training policies. If you tick sensitive files as reference, that content may be retained by the provider. If unsure, read the provider's privacy policy or use a "no-training" tier (such as OpenAI's zero-data-retention agreements or Gemini's paid tier)
- **Open and auditable**: you can always read the source under `app/llm/` to verify that the outbound requests match what this document describes

---

## 6. Feedback

If you find this document does not match the app's actual behavior, please open an issue.

---

## 7. Disclaimer

The maintainer will continue to support this software, but **assumes no responsibility for any file loss, data corruption, or other damages arising from its use** — including but not limited to abnormal usage, misoperation, system failures, third-party LLM service issues, or anomalies during database migration.

Important notes:

- **Keep regular backups of important data** — original files (in `link` mode), the `library/` directory (in `copy` mode), and the database file `fileman.db`
- LLM metadata suggestions may be inaccurate; always review them manually before applying
- When upgrading, the app automatically backs up the previous `fileman.db` as a `.bak` file, but this **does not replace your own backup strategy**

This software is provided "AS IS", without warranty of any kind, express or implied. See [MIT License](LICENSE) for details.
