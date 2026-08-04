# ChatGPT Memory Profile Review

This starter profile contains only durable context useful for technical,
career, and Elise-development conversations.

It intentionally excludes:

- relationship history;
- medical or mental-health information;
- finances and debt;
- immigration and identity-document information;
- alcohol or substance history;
- exact location and contact information;
- private facts about other people;
- dating, family, and other highly personal history.

Before importing, open `profiles/chatgpt_memory_profile.json`.

For any item you do not want Elise to consider, change:

```json
"include": true
```

to:

```json
"include": false
```

The import command creates pending suggestions only. It does not create
confirmed memories. Review each result with:

```text
/memory-suggestions
/approve-memory <id>
/reject-memory <id>
```
