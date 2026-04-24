Diagnose why whisperme crashed or misbehaved.

## Steps

1. Read the last 200 lines of `logs/whisperme.log` (Python logging — includes RealtimeSTT errors, tracebacks)
2. Read the last 200 lines of `logs/whisperme_stdout.log` (print output — includes [recorder], [hotkey], [whisperme] messages)
3. Look for tracebacks, ERROR/CRITICAL entries, and repeated error patterns
4. Cross-reference with the source code in `src/whisperme/` to identify the root cause
5. If the user provided additional context, factor that in

## User context (optional)

$ARGUMENTS

## Output

- State what went wrong (the error and where it originated)
- Explain why it happened
- Suggest or apply a fix
