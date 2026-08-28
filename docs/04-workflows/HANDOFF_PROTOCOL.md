# External Handoff Protocol

Pattern:

**Request Package → External Execution → Return Directory → Validation → Resume**

## Emulator Capture Handoff Package
Must include:
- campaign/product IDs
- APK path/copy + checksum
- emulator/API/device specification
- orientation/resolution
- fictional demo data
- exact navigation steps
- screenshots required
- recordings required
- durations
- filenames
- output tree
- validation checklist
- scripts where possible
- standalone Claude Code/Codex execution prompt

## Generation Handoff Package
Must include:
- scene IDs
- exact prompts
- negative constraints
- continuity references
- input/reference images
- first/last frame where relevant
- ratio/duration
- preferred model/mode
- generation count/retry allowance
- expected filenames
- acceptance criteria
- return directory

Returned assets are validated before state resumes.
