# llama-sonify Session Handoff

## What We Built

**llama-sonify** — A tool that streams neural activation projections via OSC during LLM inference for real-time sonification.

### Files Created

```
tools/llama-sonify/
├── CMakeLists.txt          # Build config
├── osc.h                   # Header-only OSC UDP sender
├── sonify.cpp              # Main implementation
├── test_osc_receiver.py    # Python OSC receiver for testing
├── download_cvs.py         # Script to download control vectors from HF
├── llama_sonify_receiver.scd  # SuperCollider patch for audio synthesis
├── .venv/                  # Python venv with huggingface_hub
├── cvs/                    # Downloaded control vectors (6 files)
│   ├── qwen-2.5:7b-empathy_vs_sociopathy__empathy.gguf
│   ├── qwen-2.5:7b-empathy_vs_sociopathy__sociopathy.gguf
│   ├── qwen-2.5:7b-optimism_vs_nihilism__optimism.gguf
│   ├── qwen-2.5:7b-optimism_vs_nihilism__nihilism.gguf
│   ├── qwen-2.5:7b-language__ornate.gguf
│   └── qwen-2.5:7b-language__simple.gguf
└── models/
    └── Qwen2.5-7B-Instruct-Q8_0.gguf  # ~8GB model
```

### Modified Files

- `tools/CMakeLists.txt` — Added `add_subdirectory(llama-sonify)`

## Current State

### What Works
- ✅ llama-sonify builds and runs
- ✅ Generates tokens continuously (use `-n 256` for more tokens)
- ✅ Sends OSC messages to port 9000
- ✅ Python test receiver confirms OSC messages arrive correctly
- ✅ SuperCollider receives OSC (verified with `OSCFunc.trace(true)`)
- ✅ SC outputs to Scarlett 4i4 USB audio interface

### Current Issue
- SuperCollider receives OSC but synth produces no sound
- Likely cause: Synth started before buses were created by OSC receiver
- Debug needed: Check `~cvBuses.keys` and `~smoothedValues` after running llama-sonify

## How to Run

### Terminal — llama-sonify
```bash
cd /Users/mgm/development/code/llama.cpp

./build/bin/llama-sonify \
  -m tools/llama-sonify/models/Qwen2.5-7B-Instruct-Q8_0.gguf \
  -p "Once upon a time" \
  -n 256 \
  --cv tools/llama-sonify/cvs/qwen-2.5:7b-empathy_vs_sociopathy__empathy.gguf \
  --cv tools/llama-sonify/cvs/qwen-2.5:7b-optimism_vs_nihilism__optimism.gguf \
  --cv tools/llama-sonify/cvs/qwen-2.5:7b-language__ornate.gguf \
  --osc-port 9000
```

### SuperCollider — Setup sequence
1. Set audio device and boot:
```supercollider
s.options.outDevice = "Scarlett 4i4 USB";
s.boot;
```

2. Run Step 2 (OSC setup block) from `llama_sonify_receiver.scd`

3. Run llama-sonify once to create buses

4. Verify buses exist:
```supercollider
~cvBuses.keys;  // Should show CV names
```

5. Run Step 3 (synth block) from `llama_sonify_receiver.scd`

6. Run llama-sonify again — should hear audio changes

### Debug Commands (SuperCollider)
```supercollider
// See all incoming OSC
OSCFunc.trace(true);
// ... run llama-sonify ...
OSCFunc.trace(false);

// Check state
~cvBuses.keys;
~cvBuses.values;
~smoothedValues;

// Manual test synth
~testBus = Bus.control(s, 1);
~testBus.set(0.5);
{ SinOsc.ar(In.kr(~testBus).linexp(0, 1, 200, 800)) * 0.2 }.play;
~testBus.set(0.8);  // Should change pitch
```

## Architecture

```
llama-sonify (C++)
    │
    │ UDP/OSC port 9000
    │ /cv/{cv_name}/{layer} float
    │ /generating int
    ▼
SuperCollider
    │
    │ OSCdef receives messages
    │ Aggregates 28 layers → 1 mean value per CV
    │ Writes to control buses
    ▼
SynthDef(\llamaDrone)
    │
    │ Reads control buses
    │ Maps to 3-voice synthesis
    ▼
Audio out (Scarlett 4i4 USB)
```

## Next Steps

1. Debug why synth isn't producing sound:
   - Confirm buses are created after OSC messages arrive
   - Confirm synth is connected to correct buses
   - Try the simple test synth (Step 4) first

2. Once sound works, iterate on synthesis mappings

3. Future enhancements:
   - Interactive/continuous mode
   - More CV axes
   - Different synthesis approaches
   - Visualization

## Key Reference Files

- [sonify.cpp](tools/llama-sonify/sonify.cpp) — Main C++ implementation
- [osc.h](tools/llama-sonify/osc.h) — OSC sender
- [llama_sonify_receiver.scd](tools/llama-sonify/llama_sonify_receiver.scd) — SC patch
- [DESIGN.md](tools/llama-sonify/DESIGN.md) — Original design document
