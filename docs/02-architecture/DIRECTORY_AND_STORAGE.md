# Runtime Directory and Storage Contract

Recommended production root:

```text
/opt/adforge/
├── app/
├── config/
├── data/
├── products/
├── campaigns/
├── assets/
├── exports/
├── browser-profiles/
├── logs/
├── backups/
└── temp/
```

## Campaign workspace

```text
campaigns/<campaign-id>/
├── brief/
├── truth/
├── strategy/
├── script/
├── storyboard/
├── asset-plan/
├── generated/
│   ├── images/
│   └── video/
├── app-capture/
├── audio/
│   ├── voice/
│   ├── music/
│   └── sfx/
├── edit/
├── renders/
│   ├── drafts/
│   └── final/
├── qc/
├── handoffs/
├── manifest.json
└── production-ledger.jsonl
```

## Retention
No automatic pruning in v1. Cleanup requires explicit user confirmation.
