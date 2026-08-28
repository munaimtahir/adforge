# Campaign State Machine

Canonical states:

CREATED  
PRODUCT_TRUTH_VALIDATION  
STRATEGY  
SCRIPT  
STORYBOARD  
ASSET_PLAN  
ASSET_GENERATION  
APP_CAPTURE  
AUDIO_PRODUCTION  
EDIT_PLAN  
DRAFT_RENDER  
QC  
REPAIR  
FINAL_RENDER  
EXPORT  
COMPLETE

Exceptional states:
PAUSED  
BLOCKED  
FAILED  
WAITING_FOR_EXTERNAL_ASSET  
WAITING_FOR_USER

## Requirements
- Every transition is persisted.
- Every transition is ledgered.
- A restart resumes from the last durable state.
- Completed artifacts are not regenerated unless invalid, explicitly superseded, or required by repair.
- At most one campaign may hold the ACTIVE production lease in v1.
