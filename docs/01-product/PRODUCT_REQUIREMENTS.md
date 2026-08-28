# Product Requirements Document — AdForge v1

## Problem
Creating a strong AI-assisted app advertisement currently requires manual coordination among ideation, prompt writing, generative video, authentic app capture, voice/music, editing, QC, retries, and export.

## Product promise
A user supplies a product and a concise campaign brief. AdForge performs the production workflow and returns a finished advertisement.

## Primary user
One authenticated project owner.

## Primary v1 user story
> As the owner of Warranty Vault, I can enter a campaign objective and have AdForge autonomously produce a finished 20-second vertical social advertisement using truthful product information, AI-generated cinematic footage, authentic app UI capture, autonomous audio production, deterministic editing, QC, and final export.

## Functional requirements
- Product registry and Product Truth snapshots
- Campaign creation
- One active campaign queue
- Persistent state machine
- AI task routing
- Asset planning and manifest
- AI image/video generation
- Android capture
- Audio production
- Edit specification
- FFmpeg render
- QC/repair
- Export and downloadable artifacts
- Production ledger
- Failure escalation
- External handoff workflows
- Resume after restart

## UX minimum
Desktop pages:
- Dashboard
- Products
- Product detail / truth status
- New Campaign
- Campaign queue
- Campaign detail / timeline
- Asset browser
- Ledger
- Outputs
- Settings / provider health

## Default Android ad practice
- Primary master: 9:16
- Default duration: 20 seconds
- Common structure: Hook → Pain → Product reveal → Authentic proof → Benefit → CTA
- 15-second performance derivative
- 6/10-second hook derivatives
- 30-second narrative derivative when useful
- 16:9 and 1:1 should be recomposed from adaptable source assets rather than naive crop where practical.
