# Provider Adapter Contract

Providers are replaceable.

## Core adapters
- ReasoningProvider
- CodingProvider
- VideoGenerationProvider
- ImageGenerationProvider
- VoiceProvider
- MusicProvider
- Renderer

## Initial implementations
- ClaudeCodeProvider
- CodexCLIProvider
- FlowBrowserVideoProvider
- FFmpegRenderer

## Provider router inputs
- task type
- required capability
- current health
- usage/limits
- previous quality
- cost/credit budget
- context size
- explicit campaign constraints

## Rule
Claude and Codex are not permanently bound to creative/technical roles. Route to the best available worker.

## Failure
Provider call: initial attempt + two retries. If still failing, persist state, record diagnostics, and escalate or use a supported handoff path. Do not silently switch to a paid API unless explicitly enabled.
