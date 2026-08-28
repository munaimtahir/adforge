# Rendering Standard

## Canonical v1 renderer
FFmpeg / ffprobe.

## Edit specification
The AI Edit Director produces a machine-readable timeline. The renderer executes it.

Timeline should support:
- source asset
- in/out
- placement
- crop/scale
- transition
- overlays
- captions/text
- logo
- audio track
- gain/ducking
- SFX
- CTA/end card
- output profile

## Extensibility
Renderer interface must allow future Remotion/Blender/custom adapters.

## Deterministic rule
Never ask a generative model to redraw existing logos or critical text when deterministic compositing can preserve exact assets.
